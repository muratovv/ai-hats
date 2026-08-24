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

The prose below is only what neither the machine nor an ADR already says. The
gate's own refusal is self-sufficient — it prints the tree, a copy-pasteable
command, and the stages that command runs — so nothing here repeats it. The
design rationale lives in the ADRs mapped at the bottom; do not restate it here.
It was restated once, and **the copy is what rotted** — this file's duration
claim drifted 6× while ADR-0023's measured table stayed right, because the ADR
keeps its numbers in one table with the measurement method beside them and this
file scattered them through rhetoric (HATS-1811). Cite the anchor, never the
number.

## The interface

    cd <task worktree> && make done-gate     # clears BOTH edges
    cd <task worktree> && make merge-gate    # clears the merge only

Prefer `done-gate`: it is the superset, so its marker clears `->merge` too
(absorption). `merge-gate` is for merging now and closing the card later.

    scripts/run-e2e-gate.sh    # out of band, BEFORE pushing master
    git push origin master     # the hook then passes instantly

## The one thing nothing will tell you: a green gate is a narrow claim

**Neither card gate runs the e2e tier, and a green one does not claim it did.**
`done-gate`'s `integration` stage is `pytest --ignore=tests/e2e`; the only thing
reaching `tests/e2e/` is `merge-smoke`, the curated `-m smoke` subset. The full
tier belongs to the **push** gate alone.

So "`make done-gate` is green" answers a narrower question than "the e2e tier is
green" — and **nothing refuses**, which is why this paragraph exists and the
rest of the file does not. A card whose change touches that tier runs
`pytest -m integration tests/e2e/` on its own and says so. Ask
`scripts/ci-local.sh --stages done-gate` rather than assuming; a card reached
review with a red e2e test behind a green marker for exactly this reason
(HATS-1682).

## Status is not documented here — it is printed

**Do not come back to this file to find out what happened. Read what the gate
said.** Every branch narrates itself with a reason and a remedy — the refusals,
the green, and every quiet pass-through alike (`lib/gate.sh`). Enumerating them
here would only be a second copy, free to disagree with the one that runs.

Two things it does **not** print:

- **The transcript.** Every run leaves one beside the card. Glob it, never
  hand-build the name: `<tasks_dir>/<ID>/.checks/*done-gate*.log`.
- **Where the composition lives** — `scripts/ci-local.sh` → `gate_composition`,
  project-side, never in this skill (ADR-0023 D7). A library file that restated
  project content drifted from it within days.

## Why the push gate runs out of band

Not in any ADR — it is here because it was paid for twice (HATS-684).

Running the suite **inside** the pre-push hook is incompatible with pushing to
GitHub over SSH. Git opens the SSH connection for ref-advertisement *before*
running the hook, then runs the hook, then sends the pack. GitHub closes the
idle connection after ~30s, so any hook taking longer dies with
`Connection closed by remote host` → SIGPIPE (exit 141), and the pack is never
sent. Minutes against seconds: the order-of-magnitude gap is the whole argument,
and it does not depend on how fast our suite happens to be this month.

**Negative result — do not re-attempt.** Client-side `ServerAliveInterval` was
tried at 60 **and** 15 and did not fix it. The 2022 community keepalive
workaround no longer works on GitHub.

## How to bypass

You can't, in the normal flow: there is no `AI_HATS_E2E_SKIP=1` and no `--ack`.
Forging a marker *is* a bypass, but it is the moral equivalent of
`git push --no-verify` — a deliberate local act by the trusted maintainer, not
an accidental skip (ADR-0023 D8).

## Where the rationale lives

Everything about *why the machine is shaped this way* is ADR material. This file
used to carry a second copy of it; that copy is what went stale.

| question                                             | anchor                     |
| ---------------------------------------------------- | -------------------------- |
| marker keyed by tree, composition digest, absorption | ADR-0023 D5                |
| the gate primitive, dirty-tree rule, refusal text    | ADR-0023 D6                |
| engine owns points, the role owns content            | ADR-0023 D7                |
| bypass: explicit, recorded, surfacing                | ADR-0023 D8                |
| the full e2e tier on the CI road                     | ADR-0023 D10               |
| stage-by-stage cost, with the measurement method     | ADR-0023 §Стадии           |
| binding grammar, `run:` / `at:` / `on_error:`        | ADR-0017 §3–4, ADR-0019 D2 |
| priority 15 and the in-lock ladder                   | ADR-0019 D2 / D9 / D11     |
| hook exit contract (0 / 2 / 126 / 127)               | ADR-0020 D2                |

Why a separate skill and not part of `git-mastery`: this is project-specific
(only this codebase has `tests/e2e/` + `tests/smoke/`), so it lives in
`usage/` per `rule_core_vs_usage_split`. Folding it into a core skill would push
a no-op hook onto every consuming project.

## References

- Scripts here: `git_hooks/pre-push-e2e-master.sh`, `hooks/{done,merge}-gate.sh`,
  `lib/{gate,gate-marker}.sh`
- Wrapper: `scripts/run-e2e-gate.sh`; compositions: `scripts/ci-local.sh`
- E2e tests: `tests/e2e/test_prepush_e2e_master_gate.py`,
  `tests/e2e/test_done_gate.py`, `tests/e2e/test_gate_primitive.py`
- Nesting guard: `tests/test_gate_entrypoint_parity.py`
- Plans: HATS-550 (origin), HATS-686 (decoupling), HATS-1137 / HATS-1614 (card gates)
