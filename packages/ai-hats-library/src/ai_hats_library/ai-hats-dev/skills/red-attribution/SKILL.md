---
name: red-attribution
description: Decide whose failure a red stage is before acting on it. Use when a gate stage, test tier or CI run goes red and the change may not be its cause, when master-ci refuses a close, and before reporting a suite green.
license: MIT
---

# red-attribution

A red stage asks two questions and the gate only prints one. "How do I fix this"
is second. **Whose failure is this** is first, and answering it wrong is how a
card spends its day on someone else's breakage — or reports green by shrinking
what it ran.

## When to Use

- A gate stage is red and the change did not touch what it exercises.
- `master-ci` refuses a close, or a whole tier is red on a long-lived branch.
- Before writing "green" anywhere: the claim needs the runner's own number over
  the run the gate would have done, not a subset.

## Procedure

**1. Re-check the base before blaming the diff.** The cheapest cause of a red
tier on a branch is a stale base, not the change. Merge or rebase onto current
master and run again. One card's 13 failures went to 1 that way — spend this
step before the expensive ones.

**2. Rule out the local venv.** Mass failures that die partway through are more
often a broken interpreter than a broken master. A venv fault looks exactly like
foreign redness and is fixed in a minute.

**3. Take a baseline in the SAME venv.** Run the same stage on the base commit,
same interpreter, same markers. A baseline from a different venv proves nothing:
one measured pair was 57 failures on the branch against 32 on master with large
unique sets *in both directions* — neither number meant what it looked like.

**4. Diff the failure LISTS, not the counts.** Equal counts hide a swap. The
usable verdict is a byte-identical list of node ids, or an explicit set
difference. `N failed` on both sides is not evidence.

**5. Name the owner of every line the branch did not add.** An open card per
failing node id — the card that broke it, or a new one. A line you cannot assign
is not classified yet; say that, rather than rounding it to "pre-existing".

**6. Report the classification. Do not act on it alone.** Say which failures are
yours (fix those), which are not, and who owns them. The decision to proceed on
someone else's red is the supervisor's — and the flag that encodes it comes from
the launching environment, never from your command line (`safety-guard` refuses
the prefix).

## Completion

- Every red line is either fixed, assigned to a card, or explicitly named as
  unassigned — no line is left as "probably pre-existing".
- Every green claim carries the runner's own exit code over the unnarrowed run,
  plus the log path.
- Validation — the false green: RED runs only the files the card touched, finds
  them passing, and reports the tier green while the tier is red. GREEN runs the
  stage the gate runs, takes a same-venv baseline, diffs the lists, and reports
  "N failures, M mine, K owned by card X" — a sentence the subset run cannot
  produce.

## Anti-Patterns

- **Narrowing the run until it is green.** The subset that passes is not the
  gate. This is the failure mode the skill exists for: a 32-minute tier once
  "passed" in 98 seconds this way.
- **"Probably pre-existing."** A guess wearing a verdict's clothes. Steps 3–4
  are cheap; skipping them is what makes the guess load-bearing.
- **Reading the exit code of a wrapper.** A pipeline reports its last stage and a
  background notice reports the whole command; both have announced a red tier as
  `0`. Take the number from the runner's own artefact.
- **`quarantine` for a broken test.** That marker means *flaky*, and it is
  subtracted from the tier everywhere including CI. Tagging a broken cohort with
  it silently redefines the marker and drops the floor for everyone.
- **Asking for the override instead of the classification.** The flag answers
  "may I proceed"; it was never the answer to "whose failure is this".
