# Rule: Tool-Call Hygiene

**Use Bash only when no dedicated tool fits.** When in doubt — dedicated tool wins.

## 1. Forbidden Bash anti-patterns (with replacements)

| ❌ Bash | ✅ Use instead | Why |
|---|---|---|
| `grep -rn PATTERN path/` | **Grep** tool | Native, faster, no shell parse |
| `rg PATTERN path/` | **Grep** tool | Same — Grep wraps ripgrep |
| `find . -name "*.py"` | **Glob** tool | Glob handles patterns natively |
| `ls -R dir/` | **Glob** tool | Recursive listing without shell |
| `cat path/file.md` (known path) | **Read** tool | Numbered output, no truncation surprises |
| `head -50 file` / `tail -100 file` | **Read** with `offset`/`limit` | Same data, paginated cleanly |
| `sed -i 's/A/B/' file` | **Edit** tool | Edit checks uniqueness; prevents silent multi-replace |
| `awk -i ...` / inline rewrite | **Edit** / **Write** | See above |

**Bash IS appropriate for:** `git`, `pytest`, build commands, multi-stage pipes that have no dedicated alternative, anything reading shell-only state (env vars, processes, exit codes).

### Worked examples

❌ **Bad** — Bash substitutes Grep:
```
Bash("rg -n 'class TaskCard' src/")
```
✅ **Good**:
```
Grep(pattern="class TaskCard", path="src/", output_mode="content", -n=true)
```

❌ **Bad** — Bash substitutes Glob:
```
Bash("find . -name 'task.yaml' | head -20")
```
✅ **Good**:
```
Glob(pattern="**/task.yaml")
```

❌ **Bad** — Bash substitutes Read on a known path:
```
Bash("cat src/ai_hats/retro/builder.py | head -100")
```
✅ **Good**:
```
Read(file_path="/abs/path/to/src/ai_hats/retro/builder.py", limit=100)
```

❌ **Bad** — Bash substitutes Edit:
```
Bash("sed -i 's/old_name/new_name/g' src/foo.py")
```
✅ **Good**:
```
Edit(file_path="/abs/path/to/src/foo.py", old_string="old_name", new_string="new_name", replace_all=true)
```

## 2. Session-entry batch

When restoring context at session start (reading the task card, recent retros, current state), independent reads MUST be issued as **a single parallel tool block** — not one-by-one. One assistant message, multiple `Read`/`Grep`/`Glob` calls.

## 3. Parallel by default

If multiple tool calls don't depend on each other's output, invoke them in a single parallel batch. Never chain independent Reads, Greps, or Globs sequentially.

## 4. Combine shell checks

Merge related shell-only commands into one Bash call with `&&`
(e.g., `git status && git log --oneline -5` instead of two separate calls).

## 5. 5-call threshold

If you notice 5+ similar sequential tool calls — STOP. Rethink the approach: batch, combine, or use a more targeted tool.

## 6. Discovery budget

Initial codebase exploration: ≤ 3–5 tool calls. For broad searches use **Agent(Explore)** rather than manual sequential probing.
