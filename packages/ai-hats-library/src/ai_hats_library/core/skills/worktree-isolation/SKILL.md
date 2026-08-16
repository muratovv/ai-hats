---
name: worktree-isolation
description: Isolated development using git worktrees so the main branch stays clean. Use when starting any non-trivial task (execute state), doing parallel work on multiple tasks, making risky changes you might want to discard, or delegating to a sub-agent (automatic via ai-hats agent --isolation).
ai_hats:
  runtime_hooks:
    PreToolUse:
      - matcher: Edit|Write|MultiEdit
        script: hooks/wt_gate.py
      # Claude-surface tool; inert where it does not exist (HATS-1278).
      - matcher: EnterWorktree
        script: hooks/wt_entry_gate.py
license: MIT
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

> **Worktree Python Environment & Interpreter Trap (HATS-1242).** The main venv's editable install points at the MAIN checkout, so **any** command run with that interpreter observes the main checkout — not only tests. `ai-hats` CLI invocations and compose smokes count, and so does anything else you would call verification.
>
> - **Run everything through the worktree's own interpreter**: `./.venv/bin/python -m pytest …`. `wt create` mints that venv for you (`worktree-venv`, HATS-1291); if it is missing, `uv venv .venv && VIRTUAL_ENV=.venv uv pip install -e .` (plus any workspace sub-packages `-e packages/...`).
> - **Verifying a library-DATA change is the dangerous case.** For a code change the session tripwire (`tests/conftest.py`) refuses the run, so the trap is loud. For `SKILL.md` / trait / role `config.yaml` there is no tripwire: a compose smoke run against the main venv succeeds, exits 0, and validates a tree your change never touched. Validate the worktree file directly, or put the worktree venv on PATH.

## Workflow

1. **Start task** → create worktree:
   ```
   ai-hats wt create feat/PROJ-004
   cd <worktree-path>
   ```

   **Claude surface only — enter with `cd`, never the `EnterWorktree` tool.** It
   builds a rival worktree outside ai-hats (no state, no locks, no venv, wrong
   branch name), or raises an approval prompt nothing can suppress. The
   `wt_entry_gate.py` PreToolUse hook denies it and repeats this recipe
   (HATS-1278). No other surface has the tool — skip this paragraph there.

   **Claude surface only — delegate with `ai-hats agent --isolation`, never the
   Agent tool's `isolation: "worktree"`.** A subagent that writes leaves its
   worktree on disk on a `worktree-agent-<hex>` branch — registered in git,
   invisible to `ai-hats wt list` (measured, HATS-1285). A read-only subagent
   needs no isolation at all. No other surface has the parameter — skip this
   paragraph there.

   A **PreToolUse gate** (`hooks/wt_gate.py`) **hard-denies** a code/config Edit/Write in
   the **main checkout** — interactive and headless (HATS-889; the old nudge was ignored,
   PROX-375). On a deny, move into a worktree and re-apply: `ai-hats wt status` for an
   active one, else `ai-hats wt create <type>/<name>` from `master`. Don't ask to skip a
   worktree (making one is one command); ask the supervisor only for a genuine
   direct-master change. Bypass is supervisor-only (`AI_HATS_WT_GATE_OFF=1`, never
   agent-set). Docs, non-trigger extensions, and gitignored paths (tracker, `ai-hats.yaml`)
   are exempt (trigger set: `hooks/code_extensions.json`).

2. **Work** — commit freely in the worktree. Main tree is untouched.

3. **Finish** → hand off for review; the merge is review-gated (HATS-1019).
   `wt merge` — and the merge inside `transition done` — is refused without
   `AI_HATS_MERGE_ACK=1`. The flag attests that review PASSED: set it on your
   own merge only when the conversation shows the supervisor saw this diff
   AND review passed (notes drained, no blocking findings, explicit go).
   Setting it without that evidence is self-grant — an auditable violation,
   same discipline as `AI_HATS_SHARED_STATE_ACK`. The canonical close:
   ```
   cd <project-dir>
   rack transition <id> review    # then STOP — supervisor reviews the diff
   # after review passed (diff seen + notes drained + explicit go).
   # `export`, never an inline prefix: the guard refuses an inline ack on the
   # agent's own command as a self-grant (HATS-1639). On ONE line: a lone
   # export dies with the shell that ran it (HATS-1654).
   export AI_HATS_MERGE_ACK=1 && ai-hats wt merge <task-branch>
   rack transition <id> done      # ack-free: already-merged cleanup (HATS-596)
   ```
   The supervisor may equally run the merge himself. Yolo-mode is inherited,
   not requested: a supervisor-exported `AI_HATS_MERGE_ACK=1` flows into
   subagents via plain env inheritance.
   If `wt merge` refuses with `Refused (drift)`, the base holds commits
   your branch never took in (another agent's worktree merged, or
   `origin/<base>` received commits). Re-verify your changes against the
   new base (re-run grep-verify, re-check moved/renamed paths), then
   **rebase in the worktree** — `git rebase <base>` clears the guard, no
   flag needed, so `rack transition <id> done` works again (HATS-1307).
   `--accept-drift` is for the other case: merging a stale baseline you
   accept knowingly. **Do not** pass `--force` for drift — `--force` only
   bypasses uncommitted changes; drift has its own override (HATS-457).
   Neither `--force` nor `--accept-drift` bypasses the consent gate.

