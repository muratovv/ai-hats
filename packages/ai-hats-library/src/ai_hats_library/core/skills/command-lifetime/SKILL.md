---
name: command-lifetime
description: PreToolUse hook refusing shell commands with no upper bound on their lifetime — unbounded while/until loops and unbounded background launches — and nudging on long-running ones. Infrastructure; read it to diagnose a refusal, not to invoke it.
ai_hats:
  runtime_hooks:
    PreToolUse:
      - matcher: Bash
        script: hooks/pre_bash_lifetime_guard.sh
license: MIT
---

# Command Lifetime

Nothing bounds how long a shell command launched on the agent's behalf stays
alive. This skill carries the one guard that does.

## Why the obvious bounds don't cover it

Two mechanisms look like they already solve this, and neither fires where the
problem is:

- **The Bash tool's `timeout`** bounds the *call*, not the process. A foreground
  command that exceeds its budget is *moved to the background*, not stopped.
- **`bounded_proc_shutdown`** (`src/ai_hats/pty_shutdown.py`) reaps the
  provider's process group — when the **session ends**.

Both are correct. Both are absent from a long-lived session, which is the
session a person is sitting in front of. Field evidence: three wait-loops alive
21 h 25 m, 83 minutes of CPU between them, found by hand in `ps`.

## What the guard does

| Shape                                                              | Verdict  |
| ------------------------------------------------------------------ | -------- |
| `while` / `until` with no `timeout`, no read-until-EOF, no counter | **deny** |
| `run_in_background: true` with no `timeout`                        | **deny** |
| install / fetch / image pull with no `timeout`                     | nudge    |
| everything else                                                    | silent   |

The split is not severity, it is whether advice has anything to appeal to. An
unbounded loop cannot terminate on its own; a long command finishes. A deny on
the third row would fire on routine work, and a gate that cries wolf gets
switched off — worth less than the nudge it replaced.

## Diagnosing a refusal

The refusal names the bounded spelling. Take it literally:

```bash
# refused — nothing stops this
until grep -q done /tmp/run.log; do :; done

# bounded by wall-clock; 124 tells you the bound was hit
timeout 300 bash -c 'until grep -q done /tmp/run.log; do sleep 5; done'

# or don't write the loop — Monitor is built for waiting on a condition
```

Three shapes are allowed without a `timeout`, because each already ends:
`while read … done < file` (EOF), a loop carrying a counter (`++`, `+=`,
`=$((`), and any `for … in <list>` (the guard never looks at those).

## Deliberately long-lived processes

`AI_HATS_LIFETIME_ACK=1` relaxes the refusal. It must be in the environment that
**launched the agent** — this hook runs before the command it judges is a
process, so `AI_HATS_LIFETIME_ACK=1 <command>` never reaches it. Taking the
hatch is recorded in the bypass journal. It is the supervisor's to set, not the
agent's.

## Why the guard does not add the `timeout` itself

It could — `updatedInput` is a real capability here; `safety_gate.py` already
answers `ask` plus a rewrite in one reply. The guard refuses instead, so the
number in `timeout N` is one the agent chose. A bound nobody picked produces an
exit 124 nobody can interpret.

## Anti-Patterns

- Reading this skill to decide whether to write a loop — it is infrastructure;
  the guard speaks when it needs to.
- Adding a second nudge here for test runners — `tool-call-hygiene` already owns
  that surface, and two nudges about one `pytest` run is how a channel gets
  tuned out.
- Setting `AI_HATS_LIFETIME_ACK` to get past a refusal. The hatch answers a
  supervisor's deliberate long-lived process; the refusal usually means the
  bound is genuinely missing.
