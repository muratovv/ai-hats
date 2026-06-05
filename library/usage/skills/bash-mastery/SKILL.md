---
name: bash-mastery
description: Shell scripting, Makefile conventions, and modern CLI tooling. Use when writing or reviewing shell scripts, creating or modifying Makefiles, or choosing CLI tools for automation tasks.
---
# Bash Mastery

Shell scripting, Makefile conventions, and modern CLI tooling.

## When to Use
Scripting, Makefile, and CLI-tooling *craft* — not git plumbing (→ **git-mastery**)
and not the Bash-vs-dedicated-tool choice (→ **tool-call-hygiene**, which decides
whether to shell out at all versus use Read/Grep/Edit). Reach here once you've
decided a shell script is the right tool and need it to be correct and portable.

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
- Multi-line shell loops in Claude Code Bash calls — write as one-liners:
  ```bash
  # WRONG — Claude CLI parses each line as a separate command, generates garbage permissions:
  for f in *.yaml
  do
    echo "$f"
  done

  # CORRECT — single command, one permission entry:
  for f in *.yaml; do echo "$f"; done
  ```
