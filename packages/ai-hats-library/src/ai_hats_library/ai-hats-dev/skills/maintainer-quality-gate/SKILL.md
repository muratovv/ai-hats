---
name: maintainer-quality-gate
description: When a change owes an e2e test, and the gates that enforce it — pre-push e2e+smoke to master plus the ->review, ->merge and ->done gates. Use when changing the CLI, a shell script, the install flow or any installed hook, and when handing over, merging or closing a card.
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

**This directory is machinery, not prose.** It carries the gates that guard the
ai-hats codebase — one thin script per edge in `hooks/`, and the git one in
`git_hooks/` — and the role binds each by path (`ai-hats-dev/roles/maintainer/config.yaml`).
Delete it and the gates stop being installed.

**A gate is a few lines: the stages it requires, and a call into `lib/gate.sh`.**
`hooks/done-gate.sh --stages` prints the list. How a stage runs, and how a marker
is earned for it, is the project's `scripts/gates.sh` — the gate only hands the
list over. Nothing here runs a stage itself (ADR-0023 D6/D7).

| gate          | where it fires           | who is at the refusal | asks                            |
| ------------- | ------------------------ | --------------------- | ------------------------------- |
| `review-gate` | every road into `review` | the agent, alone      | is this work fit for a reviewer |
| `merge-gate`  | `wt:pre-merge`           | the agent, alone      | is this branch fit for master   |
| `done-gate`   | every road into `done`   | the supervisor        | is master green after this card |
| `push-gate`   | `git push` to master     | a human               | is the pushed tree green        |

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

## Earning a gate

    cd <task worktree>
    make review-gate            # or merge-gate / done-gate
    hooks/done-gate.sh --stages # what a gate requires

A gate is earned per STAGE: the run skips every stage already marked green for
this tree, runs the rest in the declared order, stops at the first red, and
stamps each green one. A refusal on the edge names exactly the stages still
missing and the command above. So a card that ran `make review-gate` and later
`make done-gate` pays for the difference, not twice.

The subject is always a commit — `HEAD`, or `REV=<sha>`. The run happens in the
checkout only when it is clean and at that commit; otherwise in a one-shot
scratch checkout of the commit. A dirty desk therefore never taints a marker
and never blocks one either.

Neither card gate runs the e2e tier. If `--stages` does not name what your
change touched, run it yourself and say so — e.g. `pytest -m integration
tests/e2e/`. Nothing refuses here, which is why the second command is not
optional.

## Pushing master

    scripts/run-e2e-gate.sh    # out of band, BEFORE the push
    git push origin master     # the hook then passes instantly

## Status is printed, not documented

**Do not come back to this file to find out what happened. Read what the gate
said.** Every branch narrates itself with a reason and a remedy — the refusals,
the green, and every quiet pass-through alike. The one thing it never prints is
the transcript, which lands beside the card; glob it, never hand-build the name:

    <tasks_dir>/<ID>/.checks/*done-gate.sh*.log

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
