---
name: maintainer-quality-gate
description: Maintainer-only quality gates — pre-push e2e+smoke to master, plus the ->merge and ->done gates on the two roads a card takes into it
ai_hats:
  # HATS-550 / HATS-686 — hook-carrier skill. `ai-hats self init` installs one
  # dispatcher per event in `.githooks/`; the script below is resolved from this
  # directory and run in place at push time (HATS-1337, nothing is copied).
  # Hard gate: no env-var bypass; `git push --no-verify` is the only escape.
  git_hooks:
    pre-push:
      - git_hooks/pre-push-e2e-master.sh
license: MIT
---

# maintainer-quality-gate

**This directory is machinery, not prose.** It carries the three gates that
guard the ai-hats codebase, and the role binds them by path
(`usage/roles/maintainer/config.yaml`). Delete it and the gates stop being
installed.

| gate      | where it fires         | who is at the refusal | asks                            |
| --------- | ---------------------- | --------------------- | ------------------------------- |
| `->merge` | `wt:pre-merge`         | the agent, alone      | is this branch fit for master   |
| `->done`  | every road into `done` | the supervisor        | is master green after this card |
| pre-push  | `git push` to master   | a human               | is the pushed tree green        |

## Closing a card

    cd <task worktree>
    make done-gate                              # earns the marker
    scripts/ci-local.sh --stages done-gate      # prints what that marker covers

Run both. The first clears **both** card edges — `done-gate` is the superset, so
its marker absorbs `->merge`. The second is the check on the first: a green gate
is a **narrow** claim, and this is the only way to see how narrow.

Neither card gate runs the e2e tier. If `--stages` does not name what your change
touched, run it yourself and say so — e.g. `pytest -m integration tests/e2e/`.
Nothing refuses here, which is why the second command is not optional
(HATS-1682).

Use `make merge-gate` only to merge now and close later.

## Pushing master

    scripts/run-e2e-gate.sh    # out of band, BEFORE the push
    git push origin master     # the hook then passes instantly

## Status is printed, not documented

**Do not come back to this file to find out what happened. Read what the gate
said.** Every branch narrates itself with a reason and a remedy — the refusals,
the green, and every quiet pass-through alike. The one thing it never prints is
the transcript, which lands beside the card; glob it, never hand-build the name:

    <tasks_dir>/<ID>/.checks/*done-gate*.log

## Why the push gate runs out of band

Running the suite **inside** the pre-push hook cannot work. Git opens the SSH
connection for ref-advertisement *before* running the hook, then runs the hook,
then sends the pack. GitHub closes the idle connection after ~30s, so a hook
taking minutes dies with `Connection closed by remote host` → SIGPIPE (exit 141)
and the pack is never sent.

**Do not re-attempt:** client-side `ServerAliveInterval` at 60 **and** 15 did not
fix it, and the 2022 community keepalive workaround no longer works (HATS-684,
paid for twice).

## No bypass

There is no `AI_HATS_E2E_SKIP` and no `--ack`. `git push --no-verify` and forging
a marker are deliberate local acts by the trusted maintainer, never an accidental
skip.