4. **Abandon** → discard:
   ```
   cd <project-dir>
   ai-hats wt discard
   ```

## Pre-merge checklist (long-lived tasks)

For tasks that span multiple sessions OR multiple review rounds,
the base branch (usually `master`) can drift forward while you work
— other agents' worktrees merge in parallel, or `origin/<base>`
receives commits. `rack transition <id> done` will then
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
rack transition <id> done   # ack-free once the branch is merged; do NOT `git merge` by hand
```

The merge itself is review-gated (step 3 above) — never `git merge <task-branch>`
yourself (a manual pre-merge collides with the FSM merge, HYP-023). If the close
still reports drift, you skipped the rebase above — do it, then re-run the
transition. Only a baseline you knowingly leave stale needs
`ai-hats wt merge --accept-drift` from the main repo.

**Skip this for short tasks** (< ~2 hours wall-clock, no review
rounds). Drift typically only matters on multi-session work; for a
30-minute task the base hasn't moved.

If you skipped the checklist and hit `WorktreeDriftError` anyway,
the recovery is the same rebase — see the drift block in step 3 of
the Workflow above.

## Commands

| Command                      | What it does                                            |
| ---------------------------- | ------------------------------------------------------- |
| `ai-hats wt create <branch>` | Create worktree on new branch                           |
| `ai-hats wt merge`           | Squash-merge changes back, clean up                     |
| `ai-hats wt discard`         | Delete worktree and branch                              |
| `ai-hats wt list`            | Show all worktrees                                      |
| `ai-hats wt status`          | Show active worktree                                    |
| `ai-hats wt exec -- <cmd>`   | Run command in worktree, where you stand (+ PYTHONPATH) |
| `ai-hats wt env [<branch>]`  | Print `export WT=... PYTHONPATH=...` for eval           |

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

**Use `wt exec` instead of manual WT=/PYTHONPATH= boilerplate.** It is an
environment wrapper, not a teleporter: it runs your command **where you stand**
when your cwd is inside the worktree, and at the worktree root otherwise.

**It swaps the import path, not the interpreter** (HATS-1304). Measured:

```bash
ai-hats wt exec task/hats-1 -- python -c 'import sys, ai_hats; print(sys.executable, ai_hats.__file__)'
#   sys.executable   -> <MAIN checkout>/.venv/bin/python
#   ai_hats.__file__ -> <worktree>/src/ai_hats/...     (via PYTHONPATH)
```

That is enough for in-process imports and nothing else. Any subprocess that does
not inherit `PYTHONPATH`, and any `ai-hats` binary found on `PATH`, sees the MAIN
checkout. **So for pytest — and for anything that spawns subprocesses — use the
worktree's own interpreter**; the cost of not doing so is not a quiet false green
but a loud false red, a wall of HATS-1242 tripwire errors about a mismatch you
did not cause.

```bash
# CORRECT — pytest and anything spawning subprocesses, from inside the worktree:
./.venv/bin/python -m pytest tests/test_foo.py -xvs

# CORRECT — wt exec for in-process, single-shot commands:
ai-hats wt exec -- ruff check src/
ai-hats wt exec -- python -c 'import ai_hats; print(ai_hats.__file__)'

