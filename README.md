# claude-introspection

Audits your Claude Code transcripts (`~/.claude/projects/*/[uuid].jsonl`) to surface usage patterns worth changing.

**Why it exists**: self-reports about Claude's behavior are unreliable. The transcripts are ground truth. This skill reads them.

## Prerequisites

- Python 3.9+
- `jq` on `$PATH`

## Three scripts

### `audit.py` — automated anti-pattern scan

```bash
~/.claude/skills/claude-introspection/scripts/audit.py
```

Runs all `.jq` detectors in `patterns/` against every session transcript, ranks hits by frequency, and proposes concrete remediations (CLAUDE.md additions, hook changes, settings tweaks). Output is text for review — nothing is applied automatically.

### `query.py` — ad-hoc introspection

```bash
scripts/query.py --list                        # list available queries
scripts/query.py tool-counts                   # all tools, by frequency
scripts/query.py bash-verbs                    # top shell commands (first word)
scripts/query.py largest-bash-outputs          # worst context-bloat offenders
scripts/query.py busiest-files                 # most-read/edited files
scripts/query.py session-cost-proxy            # event counts per session
scripts/query.py agent-targets                 # what subagents were dispatched to do

# Filters work on any query:
scripts/query.py tool-counts --since 2026-05-01
scripts/query.py bash-verbs --project my-project-slug
```

### `project-diag.py` — per-project cost decomposition

```bash
scripts/project-diag.py <project-slug>
```

Breaks down a single project's sessions: avg context loaded, avg turns, tool mix (Bash% / Read% / subagent%), avg tool output size. Useful for understanding why one project costs more per session than another.

## Detectors (`patterns/`)

One `.jq` file per anti-pattern. Per-event detectors are plain `.jq`; per-session detectors (which see all events in a session at once) are `.session.jq`.

| File | What it catches |
|------|-----------------|
| `shell-truncate.jq` | `bash … \| head -N` / `\| tail -N` — use `Read` with offset/limit instead |
| `tiny-agent-dispatch.jq` | Agent calls with very short prompts that could have stayed in the main thread |
| `long-bash.session.jq` | Bash commands >30 s wall-time — candidates for `run_in_background` |
| `repeat-reads.session.jq` | Same file Read 3+ times in one session — poor context retention |
| `repeat-webfetch.session.jq` | Same URL fetched 2+ times in one session — context7 or local clone candidate |
| `sequential-edits.session.jq` | 3+ consecutive Edits to the same file — consider batching |

Cross-session detectors (`skills-never-invoked`, `claude-md-stale`) are inlined in `audit.py`.

### Adding a detector

Drop a new `.jq` or `.session.jq` file in `patterns/`. Each is independently runnable:

```bash
jq -c -f patterns/shell-truncate.jq < ~/.claude/projects/*/SESSION.jsonl
```

For logic that needs more than jq (file I/O, shell commands), add it inline to `audit.py` instead of putting a `.sh` in `patterns/`.

## Security model

`audit.py` refuses to run if any of these are true:

- A `.sh` file exists in `patterns/` (blocks "drop a shell script and it executes" attacks)
- Any file in `patterns/` is not owned by you
- Any file in `patterns/` is world-writable

jq detectors are sandboxed: they can read JSON, transform it, and output JSON. They cannot shell out, write files, or make network calls. The worst a malicious `.jq` could do is produce misleading output.

## What this skill does NOT do

- Apply changes — all output is text for your review
- Live monitoring — runs on-demand against existing transcripts, not continuously
- Per-message cost tracking — use `codeburn` for that

## Pairing with `codeburn`

`codeburn report --format json -p all` gives spend and rate-limit data from Anthropic API telemetry. This skill reads transcript content. Use both: codeburn for *where the money went*, introspection for *what Claude was actually doing when it spent it*.
