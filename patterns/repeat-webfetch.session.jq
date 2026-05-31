# Detects: Same URL WebFetched 2+ times within one session
#
# Why it matters: repeated WebFetches of the same URL waste tokens and time
# (WebFetch responses are summarized, so each fetch consumes a new ~LLM pass).
# Candidates for: context7 (for library docs), local clone (for repeatedly-
# referenced source repos), or noting the relevant content the first time.

[ .[]
  | select(.message?.content)
  | .message.content[]?
  | select(.type == "tool_use" and .name == "WebFetch")
  | .input.url
]
| group_by(.)
| map(select(length >= 2) | {url: .[0], count: length})
| .[]
| . as $hit
| {
    pattern: "repeat-webfetch",
    url: $hit.url,
    count: $hit.count
  }
