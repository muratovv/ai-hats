---
name: maintainer-quality-gate
description: When a change owes an e2e test, and the gates that enforce it — pre-push e2e+smoke to master plus the ->merge and ->done gates. Use when changing the CLI, a shell script, the install flow or any installed hook, and when closing or merging a card.
ai_hats:
  # hook-carrier skill. `ai-hats self init` installs one
  # dispatcher per event in `.githooks/`; the script below is resolved from this
  # directory and run in place at push time (nothing is copied).
  # Hard gate: no env-var bypass; `git push --no-verify` is the only escape.
  git_hooks:
    pre-push:
      - git_hooks/pre-push-e2e-master.sh
license: MIT
---

# maintainer-quality-gate

**This directory is machinery, not prose.** It carries the three gates that
guard the ai-hats codebase, and the role binds them by path
(`ai-hats-dev/roles/maintainer/config.yaml`). Delete it and the gates stop being
installed.

| gate      | where it fires         | who is at the refusal | asks                            |
| --------- | ---------------------- | --------------------- | ------------------------------- |
| `->merge` | `wt:pre-merge`         | the agent, alone      | is this branch fit for master   |
| `->done`  | every road into `done` | the supervisor        | is master green after this card |
| pre-push  | `git push` to master   | a human               | is the pushed tree green        |

Below the gates sits the policy they enforce: **which changes owe an e2e test at
all.** That half was a standing rule until it folded in here — one home for
the question and the machine that asks it.

## When an e2e test is mandatory

A task may not reach `done` if it changed any of the trigger surface below
without adding at least one e2e test under `tests/e2e/` that exercises the real
command chain.

**The invariant:** the gate fires on any change to code that runs *outside* the
Python process — an entry point, a shell script, the install/venv flow, or any
script the assembler installs as a hook. That is where the contract lives at a
subprocess boundary the unit suite cannot observe. The list below names the
current surfaces; a path that is not on it but crosses the same boundary fires
the gate all the same.

- `src/ai_hats/cli/**/*.py` — click commands, command nesting, CLI args/flags.
- `scripts/*.sh` — shell scripts (`install-launcher.sh`, `bootstrap.sh`, …).
- `src/ai_hats/_bootstrap.py`, `src/ai_hats/cli/maintenance.py` — pip install / launcher / venv flow.
- `[project.scripts]` in any workspace `packages/*/pyproject.toml` — new or
  renamed entry points (the root `pyproject.toml` declares none).
- `packages/ai-hats-library/**/hooks/**` and `**/git_hooks/**` — PreToolUse /
  PostToolUse hook scripts and the installed git hooks (`git_hooks` is a
  distinct segment: a `**/hooks/**` glob does not match it). Highest blast
  radius in the repo: a hook gates *every* tool call of *every* role composing
  it, so a four-line change can disable every agent. The test must drive the
  **composed chain** (all hooks on the matcher, in order) via
  `tests/e2e/_helpers/hook_chain.py`, never one script in isolation — a
  single-hook test cannot observe a second hook overriding its verdict, which is
  how a blanket deny once shipped past a green suite.
- Anything else crossing an external contract: PEP 508 URL forms, click nesting,
  shell quoting, venv invocation.

**Does not fire:** internal Python modules (storage, parsing, business logic),
docs, tests-only changes, version bumps.

### What counts as an e2e test

All of these must hold:

- Lives under `tests/e2e/` (the dedicated real-subprocess CLI layer — see `tests/README.md`).
- Marked `@pytest.mark.integration`.
- Spawns a **real** subprocess chain: real `bash`, real `pip install`, real
  `ai-hats` binary. No `MagicMock`, no `monkeypatch` on `subprocess.Popen`, no
  `CliRunner.invoke()`.
- Asserts observable end-to-end side effects (exit codes, files on disk,
  captured output) — not internal call counts.

In-process `CliRunner` tests do **not** satisfy this, regardless of marker.
Neither do pipeline-integration tests.

### Plan and review

The plan names the test by path and by what it asserts — "will add e2e coverage"
is not a name. The reviewer runs it, and checks the one thing a green run cannot
show: that it **fails when the change is reverted**. If it does not, it lives
beside the change rather than exercising it, and the card returns to `execute`.

Origin: PROP-031 — two production bugs shipped past `done` because the unit
suite stubbed the very contracts the change broke.

## Handing a card over

    cd <task worktree>
    make review-gate                            # earns the marker
    scripts/ci-local.sh --stages review-gate    # prints what that marker covers

`->review` refuses without it. That edge used to ask nothing, so a card reached
a reviewer on the agent's word that the suite was green — and an agent reading a
verdict is the unreliable part: a pipeline eats the runner's status, a stray `cd`
moves the run into another checkout, a planted fixture is never scanned. A marker
removes the reading. The composition stops at the first red and writes nothing;
there is no number to misread and no way to hand over anyway.

`review-gate` and `merge-gate` name the same set, so one run clears both edges,
and a `done-gate` run clears all three.

## Closing a card

    cd <task worktree>
    make done-gate                              # earns the marker
    scripts/ci-local.sh --stages done-gate      # prints what that marker covers

Run both. The first clears **every** card edge — `done-gate` is the superset, so
its marker absorbs `->review` and `->merge`. The second is the check on the first: a green gate
is a **narrow** claim, and this is the only way to see how narrow.

Neither card gate runs the e2e tier. If `--stages` does not name what your change
touched, run it yourself and say so — e.g. `pytest -m integration tests/e2e/`.
Nothing refuses here, which is why the second command is not optional.

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
fix it, and the 2022 community keepalive workaround no longer works (paid for twice).

## No bypass

There is no `AI_HATS_E2E_SKIP` and no `--ack`. `git push --no-verify` and forging
a marker are deliberate local acts by the trusted maintainer, never an accidental
skip.
