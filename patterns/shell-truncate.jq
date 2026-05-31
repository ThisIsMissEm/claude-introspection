# Detects: shell commands containing "| head -N" or "| tail -N"
#
# Why it matters: most upstream commands (rg, find, git log, jq -s) have
# native limit flags; or you can switch to the built-in Grep tool with its
# head_limit parameter. Shelling out then truncating wastes context on the
# untruncated bytes, even though "head -N" caps what's displayed.
#
# False positives: pipe-chained transforms where head is genuinely an
# intermediate step (e.g. `cmd | head -1 | xargs ...`). Acceptable noise.

. as $evt |
select($evt.message?.content) |
$evt.message.content[]? |
select(.type == "tool_use" and .name == "Bash") |
.input.command as $cmd |
($cmd | test("\\|[[:space:]]*head[[:space:]]+-")) as $head |
($cmd | test("\\|[[:space:]]*tail[[:space:]]+-")) as $tail |
select($head or $tail) |
{
  pattern: "shell-truncate",
  subtype: (if $head then "head" else "tail" end),
  cmd: ($cmd | gsub("\n"; " ⏎ ") | .[0:200]),
  session: ($evt.sessionId // "?"),
  ts: ($evt.timestamp // "?")
}
