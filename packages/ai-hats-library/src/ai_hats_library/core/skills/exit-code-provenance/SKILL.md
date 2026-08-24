---
name: exit-code-provenance
description: Read a test or check run's verdict from the runner's own exit status, in the shell you are actually in. Use when a runner command carries a pipe, a trailing command or a background chain; when a status or log file is read back in a later call; when a guard nudges about exit-code masking; or when a run came back green and the number has to be trusted.
license: MIT
---

# Exit-Code Provenance

Two questions decide whether a green means anything: whose status is this, and
which run produced the artefact it came from.

## When to Use

Load this when the answer has to hold up — a merge, a `->done`, a claim to a
reviewer. For the constraint itself, `dev_rule_exit_code_provenance` is always
in context and needs no loading.

Not this skill: choosing Bash over Grep/Read (skill `tool-call-hygiene`), or
whether the gate covers the right files — provenance says the number is real,
never that the command was the right one.

## Conventions

### One mechanism, not a list of forms

A compound command's exit status is the status of the **last command it ran**.
Nothing misreports: not the pipe, not the harness, not a background wrapper —
each hands you that last status faithfully. When the last command is not the
runner, a red run reads green.

`&&` needs no exception: it short-circuits, so a failing runner *stays* last.

### Measured, not remembered

In the Bash tool's own shell (**zsh 5.9**, `$BASH_VERSION` empty), `false` as
the runner:

| command                              | status |    | command                            | status |
| ------------------------------------ | -----: | -- | ---------------------------------- | -----: |
| `false`                              |      1 |    | `false && true`                    |  **1** |
| `false; true`                        |      0 |    | `set -o pipefail; false \| tail`   |      1 |
| `false \| tail`                      |      0 |    | `false \| tail; exit "${PIPESTATUS[0]}"` |  **0** |
| `false \| tee log`                   |      0 |    | `false \| tail; exit "${pipestatus[1]}"` |      1 |
| `false \|\| true`                    |      0 |    | `false > log; echo $? > rc`        | 0, `rc`=1 |

Re-measure in your own shell rather than trusting this table: `echo "$ZSH_VERSION / $BASH_VERSION"`.

### The pipeline spelling depends on the shell

`set -o pipefail` is correct in **both** bash and zsh — reach for it first.

`${PIPESTATUS[0]}` is **bash-only**. In zsh the name does not exist, so
`exit "${PIPESTATUS[0]}"` becomes `exit ""` and returns **0** — silently, with
nothing on stderr. The cure for a red run then reads green. zsh spells it
`${pipestatus[1]}`, indexed from 1.

### An artefact read as evidence proves it belongs to this run

A status file, a log, a cached build, a baseline from another branch — each
reads as evidence of a run that may not have produced it. A fixed path is
shared across runs *and* across sessions on the same machine.

Prove the run: delete the artefact at the head of the chain, so its absence
means "not finished" instead of an older run's green. Where deleting is not
possible, compare its mtime against the moment the run started.

### The shapes

```bash
# ✅ default — no status file at all; the harness reports the runner's code
pytest tests/ > /tmp/gate.log 2>&1        # read the log in a separate call

# ✅ when the verdict must outlive the call (backgrounded, long, or cut short)
rm -f /tmp/gate.rc; pytest tests/ > /tmp/gate.log 2>&1; echo $? > /tmp/gate.rc

# ✅ a pipeline that still judges the runner
set -o pipefail; pytest tests/ | tail -20

# ❌ the last command is the filter, the echo, or the `date` at the end
pytest tests/ | tail -20
pytest tests/ > /tmp/gate.log 2>&1; echo "EXIT=$?"
pytest tests/ > /tmp/gate.log 2>&1; echo $? > /tmp/gate.rc; date

# ❌ $? in a later tool call — each call is a fresh subshell, so it reads 0
```

## Completion

Before a verdict is reported, all four hold:

- [ ] The number came from the runner, not from whatever ran last.
- [ ] The spelling is right for the shell in use — measured, not assumed.
- [ ] Every artefact cited postdates the change it describes, or was deleted
      before the run that wrote it.
- [ ] The output was read in a separate call from the one that judged it.

**Validation scenario (RED).** An agent is asked to confirm a suite is green
after a fix, in a session where `/tmp/gate.rc` exists from an earlier run.
Without this skill it writes `pytest … | tail -20`, or reads the pre-existing
`/tmp/gate.rc`, and reports green — the observed HATS-1430, HATS-1453 and
HATS-1783 incidents. GREEN: it redirects, reads the harness's own status, and
where a file is needed deletes it first — reporting the runner's number, or
"the run has not finished" when the file is absent.

## Anti-Patterns

- Filtering and judging on one line — `| tail`, `| tee`, `| grep` all put
  themselves last. Redirect, then read in a separate call.
- Copying `exit "${PIPESTATUS[0]}"` from a bash example into a zsh shell: it
  returns 0 for every run, red ones included.
- Reading a status file whose age nobody checked.
- Reporting an adjective ("green", "clean") instead of the number.
