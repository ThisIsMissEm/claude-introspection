# Detects: Bash invocations whose wall-time exceeded 30 seconds
#
# Wall-time is computed as (tool_result.timestamp - tool_use.timestamp).
# Why it matters: long-blocking Bash commands are candidates for
# run_in_background, freeing the conversation to continue working while the
# command runs. Common culprits: test suites, builds, downloads, deploys.
#
# Slurped: needs to pair tool_use with matching tool_result by id.

(map(select(.message?.content)) | map({event: ., content: .message.content})) as $events
| ($events
    | map(.content[]? as $c | select($c.type == "tool_use" and $c.name == "Bash") | {id: $c.id, cmd: $c.input.command, ts: .event.timestamp, session: .event.sessionId})
  ) as $bash_uses
| ($events
    | map(.content[]? as $c | select($c.type == "tool_result") | {id: $c.tool_use_id, ts: .event.timestamp})
    | map({(.id): .ts}) | add // {}
  ) as $result_ts
| $bash_uses[]
| . as $u
| ($result_ts[$u.id] // null) as $end
| select($end != null)
| (($end | sub("\\..*"; "Z") | fromdateiso8601) - ($u.ts | sub("\\..*"; "Z") | fromdateiso8601)) as $dur
| select($dur > 30)
| {
    pattern: "long-bash",
    duration_s: $dur,
    cmd: ($u.cmd | gsub("\n"; " ⏎ ") | .[0:200]),
    session: $u.session,
    ts: $u.ts
  }
