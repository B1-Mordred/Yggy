# Automation Worker

The worker polls the automation API for approved enabled tasks and runs bounded handlers. It does not execute arbitrary shell commands, use the Docker socket, or write arbitrary host files.

`DISCORD_DRY_RUN=true` keeps notification execution non-networked by default.
