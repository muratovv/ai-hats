# Worktree Isolation

Isolated development using git worktrees. Each task gets its own working copy — main branch stays clean.

## Workflow

1. **Start task** → create worktree:
   ```
   ai-hats wt create feat/HATS-004
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

## When to Use

- Starting any non-trivial task (execute state)
- Parallel work on multiple tasks
- Risky changes you might want to discard
- Sub-agent delegation (automatic via `ai-hats run --isolation`)

## Conventions

- Branch naming: `type/TICKET-ID` (e.g., `feat/HATS-004`, `fix/HATS-012`)
- One active worktree at a time (per project)
- Always `cd` back to project dir before merge/discard
- Commit your work in the worktree before merging
