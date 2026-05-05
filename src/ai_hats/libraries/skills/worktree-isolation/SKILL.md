---
name: worktree-isolation
description: Isolated development using git worktrees — main branch stays clean
---
# Worktree Isolation

Isolated development using git worktrees. Each task gets its own working copy — main branch stays clean.

> **Invocation in a harness shell.** Harness-spawned bash does not inherit an activated venv. Before running any `ai-hats` command, resolve the binary once:
> ```bash
> AH="$(command -v ai-hats || echo ./.venv/bin/ai-hats)"
> "$AH" wt list
> ```
> If neither works, the project's venv lives at `./.venv/bin/ai-hats`. Resolve the binary path explicitly — falling back blindly between `ai-hats` and the venv path wastes a turn.

## Workflow

1. **Start task** → create worktree:
   ```
   ai-hats wt create feat/PROJ-004
   cd <worktree-path>
   ```

2. **Work** — commit freely in the worktree. Main tree is untouched.

3. **Finish** → merge back:
   ```
   cd <project-dir>
   ai-hats wt merge            # squash merge (default)
   ai-hats wt merge --no-squash  # regular merge
   ```

4. **Abandon** → discard:
   ```
   cd <project-dir>
   ai-hats wt discard
   ```

## Commands

| Command | What it does |
|---------|-------------|
| `ai-hats wt create <branch>` | Create worktree on new branch |
| `ai-hats wt merge` | Squash-merge changes back, clean up |
| `ai-hats wt discard` | Delete worktree and branch |
| `ai-hats wt list` | Show all worktrees |
| `ai-hats wt status` | Show active worktree |
| `ai-hats wt exec -- <cmd>` | Run command in worktree (auto cwd + PYTHONPATH=src) |
| `ai-hats wt env` | Print `export WT=... PYTHONPATH=...` for eval |

## Running Commands in Worktree

**Always use `wt exec` instead of manual WT=/PYTHONPATH= boilerplate:**

```bash
# CORRECT — single command, no env vars, no permission noise:
ai-hats wt exec -- pytest tests/test_foo.py -xvs
ai-hats wt exec -- python -c 'import ai_hats; print(ai_hats.__file__)'
ai-hats wt exec -- ruff check src/

# WRONG — generates garbage permission entries on every new worktree:
WT=/var/folders/.../ai-hats-wt-...
PYTHONPATH=$WT/src python -m pytest tests/test_foo.py -xvs
```

For interactive shell work (rare):
```bash
eval "$(ai-hats wt env)"
cd $WT
```

## When to Use

- Starting any non-trivial task (execute state)
- Parallel work on multiple tasks
- Risky changes you might want to discard
- Sub-agent delegation (automatic via `ai-hats run --isolation`)

## Conventions

- Branch naming: `type/TICKET-ID` (e.g., `feat/PROJ-004`, `fix/PROJ-012`)
- One active worktree at a time (per project)
- Always `cd` back to project dir before merge/discard
- Commit your work in the worktree before merging

## Syncing After Skill Edits

After editing `src/ai_hats/libraries/skills/*/SKILL.md`, run:
```
ai-hats self bump
```
This re-copies all skills to `.claude/skills/` and `.agent/skills/`.
**Never manually `cp` skill files** — it generates garbage permission entries.

## Anti-Patterns
- Working directly on main branch for non-trivial changes — use a worktree
- Forgetting to `cd` back to project dir before merge/discard — commands fail silently
- Manually copying skill files with `cp src/.../SKILL.md .claude/skills/.../SKILL.md` — use `ai-hats self bump` instead
- Multiple active worktrees without tracking — leads to forgotten branches
- Running `ai-hats wt create` from inside a linked worktree — blocked with an error. Always `cd` back to the main repo first.
- Mixing manual `wt create` with `task transition execute` from the main repo — if you created a worktree manually and want the task to use it, `cd` into the worktree first, then transition. Otherwise the transition errors out with a clear remediation message.
- **Working without committing inside a worktree** — uncommitted work in a worktree is NOT protected. The worktree is a filesystem directory that parallel sessions, cleanup hooks, or `git worktree remove --force` can destroy without warning, and there is **no recovery** for uncommitted changes. Commit at every meaningful checkpoint (every passing test run, every completed sub-task). If a step could be reverted with `git checkout HEAD -- .`, you've waited too long to commit.

## If You End Up With a Stray Worktree
```
git worktree list                 # audit
git worktree remove <path>        # remove a stray linked worktree
git worktree prune                # clean stale metadata
rm .agent/worktree.json           # if ai-hats state is stale
```
Rule of thumb: one task, one worktree, one `.agent/worktree.json` (in the main repo).
