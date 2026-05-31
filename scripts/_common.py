"""Shared helpers for the claude-introspection skill scripts.

Imported by audit.py, query.py, project-diag.py. Sits in the same directory
so plain `from _common import ...` works without sys.path tricks.

Python 3.9-compatible: avoids match/case, walrus in odd places, and PEP-604
union syntax in critical paths. Uses only stdlib.
"""

from __future__ import annotations

import json
import os
import pwd
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

# --- Paths ---------------------------------------------------------------

SKILL_DIR = Path(__file__).resolve().parent.parent
PATTERNS_DIR = SKILL_DIR / "patterns"
PROJECTS_DIR = Path(
    os.environ.get("CLAUDE_PROJECTS_DIR", str(Path.home() / ".claude" / "projects"))
)
ME = pwd.getpwuid(os.getuid()).pw_name


# --- Trust check ---------------------------------------------------------

def check_safety(path: Path) -> None:
    """Refuse to proceed if `path` is not owned by us or is world-writable.

    Same defense as the bash version: catches an attacker with limited
    write access to home dir trying to plant executable detectors.
    """
    st = path.stat()
    owner = pwd.getpwuid(st.st_uid).pw_name
    if owner != ME:
        sys.exit(f"REFUSING: {path} is owned by {owner!r}, expected {ME!r}")
    if st.st_mode & 0o002:
        perms = oct(st.st_mode & 0o777)[2:]
        sys.exit(f"REFUSING: {path} is world-writable (perms={perms})")


def assert_patterns_dir_safe() -> None:
    """Verify patterns/ is safe to load detectors from.

    - Directory itself: owned + not world-writable
    - Every file inside: owned + not world-writable
    - No .sh files allowed (only .jq, which is sandboxed)
    """
    check_safety(PATTERNS_DIR)
    sh_files = sorted(PATTERNS_DIR.glob("*.sh"))
    if sh_files:
        msg = [
            f"REFUSING: unexpected .sh file(s) in {PATTERNS_DIR}",
            "  detectors must be .jq only:",
            *[f"    {f}" for f in sh_files],
            "  If intentional, inline the logic into audit.py instead.",
        ]
        sys.exit("\n".join(msg))
    for jq_file in PATTERNS_DIR.glob("*.jq"):
        check_safety(jq_file)


# --- Session discovery ---------------------------------------------------

def find_sessions(
    project_filter: str | None = None,
    since: str | None = None,
) -> list[Path]:
    """List parent-session transcript paths.

    Excludes subagent transcripts (under */subagents/).
    If `project_filter` is given, restricts to that project directory.
    If `since` (ISO date string) is given, includes only sessions whose
    first event timestamp is >= since.
    """
    root = PROJECTS_DIR / project_filter if project_filter else PROJECTS_DIR
    if not root.exists():
        return []
    sessions: list[Path] = []
    for path in root.rglob("*.jsonl"):
        if "subagents" in path.parts:
            continue
        if not path.is_file():
            continue
        if since:
            first_ts = _first_timestamp(path)
            if first_ts is None or first_ts < since:
                continue
        sessions.append(path)
    return sessions


def _first_timestamp(path: Path) -> str | None:
    """Return the first event's ISO timestamp, or None if absent/malformed."""
    try:
        with path.open() as f:
            for line in f:
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = evt.get("timestamp")
                if ts:
                    return ts
    except OSError:
        return None
    return None


# --- Event iteration -----------------------------------------------------

def iter_events(session: Path) -> Iterator[dict]:
    """Yield each parsed JSON event from a session transcript."""
    with session.open() as f:
        for line in f:
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _content_blocks(evt: dict) -> Iterator[dict]:
    """Yield dict content blocks from an event, tolerating malformed shapes.

    The message.content field is sometimes a list of dicts (assistant turns,
    tool turns), sometimes a plain string (user text turns), sometimes
    absent. Only yield blocks that are actual dicts so callers can .get().
    """
    msg = evt.get("message")
    if not isinstance(msg, dict):
        return
    content = msg.get("content")
    if not isinstance(content, list):
        return
    for block in content:
        if isinstance(block, dict):
            yield block


def iter_tool_uses(
    session: Path, name: str | None = None
) -> Iterator[tuple[dict, dict]]:
    """Yield (event, tool_use_content) pairs from a session.

    If `name` is given, restricts to tool_uses of that name.
    """
    for evt in iter_events(session):
        for content in _content_blocks(evt):
            if content.get("type") != "tool_use":
                continue
            if name and content.get("name") != name:
                continue
            yield evt, content


def iter_tool_results(session: Path) -> Iterator[tuple[dict, dict]]:
    """Yield (event, tool_result_content) pairs from a session."""
    for evt in iter_events(session):
        for content in _content_blocks(evt):
            if content.get("type") != "tool_result":
                continue
            yield evt, content


def tool_result_text(content: dict) -> str:
    """Extract the text from a tool_result content block.

    The .content field may be a plain string OR a list of {type, text} dicts.
    Normalizes both shapes.
    """
    body = content.get("content")
    if isinstance(body, str):
        return body
    if isinstance(body, list):
        parts: list[str] = []
        for item in body:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text") or "")
        return "".join(parts)
    return ""


# --- jq invocation -------------------------------------------------------

def run_jq(filter_path: Path, session: Path, slurp: bool = False) -> list[dict]:
    """Run a .jq detector against a session, return parsed match objects.

    `slurp=True` for .session.jq detectors that operate on an array of all
    events in the session.

    Returns [] on error rather than raising — matches the bash audit's
    "tolerate detector failures" behavior. Errors are silent so a single
    broken detector doesn't crash the whole audit.
    """
    flags = ["-c"]
    if slurp:
        flags.append("-s")
    flags.extend(["-f", str(filter_path), str(session)])
    try:
        proc = subprocess.run(
            ["jq", *flags],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []
    if proc.returncode != 0:
        return []
    matches: list[dict] = []
    for line in proc.stdout.splitlines():
        try:
            matches.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return matches


# --- Detector descriptor -------------------------------------------------

def list_detectors() -> list[tuple[str, Path, bool]]:
    """Enumerate the jq detectors in patterns/.

    Returns list of (name, path, is_slurp). is_slurp is True for .session.jq
    files (slurped jq input — array of events).
    """
    out: list[tuple[str, Path, bool]] = []
    for p in sorted(PATTERNS_DIR.glob("*.jq")):
        name = p.stem
        is_slurp = p.name.endswith(".session.jq")
        # .session.jq gives stem of "foo.session" — strip
        if is_slurp:
            name = name[: -len(".session")]
        out.append((name, p, is_slurp))
    return out


# --- Output formatting ---------------------------------------------------

def keyfield(match: dict) -> str:
    """Pick a representative key field from a detector match for grouping.

    Falls through .cmd → .file_path → .url → .prompt → .id → .name.
    """
    for k in ("cmd", "file_path", "url", "prompt", "id", "name"):
        v = match.get(k)
        if v is not None:
            return str(v)
    return "(no key)"


def format_section_header(title: str, count: int, sessions: int) -> str:
    return f"## {title}  —  {count} matches across {sessions} sessions"


def format_top_samples(
    matches: list[dict], limit: int = 5, key_width: int = 160
) -> list[str]:
    """Group matches by keyfield, return top N as '  Nx  key' lines."""
    from collections import Counter

    counts = Counter(keyfield(m) for m in matches)
    lines = []
    for key, n in counts.most_common(limit):
        display = key.replace("\n", " ⏎ ")[:key_width]
        lines.append(f"  {n}×  {display}")
    return lines
