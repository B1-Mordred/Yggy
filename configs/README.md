# Configuration

Configuration here is declarative and non-secret. Task YAML may reference credential names, but must not include tokens, passwords, webhook URLs, private keys, cookies, or API keys.

Use `scripts/validate_configs.py` before enabling or deploying a task.

Topic digest tasks must use source IDs from `configs/sources/approved_sources.yaml`.
Generic `web_query` sources are disabled by policy for topic digests so the worker
uses explicit approved feeds rather than broad search-style prompts.
