#!/usr/bin/env python3
"""claude-introspection project-diag — per-project decomposition.

Usage:
    project-diag.py                    # list projects with session counts
    project-diag.py <project-slug>     # full diag for one project

Project slug = directory name under ~/.claude/projects/.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter

from _common import (
    PROJECTS_DIR,
    find_sessions,
    iter_tool_results,
    iter_tool_uses,
    tool_result_text,
)
from query import bash_first_verb


def list_projects() -> int:
    if not PROJECTS_DIR.is_dir():
        print(f"No projects dir at {PROJECTS_DIR}", file=sys.stderr)
        return 1
    print("Available project slugs (sorted by session count):\n")
    rows = []
    for d in PROJECTS_DIR.iterdir():
        if not d.is_dir():
            continue
        n = len([
            p for p in d.rglob("*.jsonl")
            if p.is_file() and "subagents" not in p.parts
        ])
        rows.append((n, d.name))
    rows.sort(reverse=True)
    for n, slug in rows:
        print(f"  {n:>4} sessions  {slug}")
    print()
    print("Pick one and run: project-diag.py <slug>")
    return 0


def diag(slug: str) -> int:
    sessions = find_sessions(project_filter=slug)
    if not sessions:
        print(f"No transcripts found for project: {slug}", file=sys.stderr)
        return 1

    n = len(sessions)
    print(f"=== Project diagnostic: {slug} ===")
    print(f"Sessions: {n}\n")

    total_events = 0
    total_tools = 0
    total_output_bytes = 0
    tool_counts: Counter = Counter()
    bash_verbs: Counter = Counter()

    for s in sessions:
        with s.open() as f:
            total_events += sum(1 for _ in f)
        for _evt, c in iter_tool_uses(s):
            total_tools += 1
            tool_counts[c.get("name") or "?"] += 1
            if c.get("name") == "Bash":
                cmd = (c.get("input") or {}).get("command") or ""
                verb = bash_first_verb(cmd)
                if verb:
                    bash_verbs[verb] += 1
        for _evt, c in iter_tool_results(s):
            total_output_bytes += len(tool_result_text(c))

    avg_events = total_events // n
    avg_tools = total_tools // n
    avg_output_kb = total_output_bytes // n // 1024

    print("Per-session averages:")
    print(
        f"  {avg_events} events, {avg_tools} tool calls, "
        f"{avg_output_kb} KB tool output"
    )
    print()

    print("Tool mix:")
    for name, count in tool_counts.most_common():
        pct = (count * 100) / total_tools
        print(f"  {count:>6}  {pct:>5.1f}%  {name}")
    print()

    print("Top 10 bash verbs:")
    for verb, count in bash_verbs.most_common(10):
        print(f"  {count:>6}  {verb}")
    print()

    print("Hints:")
    if avg_output_kb > 100:
        print("  ⚠ Avg output > 100KB/session — tool-output bloat may dominate.")
        print("    Run: audit.py --project " + slug)
    print(
        "  • Combine with codeburn: codeburn report --format json -p all"
        f" | jq '.projects[] | select(.name == \"{slug}\")'"
    )
    print("  • Run anti-pattern audit for this project only:")
    print(f"      audit.py --project {slug}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("slug", nargs="?", help="Project slug; omit to list projects")
    args = parser.parse_args()
    if args.slug is None:
        return list_projects()
    return diag(args.slug)


if __name__ == "__main__":
    sys.exit(main())
