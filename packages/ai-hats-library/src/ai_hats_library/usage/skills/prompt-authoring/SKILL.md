---
name: prompt-authoring
description: Choose the mechanism before the wording, then write prose that holds. Use before wording or rewording any rule, injection or skill body, and when a written instruction has failed to change behaviour.
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

### 1. Are you FORBIDDING something, or guiding it?

This is the fork, and it decides the mechanism before any wording question.

**To forbid — work the automation first.** If the goal is that an action must
not happen, a machine that refuses it is the answer, and prose is the fallback
you take only when there is no automatable invariant or when building it costs
more than the defect does. Say which, and name the cost; "seems hard" is not a
reason. Prose forbids nothing on its own: it asks, every turn, and one turn will
answer no.

**To guide — prose is the right instrument, not a concession.** Naming a
component so it is not invented, saying which of two paths applies, teaching
what a good answer looks like, holding a judgement no rule can express: no gate
does any of this, and reaching for one here builds a machine that refuses the
wrong thing.

Getting the fork wrong is expensive in both directions. Wording a prohibition
buys a rule that is argued around; gating a judgement buys false refusals, and
those cost more than they look — see `agent-cli-guidelines` § "Fire only when
you should".

### 2. What a prompt that already failed tells you

If the instruction was in the prompt and the behaviour happened anyway, that is
a measurement. What it measures depends on step 1.

**For a prohibition, it is evidence for automating.** Saying it again, louder,
is the move the evidence has already refuted. Climb, and record that prose was
tried so the next author does not spend the same session.

**For guidance, it is evidence about the SHAPE of the text, not about prose as
a mechanism.** Three rewrites have a record of working on text that had already
failed once, and they are the only three: moving it to where the decision is
made; replacing a citation with the content itself; and rewriting an enumeration
into the invariant behind it. What has never worked is adding another bullet or
adding emphasis. If your revision is not one of the three, it is the fourth
thing, and it has no record.

Then narrow it further. Three states look identical in the symptom and demand
opposite fixes.

| What the evidence shows                                                                                                            | What it means           | The move                                                                  |
| ---------------------------------------------------------------------------------------------------------------------------------- | ----------------------- | ------------------------------------------------------------------------- |
| The instruction was **not in the prompt**                                                                                          | the agent never knew    | prose — write it                                                          |
| It was in the prompt but **never reached** (unloaded skill body, a doc the project does not have, a section for a different stage) | a DELIVERY defect       | move it to the point of use, do not reword                                |
| It was present, reached, and **selectively applied**                                                                               | this text does not bind | a prohibition goes to automation; guidance gets one of the three rewrites |

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

The third row is the one that costs sessions, and it has a shape worth
recognizing: an agent applies one half of a document it plainly read and skips
the other — the words were on the path and did not bind. That observation is a
single uncontrolled session, so treat it as a pattern to look for, not as a
settled frequency; what is settled is the move it rules out, which is saying the
same thing more firmly.

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

Two shapes fail this on their own, and `skill-template`'s validation checklist
already refuses both — "target behaviour stated positively" and "every omission
is a decision". Audit against it rather than against a copy here; what belongs
in this skill is why they bite: an agent told only what NOT to do picks its own
second choice, and every silence is delegated to model priors, which is exactly
where the old behaviour lives. The step-2 default for always-on text is that
second rule applied to this skill's own hardest branch.

### 6. Re-read it as an agent looking for a way out

Last pass, adversarial: can this be read as advisory? Does it leave a
"when in doubt" that resolves to the old behaviour? Does an unlisted case fall
outside it? Close those before committing — that is what step 3 buys you, and
what a rewrite that only adds emphasis never does.

## Completion

- Step 1's fork is answered out loud: forbidding, or guiding. For a prohibition,
  automation was ruled out by no automatable invariant or by a named cost that
  loses — "clearer wording" is not a reason and does not appear here.
- Which of the three states in step 2 applies is stated, with its evidence; and
  a revision to text that already failed is one of the three rewrites with a
  record, not a fourth thing.
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
