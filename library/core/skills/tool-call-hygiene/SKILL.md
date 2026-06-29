---
name: tool-call-hygiene
description: Choose between Bash and dedicated Claude Code tools (Grep/Glob/Read/Edit) and edit files efficiently. Triggers on intent to grep/find/cat/sed/rg, multiple sequential reads, session-start context restore, codebase discovery, or multiple sequential Edits to one file.
ai_hats:
  runtime_hooks:
    PreToolUse:
      - matcher: Bash
        script: hooks/tool_call_hygiene_guard.sh
---

# Tool-Call Hygiene

Decide between Bash and dedicated Claude Code tools. Batch and parallelize.

## When to Use

- About to call Bash for `grep`/`rg`/`find`/`ls -R`/`cat`/`head`/`tail`/`sed`/`awk`
- Restoring context at session start (multiple Reads/Greps/Globs)
- Initial codebase exploration (>3 tool calls planned)
- Noticing 5+ similar sequential calls
- About to make 3+ Edits to the same existing file (consider one Write)

## Conventions

### Forbidden Bash anti-patterns

| ❌ Bash                            | ✅ Use instead                 | Why                                                   |
| ---------------------------------- | ------------------------------ | ----------------------------------------------------- |
| `grep -rn PATTERN path/`           | **Grep** tool                  | Native, faster, no shell parse                        |
| `rg PATTERN path/`                 | **Grep** tool                  | Same — Grep wraps ripgrep                             |
| `find . -name "*.py"`              | **Glob** tool                  | Glob handles patterns natively                        |
| `ls -R dir/`                       | **Glob** tool                  | Recursive listing without shell                       |
| `cat path/file.md` (known path)    | **Read** tool                  | Numbered output, no truncation surprises              |
| `head -50 file` / `tail -100 file` | **Read** with `offset`/`limit` | Same data, paginated cleanly                          |
| `sed -i 's/A/B/' file`             | **Edit** tool                  | Edit checks uniqueness; prevents silent multi-replace |
| `awk -i ...` / inline rewrite      | **Edit** / **Write**           | See above                                             |

**Bash is appropriate for:** `git`, `pytest`, build commands, multi-stage pipes that have no dedicated alternative, anything reading shell-only state (env vars, processes, exit codes).

### Batching and parallelism

- **Session-entry**: independent context reads → a single parallel tool block, never one-by-one.
- **Parallel by default**: any independent calls → single batch.
- **Combine shell checks**: merge with `&&` (e.g. `git status && git log --oneline -5`).
- **5-call threshold**: 5+ similar sequential calls → STOP, batch or use a more targeted tool.
- **Discovery budget**: initial codebase exploration ≤ 3–5 tool calls; broader → `Agent(Explore)`.

### Edit efficiency

- **New file → Write**, not a sequence of Edits — building a fresh file with incremental Edits wastes turns.
- **3+ Edits to the same existing file → STOP**; plan all changes and **Write** it once.
- **Edit is surgical**: targeted, isolated changes to existing files. Read → plan all edits → execute in the fewest operations.

For ❌/✅ worked examples → `references/examples.md`.

## Anti-Patterns

- Reaching for `rg`/`grep` from Bash when Grep tool fits.
- Sequential Reads when reads are independent — always parallel.
- Drilling into a codebase via shell `ls`/`find` instead of one `Agent(Explore)` call.