# WRONG — hand-rolled paths, and one dead permission grant per worktree:
WT=/var/folders/.../ai-hats-wt-...
PYTHONPATH=$WT/src python -m pytest tests/test_foo.py -xvs
```

**Subprojects.** A worktree may hold a subproject with its own `pyproject.toml`
and venv. `cd` into it and carry on, or name it with `-C` from outside — never
`cd` to an absolute worktree path:

```bash
cd packages/ai-hats-wt && ../../.venv/bin/python -m pytest    # from inside the worktree
ai-hats wt exec task/hats-1 -C packages/ai-hats-wt -- ruff check .  # from the main checkout
```

Reaching a subproject's *tests* from the main checkout means standing in the
worktree first — `-C` moves the cwd, not the interpreter.

PYTHONPATH follows the project that **owns** the directory you run in: the
nearest ancestor with a `pyproject.toml`, bounded by the worktree root. So a
subproject gets its own `src`, and a plain subdirectory keeps the worktree-root
workspace.

Pass `--` before any command that has its own `-C` (e.g. `make -C`).

**Reaching another worktree.** A leading token naming an active branch is a
selector and always beats cwd, so this works from inside a *different* worktree
too (HATS-1213). Without a selector the worktree comes from cwd, else the sole
active one; with several active and no selector, `wt exec` refuses and lists them.

For interactive shell work (rare) — bare, or named to reach another worktree:

```bash
eval "$(ai-hats wt env)"
eval "$(ai-hats wt env task/hats-1)"
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

## Anti-Patterns

- Working directly on main branch for non-trivial changes — use a worktree
- Forgetting to `cd` back to project dir before merge/discard — commands fail silently
- Multiple active worktrees without tracking — leads to forgotten branches
- Running `ai-hats wt create` / `wt merge` / `wt discard` / `rack transition <id> done|failed|cancelled` from inside a linked worktree — all blocked (HATS-788). The teardown commands run `git worktree remove` on the very cwd you are standing in, orphaning your shell so every later `ai-hats` mis-resolves the tracker. Always `cd` back to the main repo first; use `ai-hats wt exec` / `ai-hats wt env` to act on a worktree without leaving it.
- Mixing manual `wt create` with `rack transition <id> execute` from the main repo — if you created a worktree manually and want the task to use it, `cd` into the worktree first, then transition. Otherwise the transition errors out with a clear remediation message.
- Invoking `ai-hats wt create` (or `rack transition <ID> execute`) while the main repo's HEAD is not on the worktree merge target — blocked with a "Refused: … not the worktree merge target" error (HATS-518). The worktree inherits its merge target from the current branch, so creating from the wrong branch causes `wt merge` to silently land on it. The target is `master`/`main` by default, or a configured `worktree.merge_target` (see the fork-workflow note below). Recovery: the error names the branch — `git checkout <that branch>` in the main repo, then retry.
- **Working without committing inside a worktree** — uncommitted work in a worktree is NOT protected. The worktree is a filesystem directory that parallel sessions, cleanup hooks, or `git worktree remove --force` can destroy without warning, and there is **no recovery** for uncommitted changes. Commit at every meaningful checkpoint (every passing test run, every completed sub-task). If a step could be reverted with `git checkout HEAD -- .`, you've waited too long to commit.
- **Finishing the worktree cycle with raw git** — running `git merge --no-ff <task-branch>`, `git worktree remove`, or a manual `git push` to the base branch instead of `ai-hats wt merge` / `rack transition <id> done`. The CLI wrappers run the FSM lifecycle hooks — per-branch + base-branch merge-locks, drift-check, stale-lock recovery, state cleanup (HATS-477/484). Raw git skips every one of them and re-opens the race/drift bugs those epics closed; a manual merge *before* FSM-`done` also produces a double-merge conflict that then needs `--force` (HYP-023). **Scope:** this targets the **lifecycle transitions only** (merge-to-base + cleanup + done). Raw git for *inspection* (`git status`/`log`/`diff`, `git worktree list`) and for *in-worktree conflict resolution* during a rebase stays fine.

## Specific base or target branch

Fork/dogfood repos can cut worktrees from one branch and merge them into another
(base ≠ merge-target) via the `worktree` block in `ai-hats.yaml`. Rarely needed;
full contract in `docs/how-to-configure.md`, section "The `worktree` block"
(HATS-942) — for a project whose dev trunk is not its upstream default branch.

## If You End Up With a Stray Worktree

```
git worktree list                 # audit
git worktree remove <path>        # remove a stray linked worktree
git worktree prune                # clean stale metadata
rm <ai_hats_dir>/sessions/worktree.json           # if ai-hats state is stale
```

Rule of thumb: one task, one worktree, one `<ai_hats_dir>/sessions/worktree.json` (in the main repo).

## Shipped on Master (Retrospective Close)

Work shipped on the base out-of-band? From `brainstorm`/`plan`, fast-close with a
forced terminal transition: `rack transition <id> --state done --force --reason "…"`.
From `execute`/`document`/`review`: `rack transition <id> done` finalizes an
already-merged branch even if the worktree/state is gone (HATS-697).
