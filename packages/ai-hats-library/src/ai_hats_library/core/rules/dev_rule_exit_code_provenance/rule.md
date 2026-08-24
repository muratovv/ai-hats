# Rule: The Exit Code You Read Must Be the Runner's

A run answers one question: did it pass. That answer lives in the **runner's**
exit status, and one mechanism decides whether you got it: a compound command's
status is the status of the **last command it ran**. Nothing misreports — not
the pipe, not the harness, not a background wrapper; each hands you that last
status faithfully. When the last command is not the runner, a red run reads
green. (`&&` needs no exception: it short-circuits, so a failing runner stays
last.)

```bash
# ✅ default — no status file; the harness reports the runner's own code:
pytest tests/ > /tmp/gate.log 2>&1        # read the log in a SEPARATE call

# ✅ a pipeline that still judges the runner (correct in bash and zsh):
set -o pipefail; pytest tests/ | tail -20

# ❌ the last command is the filter or the trailing command, and it succeeded:
pytest tests/ | tail -20
pytest tests/ > /tmp/gate.log 2>&1; echo "EXIT=$?"
```

Two further constraints, because each has already turned a red gate green here:

- **`${PIPESTATUS[0]}` is bash-only.** Where the shell is zsh — the Claude Code
  Bash tool included — the name does not exist, so `exit "${PIPESTATUS[0]}"`
  becomes `exit ""` and returns **0**, silently. Prefer `set -o pipefail`.
- **An artefact read as evidence must belong to this run.** A status file, a
  log, a cached build or a baseline from another branch all read as evidence of
  a run that did not produce them. Persist a status only when the verdict must
  outlive the call, and `rm -f` it at the head of the chain — an absent file is
  loud, a stale green is not.

`$?` does not survive between tool calls: each is a fresh subshell, so a later
`echo $?` reads 0 whatever the run did.

For the measured status table, the shell-by-shell spellings, and the
before-you-report checklist → skill **exit-code-provenance**.

## Source

HATS-1430, HATS-1453, HATS-1798 — a pipeline verdict, a trailing `echo`, a
two-day-old status file, and a `PIPESTATUS` cure that returns 0 in the shell it
runs in. Detail and measurements live in the skill.
