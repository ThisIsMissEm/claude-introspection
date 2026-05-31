# Detects: Same file Read 3+ times within one session
#
# Why it matters: repeated Reads of the same file indicate poor context
# retention — the file's content should still be in the conversation but
# isn't being used. Often a sign that the model should have used a single
# Read with offset/limit, or noted the relevant content the first time.
#
# Slurped: aggregates across all events in one session.

[ .[]
  | select(.message?.content)
  | .message.content[]?
  | select(.type == "tool_use" and .name == "Read")
  | .input.file_path
]
| group_by(.)
| map(select(length >= 3) | {file: .[0], count: length})
| .[]
| . as $hit
| {
    pattern: "repeat-reads",
    file_path: $hit.file,
    count: $hit.count
  }
