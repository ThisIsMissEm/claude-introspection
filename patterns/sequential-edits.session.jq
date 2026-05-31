# Detects: 3+ consecutive Edit calls to the same file
#
# Why it matters: each Edit costs context (the old_string + new_string come
# back in the tool_result). Three sequential Edits to the same file often
# could be one Write or one larger Edit with more context per operation.
# Sequences broken by other tool calls are not flagged (true interleaving
# is fine).

[ .[]
  | select(.message?.content)
  | .message.content[]?
  | select(.type == "tool_use" and .name == "Edit")
  | .input.file_path
]
# Detect runs of 3+ identical consecutive file paths
| . as $paths
| [ range(0; length - 2) as $i
    | select($paths[$i] == $paths[$i+1] and $paths[$i+1] == $paths[$i+2])
    | $paths[$i]
  ]
| unique
| .[]
| . as $f
| {
    pattern: "sequential-edits",
    file_path: $f
  }
