---
name: quality-gate
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

# quality-gate

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

## Passing a gate

1. **Transition.** `rack transition <ID> review` / `done`, or `ai-hats wt
   merge`. Run nothing first: the gate reads markers inside the lock and, when
   they are missing, refuses with the command that earns them.
2. **Refused?** The refusal ends with that command — run it:

       Missing:
           integration
           merge-smoke
           master-ci

       Run the gate on that exact content, then retry:

           cd <worktree> && make done-gate

   (`make done-gate REV=<sha>` for a card already merged.) It runs only the
   stages the tree has not earned, in order, stops at the first red, and
   stamps each green one.

   **A stage the gate does not declare** — `e2e-rack`, say, absent from
   `<gate>.sh --stages` — is a **zone**: an area of the codebase and the tests
   that assert it. The gates BEFORE the merge (`review`, `merge`) require what
   they declare PLUS the zones your diff touches, so the set depends on what you
   changed and cannot be declared in advance; `->done` never asks for them, so
   you pay for your zone once. Which gate asks: `<gate>.sh --zones`. `bash scripts/gates.sh touched` prints what your change adds and
   `bash scripts/gates.sh zones` the whole table; `make <gate>` earns them like
   any other stage. Nothing is wrong with the gate — read it as the tests your
   own area owns, asked for where you can still fix them alone.
3. **Green** — the run's first line is `RESULT …, green`. Its last says what
   the transition after this one will additionally demand, so you can earn it
   now instead of being refused for it later:

       [gates] next: merge-gate, review-gate also need wheel-contents; done-gate also needs integration, merge-smoke, master-ci

   `next: nothing` means no gate on the road is short of anything. Transition.
4. **Red** — the run's block, verdict first:

       [gates] RESULT tree 5f225a1b (2ac163eb): 11 cached, 1 ran, FAILED unit (rc=1)
       [gates] fix unit, then: make done-gate
       [gates] re-run just these:
           /path/to/repo/.venv/bin/python -m pytest tests/test_x.py::test_y
       [gates] unit said:
       tests/test_x.py:12: AssertionError: …
       1 failed, 6024 passed, 2 skipped in 40.1s
       [gates] lint: 1203 files already formatted
       …
       [gates] cached (11): e2e-catalog …
       [gates] 2ac163eb (tree 5f225a1b) in place: <worktree>
       [gates] transcript: /tmp/gates-run.Xy12ab

   In order of importance: the verdict, what to do, what the red stage said
   (a failing test per line, a finding per line, or `master-ci`'s run URL),
   one line per green stage, the cached ones, the subject, and the dir holding
   every stage's full output. Triage the red stage:

   - a red pytest stage hands you the narrow command already — paste the
     `re-run just these` line. Do not rebuild it: the interpreter in it is the
     one this checkout answers for, and another imports someone else's sources;
   - the stage alone, bare, exactly what CI runs: `bash scripts/gates.sh unit`
     (`bash scripts/gates.sh list` says what each stage checks);
   - a checker: the tool itself, `ruff check <path>` or
     `python scripts/check_<name>.py`;
   - fix, commit, `make <gate>` again. A fix is a new tree, and a new tree
     earns every stamp afresh: the checkers take seconds, `unit` about a
     minute — do not reach for `--fresh` or a narrower gate to save it.

   **`the desk is dirty`, under the verdict?** A run with uncommitted changes
   happens in a scratch checkout of the commit, so it judges what is COMMITTED
   — a red one against a fix that was never there, a green one vouching for a
   fix that never ran. Commit, then run again.

   `master-ci` is the one red the tree cannot fix: master itself is red. Its
   knob (see "No bypass") is the supervisor's to set, never yours.
5. **Touched what the gate does not name?** No card gate runs the whole tier
   under one name: `->merge` asks for the zones your diff touches, `->done` for
   `e2e-default` — the half no zone claims — plus `merge-smoke`, and never for
   your zones again. What that leaves out is a zone your change breaks WITHOUT
   touching its prefix, and nothing refuses there. Run the tier yourself and say
   so. It outlives a foreground call, so run it in the background through the
   wrapper, which exits with the tier's own status:

       timeout 1800 bash packages/ai-hats-library/src/ai_hats_library/ai-hats-dev/skills/quality-gate/bin/runcheck.sh \
           --log /tmp/e2e.log -- bash scripts/gates.sh e2e

   Report the number in `/tmp/e2e.log.rc`, never the completion notice: that
   notice reports the whole command, so a hand-rolled `; echo $?` is what it
   announces — two red tiers arrived as `exit code 0` that way. What a gate
   requires of every tree:

       bash packages/ai-hats-library/src/ai_hats_library/ai-hats-dev/skills/quality-gate/hooks/done-gate.sh --stages

### What a stamp saves

A marker names the TREE a stage ran on, per stage. It spares the re-run of an
unchanged tree, never of a fixed one:

- the transition after a green run — instant;
- `make done-gate` after `make review-gate` — pays the difference, not twice;
- a merge commit whose tree equals the branch tip — a fast-forward, or a
  merge into a master that moved nothing else — `REV=<merge sha>` finds every
  stamp already there;
- a stage marked green that you want to see run anyway:
  `<gate>.sh --run --fresh` (no make target).

### Where it runs, and what that costs

The subject is a commit — `HEAD`, or `REV=<sha>`. The run happens in the
checkout only when it is clean and at that commit; otherwise in a one-shot
scratch checkout of the commit under the shared git dir, with a venv of its
own. Measured 2026-09-03 with a warm uv cache: +2.5 s on `lint`, +5 s on a
35 s `unit`. A dirty desk therefore never taints a marker and never blocks
one, and `REV=` is not a cost worth working around.

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
skip. The one knob is `AI_HATS_RED_MASTER_ACK=1`: it lets `master-ci` pass on a
red master, for the card that fixes it, and says so in the run.
