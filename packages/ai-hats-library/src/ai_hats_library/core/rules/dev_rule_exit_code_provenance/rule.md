# Rule: The Exit Code You Read Must Be the Runner's

A test run answers one question: did it pass. That answer lives in the
**runner's** exit status. Any construct that puts something after the runner
hands you *that* thing's status instead, and a red gate reads green. The defect
is not a particular pipe or separator — it is **acting on a status that belongs
to something other than the runner**. The forms below are the common spellings,
not the boundary.

## The shape to write

```bash
# ✅ redirect, then judge — $? is the runner's:
pytest tests/ > /tmp/gate.log 2>&1
echo $?                      # a SEPARATE call, after the fact
# read /tmp/gate.log with another separate call

# ✅ if a pipeline is genuinely needed, make the status the runner's:
set -o pipefail; pytest tests/ | tail -20
pytest tests/ | tail -20; exit "${PIPESTATUS[0]}"

# ✅ && propagates failure — the runner's nonzero status survives:
pytest tests/ && ruff check .
```

## The foil to cut

```bash
# ❌ pipeline — the status is the filter's:
pytest tests/ | tail -20
pytest tests/ | tee /tmp/gate.log         # tee masks it too
pytest tests/ | grep -E 'failed|passed'

# ❌ compound command — the status is the trailing command's:
pytest tests/ > /tmp/gate.log 2>&1; echo "EXIT=$?"   # echo always succeeds
pytest tests/ ; ruff check .                         # only ruff's status survives
pytest tests/ || echo "some failed"                  # echo succeeds → green

# ❌ backgrounded / wrapped — you are shown the WRAPPER's status:
#    "Background command completed (exit code 0)" on a run where pytest returned 1
```

Measured, not remembered (`false` as the runner): `| tail` → 0, `| tee` → 0,
`> log; echo "E=$?"` → 0, `; true` → 0, `|| echo` → 0, **`&& true` → 1**,
`set -o pipefail; | tail` → 1, `| tail; exit ${PIPESTATUS[0]}` → 1.

## Before you act on a status, ask

1. Whose exit code is this — the runner's, or whatever happened to run last?
2. Is anything chained after the runner (`|`, `;`, `||`, a background or
   `timeout`-style wrapper)? → the status you get is not the runner's. `&&` is
   the exception: it passes a failure through.
3. Need the output too? → redirect to a file; read it in a **separate** call.
   Never filter on the same line you judge by.
4. It came back green — did I see the runner's own status, or a report about it?

## Source

HATS-1430. The guardrail this replaces enumerated pipeline forms
(`tail`/`head`/`grep`/`tee`) and was therefore silent on
`pytest … > /tmp/gate.log 2>&1; echo "EXIT=$?"` — the harness reported
"exit code 0" on a run where pytest returned 1, and a red gate read green
exactly as in the `tail` incident the guardrail was written for. Naming the
invariant instead of the flavors is the fix; a mechanical check in the
`PreToolUse` Bash hook is tracked separately.
