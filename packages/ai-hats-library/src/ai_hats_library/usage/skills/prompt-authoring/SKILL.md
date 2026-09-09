---
name: prompt-authoring
description: Rule automation out before writing prose, then write prose that holds. Use before wording or rewording any rule, injection or skill body, and when a written instruction has failed to change behaviour.
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

### 1. Automate unless you cannot — prose needs an excuse

The first question is never "how do I word this". It is **can a machine hold
this invariant** — a hook, a gate, a pipeline step. If it can, that is the
answer, and the comparison is not close: automation costs nothing per turn and
does not depend on the agent reading anything.

Prose is the fallback, and exactly two answers earn it:

- **There is no automatable invariant.** The call is a genuine judgement — no
  rule over the command, the diff, or the file expresses it.
- **The automation is disproportionate.** It exists, and it costs more than the
  defect does. Name what building it would take and why it loses; a number beats
  "seems hard".

"The wording could be clearer" is not one of them. It is the cheapest-looking
move, which is why it is the one reached for when neither answer above is true.

### 2. A prompt that already failed is evidence FOR automating

If the instruction was in the prompt and the behaviour happened anyway, that is
a measurement, not a wording problem: prose does not bind here. Rewording is the
move the evidence has already refuted — climb, and say in the card that prose
was tried, so the next author does not spend the same session.

Only once automation is genuinely off the table does it matter which kind of
failure you had. Three states look identical in the symptom and demand opposite
fixes, and two of them still leave prose on the table.

| What the evidence shows                                                                                                            | What it means         | The move                                   |
| ---------------------------------------------------------------------------------------------------------------------------------- | --------------------- | ------------------------------------------ |
| The instruction was **not in the prompt**                                                                                          | the agent never knew  | prose — write it                           |
| It was in the prompt but **never reached** (unloaded skill body, a doc the project does not have, a section for a different stage) | a DELIVERY defect     | move it to the point of use, do not reword |
| It was present, reached, and **selectively applied**                                                                               | prose is refuted here | automate — go back to the ladder           |

Presence is settled by reading the render (**composition-verification**), never
by remembering what the file says. Reaching is settled by the transcript: did
the agent quote it, act on part of it, or work in that section at all?

**The default when the transcript is silent.** Rows two and three look identical
for always-on text — a rule or an injection leaves no load event to point at, so
"never reached" can never be shown. Resolve it by construction: always-on prose
IS reached every turn, so a violated rule with no positive evidence of partial
application is row three, not row two. Row two is available only for content that
had to be fetched — a skill body, a doc, a section belonging to another stage —
where the transcript can show the fetch never happened. Without this default the
cheaper fix wins by silence, and rewording ships again.

The third row is the one that costs sessions. A measured case: an agent applied
the design half of a skill it had plainly read and skipped the operational half
of the same document — the words were on the path and did not bind. Strengthening
those words is the move the evidence has already refuted.

### 3. If prose is earned, write the invariant — not the enumeration

A list of forbidden spellings is refuted by the first spelling not on it, and
the agent that finds one has been given permission. Name the property that makes
any of them wrong, then let the list serve as illustration:

> ❌ "Do not use `cat`, `head`, `tail`, `sed -n`."
> ✅ "Reach for the narrowest tool the session offers; drop to the shell only
> when none of them expresses the operation — `cat`, `head` and `sed -n` are
> the common spellings, not the boundary."

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

Two shapes that fail this on their own:

- **Prohibition without a replacement.** State the target behaviour positively;
  a bare "do not X" survives only as a hard guardrail, and only when it names
  what to do instead ("redirect instead: `pytest > /tmp/gate.log`"). An agent
  told only what not to do picks its own second choice.
- **Unowned silence.** Every decision the text leaves unstated is delegated to
  model priors, which is where the old behaviour lives. Make each omission
  deliberate: fill it, or mark it an open question — the three-state default in
  step 2 is that move applied to this skill's own hardest branch.

### 6. Re-read it as an agent looking for a way out

Last pass, adversarial: can this be read as advisory? Does it leave a
"when in doubt" that resolves to the old behaviour? Does an unlisted case fall
outside it? Close those before committing — that is what step 3 buys you, and
what a rewrite that only adds emphasis never does.

## Completion

- Automation was ruled out on the record, by one of the two answers in step 1 —
  no automatable invariant, or a named cost that loses. "Clearer wording" is not
  a reason and does not appear here.
- Which of the three states in step 2 applies is stated, with its evidence, and
  prose was written only for the first two.
- The text names an invariant, sits at the point of use, and carries a foil.
- One adversarial re-read was done and its loopholes closed.
- The prediction is handed to **library-change-hypothesis-protocol**: this skill
  argues prose is the right mechanism BEFOREHAND; only the check recorded there
  can show afterwards that it worked. Prose whose effect nobody will measure is
  the state-three defect being created rather than fixed.

**Validation scenario (RED).** A recurring defect keeps appearing. The agent
opens the rule that already forbids it and strengthens the wording — "never,
under any circumstances" — then reports the fix. The transcript shows the agent
had read that very rule in the failing session and acted against it, so the
reworded rule fails identically next time, having cost a release cycle to learn
what steps 1 and 2 would have said in one reading: a prompt that already failed
is an argument for automating, not for saying it louder.

## Anti-Patterns

- Rewording an instruction the evidence shows was read and ignored.
- Answering a delivery defect with better wording instead of better placement.
- Enumerating the forms of a violation instead of naming what makes them wrong.
- Adding emphasis — caps, "MUST", "never ever" — as the substance of a revision.
- Writing the rule without the foil, then wondering why it was argued around.
