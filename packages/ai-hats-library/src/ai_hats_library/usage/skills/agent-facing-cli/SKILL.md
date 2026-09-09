---
name: agent-facing-cli
description: Write tool output an agent can act on — a guard precise enough to be worth reading, a refusal carrying its own remedy, an honest exit code. Use when building or changing a hook, gate, or CLI whose reader is an agent, and when a tool was ignored, misread, or worked around.
license: MIT
---

# Agent-Facing CLI

The agent has your output and nothing else. Earn the reading, then put the fix in it.

## When to Use

This is the other half of choosing automation. `prompt-authoring` decides that a
machine should hold the invariant; this skill is how that machine behaves toward
its reader.

Not about human ergonomics. Colour, alignment and progress bars are not the
subject; what the reader can DO next is.

## Procedure

### 1. Precision before eloquence

The failure this project actually records is not a terse refusal — blocking
guards are honoured routinely. It is a guard that fires on what it should not.
A gate that flags the very command it recommends, or warns on every call of a
tool that is fine, teaches the reader to stop reading it — and the guard that
gets scrolled past is then unavailable for the real error it was built for.

So the first question about output is not its wording. It is: **does this fire
only when it should?** A false positive is not a cosmetic defect; it spends the
reader's attention, which is the resource every later rule here depends on.

### 2. A refusal is an action, not a diagnosis

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

What makes it work is that the reader can copy one line and be further along. A
message that explains the policy and stops has moved the problem into the
reader's memory.

### 3. Every remedy must run AS PRINTED

The recurring defect here is not a missing remedy — it is a remedy that does not
work. Measured shapes, each of which cost real sessions:

- a hint naming a **verb the CLI does not have**;
- a teardown refusal advising a **flag that command never accepted**;
- a remedy whose recipe was **incomplete**, so following it exactly still failed;
- a guard that **instructed a form it then refused** — an environment assignment
  prefixed onto the very command the guard inspects, which cannot reach a
  process that does not exist yet;
- a message claiming state it had not left behind ("worktree left intact for
  retry", when it was not).

Run your own remedy, from the state that triggers it, before shipping the
message. A remedy is executable content and rots like code, not like prose.

### 4. The exit code is the contract

This is the best-evidenced rule here, and the one most often broken.

- Return the **runner's own** status, never a filter's or a wrapper's.
- A run that produced no artifact must not exit 0 because the failure was
  "expected" — a green code above a "nothing was written" line is a
  contradiction the reader resolves in favour of the code.
- A gate that cannot see the thing it judges must not report passing. A vacuous
  green is worse than red: it is consumed as evidence.
- Fail-open is a decision, not an accident: when a check cannot run, say so on a
  channel someone reads and journal it — and keep the journal free of your own
  test traffic, or it stops being readable as evidence.

### 5. Say the invariant, not the catalogue

A refusal that enumerates forbidden spellings teaches the reader to find an
unlisted one, and the unlisted ones outnumber the listed. Name the property that
makes the action wrong; the list is illustration. This is `prompt-authoring`
§ "write the invariant" applied to the message a machine prints.

### 6. Keep one home for the policy

The message the tool prints IS the documentation of its rule. Restating that
rule in prose elsewhere gives it two homes and the copy drifts — and when they
conflict, skill prose has been observed to win over the tool's own correct deny
text. Point at the tool; let it speak.

## Completion

- The guard fires only on what it means to catch, and you checked the negative
  cases, not only the positive ones.
- The refusal names what is missing and ends with one runnable command.
- That command was executed from the failing state, and worked.
- The exit code matches the body, and a check that could not run says so.
- The rule has one home, and it is this output.

**Validation scenario (RED).** A guard ships with a matcher that also catches a
legitimate spelling. It fires on nearly every session, correctly most times and
wrongly often enough to be noise; the agent learns to scroll past it. Weeks
later the same guard reports a real defect, in a correctly worded and actionable
message, and that message is scrolled past too. Nothing about the wording was
wrong — the reading had already been spent.

## Anti-Patterns

- Tuning the wording of a guard that fires when it should not.
- A refusal that explains why and not what to do.
- A remedy nobody ran: a verb, flag, or path that does not exist.
- Exit 0 on a run whose body says nothing was produced, or on a check that could
  not see its subject.
- A fail-open path that journals nothing — or a journal full of your own tests.
- The tool's rule restated in prose that will drift from it.
