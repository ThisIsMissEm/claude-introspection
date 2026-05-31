# Detects: Agent (subagent) tool calls with very short prompts
#
# Why it matters: subagent dispatch is the most expensive per-call tool in
# Claude Code (€0.79/call on Emelia's 6-month average). When the dispatched
# task is small enough to fit in the main thread, the spawn cost outweighs
# the context-protection benefit.
#
# Heuristic: prompt length < 400 chars suggests a small task. False positives
# are dense terse prompts that genuinely need isolated context; review samples
# manually.

. as $evt |
select($evt.message?.content) |
$evt.message.content[]? |
select(.type == "tool_use" and .name == "Agent") |
.input.prompt as $p |
($p | length) as $plen |
select($plen < 400) |
{
  pattern: "tiny-agent-dispatch",
  prompt_len: $plen,
  prompt: ($p | gsub("\n"; " ⏎ ") | .[0:200]),
  subagent_type: (.input.subagent_type // "claude"),
  session: ($evt.sessionId // "?"),
  ts: ($evt.timestamp // "?")
}
