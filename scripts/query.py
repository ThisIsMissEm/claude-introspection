#!/usr/bin/env python3
"""claude-introspection query — ad-hoc introspection queries.

Usage:
    query.py --list
    query.py <query-name> [--project SLUG] [--since YYYY-MM-DD]

Each query is a small function below. Add new queries by defining a function
and adding it to QUERIES. The dispatcher is `argparse subparsers`.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from _common import (
    find_sessions,
    iter_tool_results,
    iter_tool_uses,
    tool_result_text,
)

# --- Helpers -------------------------------------------------------------

_ENV_PREFIX_RE = re.compile(r"^\s*([A-Z_][A-Z0-9_]*=\S+\s+)+")
_FIRST_WORD_RE = re.compile(r"^\s*(\S+)")


def bash_first_verb(cmd: str) -> str:
    """First non-env-var word of a bash command, basename only."""
    cmd = _ENV_PREFIX_RE.sub("", cmd)
    m = _FIRST_WORD_RE.match(cmd)
    if not m:
        return ""
    tok = m.group(1)
    # Strip path prefix
    if "/" in tok:
        tok = tok.rsplit("/", 1)[-1]
    return tok


# --- Queries -------------------------------------------------------------

def q_tool_counts(sessions: list[Path]) -> None:
    """Tool invocation frequency across all sessions."""
    counts: Counter = Counter()
    for s in sessions:
        for _evt, c in iter_tool_uses(s):
            counts[c.get("name") or "?"] += 1
    for name, n in counts.most_common():
        print(f"{n:>6}  {name}")


def q_bash_verbs(sessions: list[Path]) -> None:
    """Top first-word commands invoked via Bash."""
    counts: Counter = Counter()
    for s in sessions:
        for _evt, c in iter_tool_uses(s, name="Bash"):
            cmd = (c.get("input") or {}).get("command") or ""
            verb = bash_first_verb(cmd)
            if verb:
                counts[verb] += 1
    for verb, n in counts.most_common(30):
        print(f"{n:>6}  {verb}")


def q_session_cost_proxy(sessions: list[Path]) -> None:
    """Per-session event counts (proxy for cost — codeburn has real €)."""
    rows = []
    for s in sessions:
        with s.open() as f:
            events = sum(1 for _ in f)
        tools = sum(1 for _ in iter_tool_uses(s))
        rows.append((events, tools, s))
    rows.sort(key=lambda r: -r[0])
    for events, tools, s in rows:
        project = s.parent.name
        session_short = s.stem[:8]
        print(f"{events:>6} events  {tools:>4} tools  {session_short}  {project}")


def q_agent_targets(sessions: list[Path]) -> None:
    """What subagents (Agent calls) were dispatched to do."""
    counts: Counter = Counter()
    for s in sessions:
        for _evt, c in iter_tool_uses(s, name="Agent"):
            inp = c.get("input") or {}
            stype = inp.get("subagent_type") or "claude"
            desc = inp.get("description") or "(no description)"
            counts[f"{stype} :: {desc}"] += 1
    for label, n in counts.most_common():
        print(f"{n:>4}  {label}")


def q_largest_bash_outputs(sessions: list[Path]) -> None:
    """Top 10 bash commands by tool_result size — worst context bloat."""
    rows = []
    for s in sessions:
        cmd_by_id = {}
        for _evt, c in iter_tool_uses(s, name="Bash"):
            cmd_by_id[c.get("id")] = (c.get("input") or {}).get("command") or ""
        for _evt, c in iter_tool_results(s):
            tid = c.get("tool_use_id")
            cmd = cmd_by_id.get(tid)
            if cmd is None:
                continue
            size = len(tool_result_text(c))
            if size > 0:
                rows.append((size, cmd))
    rows.sort(key=lambda r: -r[0])
    for size, cmd in rows[:10]:
        display = cmd.replace("\n", " ⏎ ")[:140]
        print(f"{size:>8} bytes  {display}")


def q_busiest_files(sessions: list[Path]) -> None:
    """Files Read/Edited/Written most across sessions, with which tools used."""
    counts: Counter = Counter()
    tools_by_file: defaultdict = defaultdict(set)
    for s in sessions:
        for _evt, c in iter_tool_uses(s):
            name = c.get("name")
            if name not in ("Read", "Edit", "Write"):
                continue
            fp = (c.get("input") or {}).get("file_path") or "?"
            counts[fp] += 1
            tools_by_file[fp].add(name)
    for fp, n in counts.most_common(20):
        tools = " ".join(sorted(tools_by_file[fp]))
        print(f"{n:>5}  {tools:<15} {fp}")


# --- Dispatcher ----------------------------------------------------------

QUERIES = {
    "tool-counts": (
        q_tool_counts,
        "Tool invocation frequency. Most basic overview.",
    ),
    "bash-verbs": (
        q_bash_verbs,
        "Top shell commands invoked via Bash (first word).",
    ),
    "session-cost-proxy": (
        q_session_cost_proxy,
        "Per-session event counts (proxy for cost — actual € from codeburn).",
    ),
    "agent-targets": (
        q_agent_targets,
        "What subagents (Agent calls) were dispatched to do.",
    ),
    "largest-bash-outputs": (
        q_largest_bash_outputs,
        "Top 10 bash commands by tool_result size — worst context bloat.",
    ),
    "busiest-files": (
        q_busiest_files,
        "Files Read/Edited/Written most across sessions.",
    ),
}


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("query", nargs="?", help="Query name (or --list)")
    parser.add_argument(
        "--list", action="store_true", help="Show available queries"
    )
    parser.add_argument("--project", help="Restrict to one project slug")
    parser.add_argument(
        "--since",
        help="ISO date — only sessions whose first event is >= this",
    )
    args = parser.parse_args()

    if args.list or args.query is None:
        print("Available queries:\n")
        width = max(len(name) for name in QUERIES)
        for name, (_fn, desc) in QUERIES.items():
            print(f"  {name:<{width}}  {desc}")
        print()
        print("Filters work on any query:")
        print(
            "  --project <slug>       Project dir name under ~/.claude/projects"
        )
        print(
            "  --since <ISO-date>     Only sessions whose first event is after"
        )
        print()
        print("Example:")
        print(f"  {Path(__file__).name} tool-counts --since 2026-05-01")
        return 0

    if args.query not in QUERIES:
        print(f"Unknown query: {args.query}", file=sys.stderr)
        print("Run with --list to see available queries.", file=sys.stderr)
        return 1

    sessions = find_sessions(project_filter=args.project, since=args.since)
    if not sessions:
        print("No sessions match the given filters.", file=sys.stderr)
        return 0

    fn, _desc = QUERIES[args.query]
    fn(sessions)
    return 0


if __name__ == "__main__":
    sys.exit(main())
