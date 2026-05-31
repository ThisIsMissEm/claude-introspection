#!/usr/bin/env python3
"""claude-introspection audit — anti-pattern scan across all transcripts.

Usage:
    audit.py [--project SLUG]

Loads jq detectors from patterns/, runs each against every parent session
transcript under ~/.claude/projects/, aggregates and prints a ranked report.

Trust model: see SKILL.md "Trust boundary" section. Only .jq detectors are
loaded (sandboxed by the jq language). Cross-session detectors live inline
in this file. Refuses to run if patterns/ or any file in it is not owned
by you or is world-writable.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from _common import (
    PATTERNS_DIR,
    assert_patterns_dir_safe,
    find_sessions,
    format_section_header,
    format_top_samples,
    iter_events,
    iter_tool_uses,
    list_detectors,
    run_jq,
    tool_result_text,
)

# --- Inline cross-session detectors --------------------------------------

def detect_skills_never_invoked(sessions: list[Path]) -> list[str]:
    """Skills present in ~/.claude/skills/ but never invoked via Skill tool."""
    skills_dir = Path.home() / ".claude" / "skills"
    if not skills_dir.is_dir():
        return []

    registry = {p.parent.name for p in skills_dir.glob("*/SKILL.md")}
    invoked = {
        (c.get("input") or {}).get("skill")
        for s in sessions
        for _evt, c in iter_tool_uses(s, name="Skill")
    }
    invoked.discard(None)
    return sorted(registry - invoked)


# Tokens that count as "distinctive" inside a CLAUDE.md section.
# Match: `code spans`, **bold**, or identifier-shaped words ≥6 chars.
_DISTINCTIVE_RE = re.compile(
    r"`[^`]+`|\*\*[^*]+\*\*|[A-Za-z_][A-Za-z0-9_-]{5,}"
)


def detect_claude_md_stale(sessions: list[Path]) -> list[str]:
    """CLAUDE.md ## sections whose distinctive tokens never appear in any transcript.

    Noisy — sections may shape behavior implicitly without verbatim citation.
    Returns section titles to REVIEW, not delete.
    """
    claude_md = Path.home() / ".claude" / "CLAUDE.md"
    if not claude_md.is_file():
        return []

    # Parse ## sections → distinctive tokens
    sections: dict[str, set[str]] = {}
    current: str = ""
    with claude_md.open() as f:
        for line in f:
            if line.startswith("## "):
                current = line[3:].rstrip()
                sections[current] = set()
                continue
            if not current:
                continue
            for tok in _DISTINCTIVE_RE.findall(line):
                tok = tok.strip("`*")
                if len(tok) > 5:
                    sections[current].add(tok)

    # Build the haystack: all transcript text content (text turns, tool_use
    # inputs, tool_result outputs).
    def _block_text(block: dict) -> str:
        ctype = block.get("type")
        if ctype == "text":
            return block.get("text") or ""
        if ctype == "tool_use":
            return json.dumps(block.get("input") or {})
        if ctype == "tool_result":
            return tool_result_text(block)
        return ""

    haystack = "\n".join(
        _block_text(block)
        for s in sessions
        for evt in iter_events(s)
        for block in (evt.get("message") or {}).get("content") or []
        if isinstance(block, dict)
    )

    stale: list[str] = []
    for section, tokens in sections.items():
        if not tokens:
            continue  # section had no distinctive tokens to check
        if not any(tok in haystack for tok in tokens):
            stale.append(section)
    return stale


# --- Main ----------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--project", help="Restrict to one project slug")
    args = parser.parse_args()

    assert_patterns_dir_safe()

    sessions = find_sessions(project_filter=args.project)
    n = len(sessions)

    print("=== Claude Introspection Audit ===")
    print(f"Sessions scanned: {n}")
    if args.project:
        print(f"Project filter:   {args.project}")
    print(f"Patterns dir:     {PATTERNS_DIR}")
    print()

    if n == 0:
        print("No transcripts found. Exiting.")
        return 0

    # Run jq detectors. Stamp the session path on each match so we can count
    # affected sessions even when the detector itself doesn't emit `session`.
    for name, path, slurp in list_detectors():
        all_matches: list[dict] = []
        affected_sessions: set[str] = set()
        for session in sessions:
            matches = run_jq(path, session, slurp=slurp)
            if matches:
                affected_sessions.add(str(session))
                all_matches.extend(matches)
        if not all_matches:
            continue
        print(format_section_header(name, len(all_matches), len(affected_sessions)))
        for line in format_top_samples(all_matches):
            print(line)
        print()

    # Inline: skills-never-invoked
    print("## skills-never-invoked (cross-session)")
    never = detect_skills_never_invoked(sessions)
    if not never:
        print("  All registered skills have been invoked at least once.")
    else:
        print(f"  {len(never)} skills in registry never invoked via Skill tool:")
        for s in never:
            print(f"    - {s}")
    print()

    # Inline: claude-md-stale
    print("## claude-md-stale (cross-session)")
    stale = detect_claude_md_stale(sessions)
    if not stale:
        print("  All CLAUDE.md sections referenced in transcripts.")
    else:
        for s in stale:
            print(f"  - {s}")
        print()
        print(f"  ({len(stale)} stale candidates — review manually before trimming)")
    print()

    print("=== End of audit ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
