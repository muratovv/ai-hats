# Rule: The Exit Code You Read Must Be the Runner's

A test run answers one question: did it pass. That answer lives in the
**runner's** exit status. One mechanism explains every form below: the status of
a compound command is the status of the **last command it ran**. Nothing lies to
you — not the pipe, not the harness, not a background wrapper; each hands you
that last status honestly, and when the last command is not the runner, a red
gate reads green. The forms below are the common spellings, not the boundary.

## The shape to write

```bash
# ✅ default — NO status file: the harness reports the runner's own exit code,
#    and the log is read in a separate call:
pytest tests/ > /tmp/gate.log 2>&1

# ✅ only when the verdict must OUTLIVE the call (backgrounded, long, or a run
#    the harness may cut short) — delete first, so the file can only be this
#    run's:
rm -f /tmp/gate.rc; pytest tests/ > /tmp/gate.log 2>&1; echo $? > /tmp/gate.rc
# no file = this run has not finished. Never an older run's green.

# ✅ if a pipeline is genuinely needed, make the runner the last thing judged:
set -o pipefail; pytest tests/ | tail -20     # works in bash AND zsh — prefer it

# ✅ && needs no exception — it short-circuits, so a failing runner IS the last
#    command run:
pytest tests/ && ruff check .
```

**`${PIPESTATUS[0]}` is bash-only, and this matters here.** The Bash tool runs
**zsh 5.9** (`$BASH_VERSION` is empty), where that name does not exist:
`pytest … | tail; exit "${PIPESTATUS[0]}"` becomes `exit ""` and returns **0** —
silently, nothing on stderr. A red run reads green through the very line written
to prevent it. zsh spells it `${pipestatus[1]}`, indexed from 1. Prefer
`set -o pipefail`, which is correct in both shells; reach for either
`pipestatus` spelling only when you know which shell you are in.

## The foil to cut

```bash
# ❌ pipeline — the last command is the filter:
pytest tests/ | tail -20
pytest tests/ | tee /tmp/gate.log         # tee is last, and it succeeds
pytest tests/ | grep -E 'failed|passed'

# ❌ compound command — the last command is whatever you appended:
pytest tests/ > /tmp/gate.log 2>&1; echo "EXIT=$?"   # echo is last, always 0
pytest tests/ ; ruff check .                         # only ruff's status survives
pytest tests/ || echo "some failed"                  # echo is last → green

# ❌ backgrounded / long chains — SAME rule, not a special case: a chain ending
#    in `date` returns 0 while the runner's real 1 sits in a file
pytest tests/ > /tmp/gate.log 2>&1; echo $? > /tmp/gate.rc; date

# ❌ separate tool call for $? — subshell state resets to 0:
#    call 1: pytest tests/ > /tmp/gate.log 2>&1
#    call 2: echo $?   # fresh subshell -> prints 0 even if pytest failed!

# ❌ a status file that outlived the run it is read against — the verdict is
#    some other run's, and it reads exactly like this run's:
pytest tests/ > /tmp/gate.log 2>&1; echo $? > /tmp/gate.rc   # no leading rm -f
#    read after a timeout, from a second session, or while the run is still
#    going, /tmp/gate.rc answers green from a run that is not yours
```

Measured in the Bash tool's own shell (zsh 5.9), `false` as the runner: bare
`false` → 1 (the harness reports it faithfully), `; true` → 0, `| tail` → 0,
`| tee log` → 0, `|| true` → 0, **`&& true` → 1**, `> log; echo $? > rc` → 0
with 1 in the file, `set -o pipefail; | tail` → 1,
**`| tail; exit "${PIPESTATUS[0]}"` → 0**, `| tail; exit "${pipestatus[1]}"` → 1.

## Before you act on a status, ask

1. What ran **last**? That is the status you were handed. If it is not the
   runner, this number says nothing about the run.
2. Is anything appended after the runner (`|`, `;`, `||`, another command in a
   backgrounded chain)? → the status is that command's. `&&` needs no exception:
   it short-circuits, so a failing runner stays the last command run.
3. Is this across tool calls? Shell state (`$?`) is NOT preserved across separate
   Bash tool calls (each tool call is a fresh subshell process, so `$?` in a new
   call is always `0`). Rely on the harness status report
   (`The command exited with code X`); persist the status to a file only when it
   must outlive the call, and then `rm -f` that file at the head of the chain.
4. Is this artefact proof that THIS run produced it? A status file, a log, a
   cached build or a baseline from another branch all read as evidence of a run
   that did not produce them. Delete before the run, or check its mtime against
   the run's start — an absent file is loud, a stale green is not.
5. Need the output too? → redirect to a file; read it in a **separate** call.
   Never filter on the same line you judge by.
6. It came back green — did I see the runner's own status, or a report about it?

## Source

HATS-1430, HATS-1453, HATS-1798. The guardrail this replaces enumerated pipeline
forms (`tail`/`head`/`grep`/`tee`) and was therefore silent on
`pytest … > /tmp/gate.log 2>&1; echo "EXIT=$?"` — the harness reported
"exit code 0" on a run where pytest returned 1, and a red gate read green
exactly as in the `tail` incident the guardrail was written for. Naming the
invariant instead of the flavors is the fix; HATS-1453 further clarified
tool-call boundary state isolation ($? resetting across separate subshell calls).

HATS-1798 made three corrections, each measured rather than recalled. The rule
prescribed a fixed `/tmp/gate.rc`, and such a file was read two days after it was
written — green, while the suite it described was still running; in a foreground
call that file is redundant, and where it is not, it must prove it belongs to
this run. The mechanism was described as a wrapper or a harness misreporting;
it is the POSIX last-command rule, which explains every form at once and retires
`&&` as a special case. And the prescribed `exit "${PIPESTATUS[0]}"` does not
exist in the zsh the Bash tool actually runs, where it silently evaluates to
`exit ""` → 0.
