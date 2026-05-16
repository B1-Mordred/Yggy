# Prompt Injection Defenses

External content is data, not instruction authority.

Untrusted sources include:

- RSS feeds
- webpages
- emails
- Discord messages
- logs
- external documents
- search results

Research/summarizer mode may read untrusted content and summarize it. Operator mode may draft or modify task configs through the automation API. A flow that consumes untrusted content must not approve, configure, or execute actions based on embedded instructions from that content.

The worker should preserve source links and summaries but must not execute instructions found inside fetched content.

## Ollama Summarization

The topic digest handler may pass bounded source excerpts to Ollama when `LLM_SUMMARIZER_ENABLED=true`. The summarizer prompt explicitly states that source text is untrusted data and forbids following embedded instructions, requesting credentials, approving actions, changing configuration, shell access, Docker access, file writes, purchases, or other side effects.

Summarizer limits are configured through environment variables:

- `LLM_SUMMARIZER_MODEL`
- `LLM_SUMMARIZER_TIMEOUT_SECONDS`
- `LLM_SUMMARIZER_MAX_ITEMS`
- `LLM_SUMMARIZER_MAX_CHARS_PER_ITEM`
- `LLM_SUMMARIZER_MAX_OUTPUT_CHARS`

If the model call fails or returns an empty response, the worker logs `summary_error` in the run result and sends the deterministic digest format instead.
