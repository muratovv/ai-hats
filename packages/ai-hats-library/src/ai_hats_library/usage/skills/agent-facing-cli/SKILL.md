---
name: agent-facing-cli
description: Write tool output an agent can act on — refusals that carry their own remedy, honest exit codes, visible truncation. Use when building or changing a hook, gate, or CLI whose reader is an agent, and when a tool was ignored, misread, or worked around.
license: MIT
---

# Agent-Facing CLI

The agent has your output and nothing else. Put the fix in it.

## When to Use

This is the other half of choosing automation. `prompt-authoring` argues that a
machine should hold the invariant; this skill is how that machine talks, because
a gate whose refusal cannot be acted on gets worked around, and a worked-around
gate is worse than none — it taught the reader to scroll past.

Not about human ergonomics. Colour, alignment and progress bars are not the
subject; what the reader can DO next is.

## Procedure

### 1. A refusal is an action, not a diagnosis

Name what is missing, then the one command that earns it — and put the command
**last**, because a reader arriving at the tail sees the last line first.

```
review-gate: tree 5d6d1b9e (HEAD) has not earned every stage this gate requires.

Missing:
    lint
    unit

Run the gate on that exact content, then retry:

    scripts/gates.sh run lint unit
```

What makes it work is not the layout: it is that the reader can copy one line
and be further along. A message that explains the policy and stops has moved the
problem to the reader's memory.

### 2. Every remedy must run AS PRINTED

The recurring defect is not a missing remedy — it is a remedy that does not
work. Measured shapes, each of which cost real sessions:

- a hint naming a **verb the CLI does not have**;
- a teardown refusal advising a **flag that command never accepted**;
- a remedy whose recipe was **incomplete**, so following it exactly still failed;
- a guard that **instructed a form it then refused** — an environment assignment
  prefixed onto the very command the guard inspects, which cannot reach a
  process that does not exist yet.

So: run your own remedy, from the state that triggers it, before shipping the
message. A remedy is executable content and rots like code, not like prose.

### 3. Deliver the context, do not cite it

The agent has your terminal, not your documentation. A message that says "see
the contributing guide" costs a search that may not resolve — the doc ships with
your repository, not with the tool. Inline what the reader needs; keep the
citation for depth beyond the immediate fix.

### 4. Truncation announces itself

A capped list with no footer teaches the reader a false total, and they act on
it. Cap loudly: say how many were withheld and the flag that shows the rest.
Silence here is the same defect as a silent fallback, one layer out.

### 5. The exit code is the contract

- Return the **runner's own** status, never a filter's or a wrapper's.
- A run that produced no artifact must not exit 0 because the failure was
  "expected" — a green code with a "nothing was written" line in the body is a
  contradiction the reader resolves in favour of the code.
- Fail-open is a decision, not an accident: when a check cannot run, say so on
  the channel someone reads and journal it. A guard that goes quiet when its
  dependency is missing has stopped guarding without telling anyone.

### 6. Say the invariant, not the catalogue

A refusal that enumerates forbidden spellings teaches the reader to find an
unlisted one. Name the property that makes the action wrong; the list is
illustration. This is `prompt-authoring` § "write the invariant" applied to the
message a machine prints.

### 7. Keep one home for the policy

The message the tool prints IS the documentation of its rule. Restating that
rule in prose elsewhere gives it two homes, and the copy drifts silently — a
guardrail described as stricter than it behaves is how a reader learns to
distrust both. Point at the tool; let it speak.

## Completion

- The refusal names what is missing and ends with one runnable command.
- That command was executed from the failing state, and worked.
- Nothing essential is behind a citation the reader may not be able to open.
- Caps and skips are visible; the exit code matches the body.
- The rule has one home, and it is this output.

**Validation scenario (RED).** A gate refuses with an accurate explanation of
its policy and no command. The agent, holding a red tool and no next step,
reruns it with a flag it guessed, gets the same refusal, and proceeds around the
gate — reporting the work as done. The gate was correct throughout and changed
nothing, because being right and being actionable are different properties.

## Anti-Patterns

- A refusal that explains why and not what to do.
- A remedy nobody ran: a verb, flag, or path that does not exist.
- Pointing at a document instead of inlining the fix.
- A silent cap, or a fail-open path that journals nothing.
- Exit 0 on a run whose body says nothing was produced.
- The tool's rule restated in prose that will drift from it.
