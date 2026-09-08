---
name: prompt-authoring
description: Decide whether a misbehaviour is curable by prose at all, then write the prose so it holds. Use before wording or rewording any rule, injection or skill body, and when a written instruction has failed to change behaviour.
license: MIT
---

# Prompt Authoring

Prose is the mechanism you reach for last. Earn it first, then write it to hold.

## When to Use

The ranking of mechanisms by cost is the ladder in trait `skill-engineer`; this
skill answers the question the ladder leaves open — whether THIS defect is one
prose can fix — and then how to write the words. Structure, description and
validation scenario of the file you end up writing are `skill-template`.

## Procedure

### 1. Establish what the agent actually had

Before touching a word, separate three states. They look identical in the
symptom and demand opposite fixes.

| What the evidence shows                                        | What it means            | The move                    |
| -------------------------------------------------------------- | ------------------------ | --------------------------- |
| The instruction was **not in the prompt**                       | the agent never knew     | prose — write it            |
| It was in the prompt but **never reached** (unloaded skill body, a doc the project does not have, a section for a different stage) | a DELIVERY defect        | move it to the point of use, do not reword |
| It was present, reached, and **selectively applied**            | prose is refuted here    | automate — go back to the ladder |

Presence is settled by reading the render (**composition-verification**), never
by remembering what the file says. Reaching is settled by the transcript: did
the agent quote it, act on part of it, or work in that section at all?

The third row is the one that costs sessions. A measured case: an agent applied
the design half of a skill it had plainly read and skipped the operational half
of the same document — the words were on the path and did not bind. Strengthening
those words is the move the evidence has already refuted.

### 2. If prose is refuted, say so and climb the ladder

"Tried prose, it did not hold" is a **result**, not a failure to be papered over
with firmer wording. Record it where the next author will meet it, and take the
rung above: a hook, a gate, a pipeline step. A machine that refuses the action
does not depend on the agent reading anything.

### 3. If prose is earned, write the invariant — not the enumeration

A list of forbidden spellings is refuted by the first spelling not on it, and
the agent that finds one has been given permission. Name the property that makes
any of them wrong, then let the list serve as illustration:

> ❌ "Do not use `cat`, `head`, `tail`, `sed -n`."
> ✅ "Reach for the narrowest tool the session offers; drop to the shell only
>    when none of them expresses the operation — `cat`, `head` and `sed -n` are
>    the common spellings, not the boundary."

### 4. Put it where the decision is made

An instruction read at the wrong moment is not read. Prose that guides a choice
belongs at the point of that choice — in the always-on layer if the agent must
not be able to reach the decision without it, in a skill body if it can be
fetched once the decision is recognized. Which layer, and what it costs per
turn, is the ladder's question.

### 5. Show the foil, and the cost of being wrong

A rule with no counter-example gets rationalized into compliance with whatever
the agent already intended. Pair the shape to write with the shape to cut, and
state what breaks when it is ignored — a consequence is harder to argue with
than an imperative.

### 6. Re-read it as an agent looking for a way out

Last pass, adversarial: can this be read as advisory? Does it leave a
"when in doubt" that resolves to the old behaviour? Does an unlisted case fall
outside it? Close those before committing — that is what step 3 buys you, and
what a rewrite that only adds emphasis never does.

## Completion

- Which of the three states in step 1 applies is stated, with its evidence.
- Prose was written only for state one or two; state three went to the ladder.
- The text names an invariant, sits at the point of use, and carries a foil.
- One adversarial re-read was done and its loopholes closed.

**Validation scenario (RED).** A recurring defect keeps appearing. The agent
opens the rule that already forbids it and strengthens the wording — "never,
under any circumstances" — then reports the fix. The transcript shows the agent
had read that very rule in the failing session and acted against it, so the
reworded rule fails identically next time, having cost a release cycle to learn
what step 1 would have said in one reading.

## Anti-Patterns

- Rewording an instruction the evidence shows was read and ignored.
- Answering a delivery defect with better wording instead of better placement.
- Enumerating the forms of a violation instead of naming what makes them wrong.
- Adding emphasis — caps, "MUST", "never ever" — as the substance of a revision.
- Writing the rule without the foil, then wondering why it was argued around.
