# rework scenario — NON-RUNNABLE until HATS-1052

This scenario isolates the second observed failure: **review returned WITH
comments → rework**. The correct path is `review → execute → document →
review`, but the `review → execute` edge does **not exist** in the FSM yet — it
lands with **HATS-1052** (`brainstorm` as of authoring). Today `review` only
reaches `done`, `failed`, or `cancelled`, so no arm can succeed and running this
would only burn budget on a foregone failure.

`scenario/seed.sh` therefore **exits non-zero in `prepare`** unless
`HATS_1052_LANDED=1` is set — a hard gate so the runner refuses it by default.

## When HATS-1052 lands

1. Confirm the edge: `rack transition <id> execute` is legal from `review`, and
   that it does **not** trigger a worktree merge (rework keeps the worktree).
2. Re-run with the edge present:
   ```bash
   HATS_1052_LANDED=1 AI_HATS_EXP_VENV="$PWD/.venv" \
     experiments/_lib/run.sh experiments/hatrack-hardening/rework new 5 <model>
   ```
3. Expected differential (per the HATS-1051 policy table): the `new` arm knows
   the rework loop and returns the card to `review`; the `old` arm has no
   rework wording and stalls or mis-transitions. Score: `score/back-in-review.sh`.

Tier: opus AND haiku (novel behavior, no saturation expected — HATS-1053 tier
finding). This is part of the gated S5b batch, not the S5a wiring smoke.
