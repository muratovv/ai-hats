# Rule: The Exit Code You Read Must Be the Runner's

A run answers one question: did it pass, and the answer is the **runner's** exit
status. One mechanism decides whether you got it: a compound command's status is
the status of the **last command it ran**. Nothing misreports — not the pipe,
not the harness, not a background wrapper; each hands you that last status
faithfully, and when the last command is not the runner, a red run reads green.
A wrapper script is a compound command too: its status is its last stage's
unless it propagates one deliberately.

- `&&` needs no exception — it short-circuits, so a failing runner stays last.
- `$?` does not survive between tool calls: each is a fresh subshell, so a later
  `echo $?` reads 0 whatever the run did. Read the status the harness reports,
  or capture it inside the same call.
- **An artefact read as evidence must belong to this run.** A status file, a
  log, a cached build or a baseline from another branch all read as evidence of
  a run that did not produce them. Persist a status only when the verdict must
  outlive the call, and `rm -f` it at the head of the chain — an absent file is
  loud, a stale green is not.
- Report the **number**, not an adjective. "Green" is a belief; `0` is a
  measurement, and only if it came from the runner.

Pipelines: `set -o pipefail` is correct in bash and zsh both. `${PIPESTATUS[0]}`
is bash-only — in zsh the name does not exist, so `exit "${PIPESTATUS[0]}"`
becomes `exit ""` and returns 0 silently, for every run.

## Source

HATS-1430, HATS-1453, HATS-1798 — a pipeline verdict, a trailing `echo`, a
two-day-old status file, and a `PIPESTATUS` cure that returns 0 in the shell it
runs in. Measurements: `docs/adr/attachments/area-extraction-notes.md`.
