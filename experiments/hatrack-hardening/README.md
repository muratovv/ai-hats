# hatrack-hardening — A/B for the HATS-1051 skill edits

Does the hardened hatrack skill (FSM edge token + explicit per-edge policy
table, HATS-1051) change subagent lifecycle behavior versus the master skill?

**Arms** (shared, wired per sub-experiment via a `arms -> ../arms` symlink):

| Arm   | `skills/hatrack/SKILL.md`                                          |
| ----- | ------------------------------------------------------------------ |
| `old` | master baseline (137 L, prose FSM, **no** `{{backlog_fsm_edges}}`) |
| `new` | HATS-1051 edit (157 L, FSM token + per-edge policy, two sections)  |

Both arms run the **same engine**, which must include the HATS-1051 S2 token
substitution — the `new` arm's SKILL.md carries the raw `{{backlog_fsm_edges}}`
token on disk and relies on the engine to render it. Point the runner at a venv
built from this worktree so the sandbox runs that engine:

```bash
AI_HATS_EXP_VENV="$PWD/.venv"   # a venv with this worktree editable-installed
```

Each sub-experiment is a self-contained experiment for the `_lib` runner (one
scenario = one decision point):

| Sub-experiment | Decision isolated                                     | Runnable                                       | Score                                                                             |
| -------------- | ----------------------------------------------------- | ---------------------------------------------- | --------------------------------------------------------------------------------- |
| `advance/`     | execute work done → advance to review and **wait**    | yes                                            | `advanced-to-review.sh` (state == review) + `drove-review-via-rack.sh` (tool_use) |
| `control/`     | work **not** done → must **not** transition           | yes                                            | `not-advanced.sh` (state == execute)                                              |
| `rework/`      | review WITH comments → rework loop (review→execute→…) | **no** — gated on HATS-1052 (`NONRUNNABLE.md`) | `back-in-review.sh` (state == review)                                             |

## Run (per the HATS-1053 infra)

```bash
# wiring smoke — N=1/arm on a runnable scenario, cheap tier:
AI_HATS_EXP_VENV="$PWD/.venv" experiments/_lib/run.sh experiments/hatrack-hardening/advance new 1 claude-haiku-4-5
AI_HATS_EXP_VENV="$PWD/.venv" experiments/_lib/run.sh experiments/hatrack-hardening/advance old 1 claude-haiku-4-5
experiments/_lib/report.sh experiments/hatrack-hardening/advance
experiments/_lib/clean.sh  experiments/hatrack-hardening/advance
```

`budget.usd` = 5.00 per sub-experiment. `runs/` is gitignored (raw session
recordings — never commit).

## S5b (gated, NOT this execute)

The paid opus/haiku batches with adaptive N are HATS-1051 S5b — run after review
and after HATS-1052 lands, on the supervisor's go. Tier policy per the HATS-1053
finding: `advance`/`control` primary on haiku (the differential shows there;
opus saturates), `rework` on opus AND haiku. Attach the `report.sh` output
(per-arm rates + dispersion + cost) to the HATS-1051 card.
