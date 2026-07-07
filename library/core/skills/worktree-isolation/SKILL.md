---
name: worktree-isolation
description: Isolated development using git worktrees so the main branch stays clean. Use when starting any non-trivial task (execute state), doing parallel work on multiple tasks, making risky changes you might want to discard, or delegating to a sub-agent (automatic via ai-hats agent --isolation).
ai_hats:
  runtime_hooks:
    PreToolUse:
      - matcher: Edit|Write|MultiEdit
        script: hooks/wt_gate.py
---

# Worktree Isolation

Isolated development using git worktrees. Each task gets its own working copy — main branch stays clean.

> **Invocation in a harness shell.** Harness-spawned bash does not inherit an activated venv. Before running any `ai-hats` command, define a resolver once (host launcher on PATH, else the project venv's interpreter — no `bin/ai-hats` console script since HATS-790):
>
> ```bash
> ah() { if command -v ai-hats >/dev/null 2>&1; then ai-hats "$@"; else ./.venv/bin/python -m ai_hats "$@"; fi; }
> ah wt list
> ```
>
> If neither works, the project's venv interpreter lives at `./.venv/bin/python` (invoke the package as `./.venv/bin/python -m ai_hats …`). Resolve the path explicitly — falling back blindly wastes a turn.

## Workflow

1. **Start task** → create worktree:
   ```
   ai-hats wt create feat/PROJ-004
   cd <worktree-path>
   ```

   A **PreToolUse gate** (`hooks/wt_gate.py`) **hard-denies** a code/config Edit/Write in
   the **main checkout** — interactive and headless (HATS-889; the old nudge was ignored,
   PROX-375). On a deny, move into a worktree and re-apply: `ai-hats wt status` for an
   active one, else `ai-hats wt create <type>/<name>` from `master`. Don't ask to skip a
   worktree (making one is one command); ask the supervisor only for a genuine
   direct-master change. Bypass is supervisor-only (`AI_HATS_WT_GATE_OFF=1`, never
   agent-set). Docs, non-trigger extensions, and gitignored paths (tracker, `ai-hats.yaml`)
   are exempt (trigger set: `hooks/code_extensions.json`).

2. **Work** — commit freely in the worktree. Main tree is untouched.

3. **Finish** → merge back:
   ```
   cd <project-dir>
   ai-hats wt merge            # squash merge (default)
   ai-hats wt merge --no-squash  # regular merge
   ```
   If `wt merge` refuses with `Refused (drift)`, the base branch
   advanced since `wt create` (another agent's worktree already
   merged, or `origin/<base>` received commits). Re-verify your
   changes against the new base (re-run grep-verify, re-check
   moved/renamed paths), then `ai-hats wt merge --accept-drift`.
   **Do not** pass `--force` for drift — `--force` only bypasses
   uncommitted changes; drift has its own override (HATS-457).

4. **Abandon** → discard:
   ```
   cd <project-dir>
   ai-hats wt discard
   ```

## Pre-merge checklist (long-lived tasks)

For tasks that span multiple sessions OR multiple review rounds,
the base branch (usually `master`) can drift forward while you work
— other agents' worktrees merge in parallel, or `origin/<base>`
receives commits. `ai-hats task transition <id> done` will then
fail with `WorktreeDriftError` and you'll need an ad-hoc recovery.

Rebase **before** closing so drift never accumulates past your
verification window. Do the rebase IN the worktree, but run the close
from the MAIN checkout:

```bash
# Rebase the task branch in the worktree (git work is fine here):
cd <worktree-path>
git fetch --all
git rebase <base-branch>         # usually master
# resolve any conflicts; re-run the affected tests

# Close from the MAIN checkout — NOT from inside the worktree. A
# worktree-backed `transition done` issued from inside its own worktree
# is refused (HATS-788): the merge runs `git worktree remove` on the cwd
# you are standing in, which would orphan your shell and desync the tracker.
cd <project-dir>
ai-hats task transition <id> done   # auto-merges; do NOT `git merge` by hand
```

`transition done` runs the merge for you — never `git merge <task-branch>`
yourself first (a manual pre-merge collides with the FSM merge, HYP-023). If
the base moved and the close reports drift, accept it explicitly from the main
repo with `ai-hats wt merge --accept-drift`, then re-run the transition.

**Skip this for short tasks** (< ~2 hours wall-clock, no review
rounds). Drift typically only matters on multi-session work; for a
30-minute task the base hasn't moved.

If you skipped the checklist and hit `WorktreeDriftError` anyway,
the recovery is the same rebase plus an explicit acceptance flag —
see the drift block in step 3 of the Workflow above.

## Commands

| Command                      | What it does                                        |
| ---------------------------- | --------------------------------------------------- |
| `ai-hats wt create <branch>` | Create worktree on new branch                       |
| `ai-hats wt merge`           | Squash-merge changes back, clean up                 |
| `ai-hats wt discard`         | Delete worktree and branch                          |
| `ai-hats wt list`            | Show all worktrees                                  |
| `ai-hats wt status`          | Show active worktree                                |
| `ai-hats wt exec -- <cmd>`   | Run command in worktree (auto cwd + PYTHONPATH=src) |
| `ai-hats wt env`             | Print `export WT=... PYTHONPATH=...` for eval       |

## Teardown runs lifecycle hooks

`ai-hats wt merge` / `wt discard` / `cleanup` run any composed **`wt_out`
lifecycle hooks** *before* removing the worktree — e.g. the `hunk-review-comments`
skill drains its review sidecar so notes aren't lost. They are **fail-closed**: if a
hook fails, the teardown **aborts** and the worktree + branch are preserved (nothing
gitignored is destroyed). Force past a genuinely broken hook with
`ai-hats wt … --skip-hooks` only if you accept the loss. (`wt create` likewise runs
`wt_in` hooks to seed gitignored data — e.g. `.env` — into the fresh worktree.) These
are *component-declared* hooks, distinct from the FSM merge-locks/drift guards; to
author one see `docs/how-to-extend.md` → "Worktree lifecycle hooks".

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

The cost (a separate working copy) is justified by **parallelism, risky-discard,
or sub-agent isolation** — not by every edit. Skip it for read-only exploration
or a trivial single-file fix on a throwaway branch. The git operations *inside*
the worktree (rebase, conflict resolution, commit conventions) are
**git-mastery**'s remit; this skill owns the worktree's create → merge → discard
lifecycle.

## Conventions

- Branch naming: `type/TICKET-ID` (e.g., `feat/PROJ-004`, `fix/PROJ-012`)
- One active worktree at a time (per project)
- Always `cd` back to project dir before merge/discard
- Commit your work in the worktree before merging

## Syncing After Skill Edits

After editing `library/{core,usage}/skills/*/SKILL.md`, run:

```
ai-hats self init
```

This re-copies all skills to `.claude/skills/` and `.agent/ai-hats/library/skills/`.
**Never manually `cp` skill files** — it generates garbage permission entries.

## Anti-Patterns

- Working directly on main branch for non-trivial changes — use a worktree
- Forgetting to `cd` back to project dir before merge/discard — commands fail silently
- Manually copying skill files with `cp src/.../SKILL.md .claude/skills/.../SKILL.md` — use `ai-hats self init` instead
- Multiple active worktrees without tracking — leads to forgotten branches
- Running `ai-hats wt create` / `wt merge` / `wt discard` / `task transition <id> done|failed|cancelled` from inside a linked worktree — all blocked (HATS-788). The teardown commands run `git worktree remove` on the very cwd you are standing in, orphaning your shell so every later `ai-hats` mis-resolves the tracker. Always `cd` back to the main repo first; use `ai-hats wt exec` / `ai-hats wt env` to act on a worktree without leaving it.
- Mixing manual `wt create` with `task transition execute` from the main repo — if you created a worktree manually and want the task to use it, `cd` into the worktree first, then transition. Otherwise the transition errors out with a clear remediation message.
- Invoking `ai-hats wt create` (or `task transition <ID> execute`) while the main repo's HEAD is not on the worktree merge target — blocked with a "Refused: … not the worktree merge target" error (HATS-518). The worktree inherits its merge target from the current branch, so creating from the wrong branch causes `wt merge` to silently land on it. The target is `master`/`main` by default, or a configured `worktree.merge_target` (see the fork-workflow note below). Recovery: the error names the branch — `git checkout <that branch>` in the main repo, then retry.
- **Working without committing inside a worktree** — uncommitted work in a worktree is NOT protected. The worktree is a filesystem directory that parallel sessions, cleanup hooks, or `git worktree remove --force` can destroy without warning, and there is **no recovery** for uncommitted changes. Commit at every meaningful checkpoint (every passing test run, every completed sub-task). If a step could be reverted with `git checkout HEAD -- .`, you've waited too long to commit.
- **Finishing the worktree cycle with raw git** — running `git merge --no-ff <task-branch>`, `git worktree remove`, or a manual `git push` to the base branch instead of `ai-hats wt merge` / `ai-hats task transition <id> done`. The CLI wrappers run the FSM lifecycle hooks — per-branch + base-branch merge-locks, drift-check, stale-lock recovery, state cleanup (HATS-477/484). Raw git skips every one of them and re-opens the race/drift bugs those epics closed; a manual merge *before* FSM-`done` also produces a double-merge conflict that then needs `--force` (HYP-023). **Scope:** this targets the **lifecycle transitions only** (merge-to-base + cleanup + done). Raw git for *inspection* (`git status`/`log`/`diff`, `git worktree list`) and for *in-worktree conflict resolution* during a rebase stays fine.

## Fork workflow (base ≠ merge target)

For fork/dogfood repos where the dev trunk is not the upstream default branch
(e.g. `main` = pristine upstream mirror, `fork-main` = local integration trunk),
configure the split in `ai-hats.yaml` (HATS-942):

```yaml
worktree:
  base_branch: main        # cut task worktrees FROM this (clean upstream base)
  merge_target: fork-main  # `wt merge` / `transition done` land HERE
```

Operating rules once configured:

- **Stay on `merge_target` (`fork-main`) for the whole task lifecycle** — the
  create-time guard and the merge-time guard (HATS-533) both require HEAD to be
  on it. No mid-task `git checkout`.
- **Never develop on `base_branch` (`main`)** — ai-hats reads it only as the
  `git worktree add` start-point; your working checkout never sits on it, so it
  stays a pristine mirror.
- Both keys are optional and default to today's behavior (`base_branch` unset →
  cut from HEAD; `merge_target` unset → `master`/`main` set-membership). A
  configured branch that does not exist fails loud.

## If You End Up With a Stray Worktree

```
git worktree list                 # audit
git worktree remove <path>        # remove a stray linked worktree
git worktree prune                # clean stale metadata
rm <ai_hats_dir>/sessions/worktree.json           # if ai-hats state is stale
```

Rule of thumb: one task, one worktree, one `<ai_hats_dir>/sessions/worktree.json` (in the main repo).

## Shipped on Master (Retrospective Close)

Work shipped on the base out-of-band? From `brainstorm`/`plan`: `ai-hats task
close --resolution "…"`. From `execute`/`document`/`review`: `transition <id>
done` finalizes an already-merged branch even if the worktree/state is gone (HATS-697).
