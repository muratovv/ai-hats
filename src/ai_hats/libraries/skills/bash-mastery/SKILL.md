---
name: bash-mastery
description: Shell scripting, Makefile conventions, and modern CLI tooling
---
# Bash Mastery

Shell scripting, Makefile conventions, and modern CLI tooling.

## When to Use
- Writing or reviewing shell scripts
- Creating or modifying Makefiles
- Choosing CLI tools for automation tasks

## Bash Scripts
- Shebang: `#!/usr/bin/env bash`
- Always use `set -euo pipefail`.
- Implement `-h/--help` with usage description.
- Prefer env vars over positional arguments; define/default at the top.
- Idempotency: multiple runs must produce the same state.
- Add brief comments to `if/elif/else` branches.

## Makefiles
- Mandatory `help` target as `.DEFAULT_GOAL`:
  ```
  @grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-18s\033[0m %s\n", $$1, $$2}'
  ```
- Standard targets: `all`, `build`, `test`, `lint`, `clean`, `deps`, `help`.
- Use `.PHONY` for all non-file targets.
- Use variables for tools: `GO ?= go`, `DOCKER ?= docker`.
- ANSI colors for status messages (green=success, yellow=warning, red=error).

## Modern CLI Tooling
Prefer modern tools when available:

| Task | Use | Instead of |
|------|-----|-----------|
| Search text | `rg` | `grep` |
| Find files | `fd` | `find` |
| View files | `bat` | `cat` |
| List dirs | `eza` | `ls` |
| JSON | `jq` | manual parsing |
| YAML | `yq` | manual parsing |

Fall back to traditional tools if modern ones are unavailable, but note the preference.

## Anti-Patterns
- Scripts without `set -euo pipefail` — silent failures hide bugs
- Makefiles without `help` target — users can't discover available commands
- Hardcoded tool paths — use env vars and `?=` defaults
