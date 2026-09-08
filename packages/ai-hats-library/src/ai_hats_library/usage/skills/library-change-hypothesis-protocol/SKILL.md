---
name: library-change-hypothesis-protocol
description: After execute-stage commits land (diff finalized) and BEFORE the task transitions to done, file a companion HYP with an explicit verification_protocol — or record an explicit "no behavior change" note for refactors at plan-stage. NEVER file the HYP at plan-stage (precommitment anti-pattern).
license: MIT
---

# Library-Change Hypothesis Protocol

> **PoC status.** This skill is intentionally lightweight.
> The framework does NOT enforce companion-HYP filing — discipline lives
> here. If the PoC produces signal that curators skip the step, a future
> task may lift enforcement into the engine.

> **Shell prelude.** Where `ai-hats` is not on `PATH` (a source checkout
> without the console script), fall back to the module:
>
> ```bash
> ah() { if command -v ai-hats >/dev/null 2>&1; then ai-hats "$@"; else ./.venv/bin/python -m ai_hats "$@"; fi; }
> ```

## Timing — read this first

Two checkpoints, NOT one:

| Stage        | What you do here                                                                                                               |
| ------------ | ------------------------------------------------------------------------------------------------------------------------------ |
| **plan**     | Behavior-delta check only. Decide *whether* a HYP will be needed. Record decision in `plan.md`. **Do NOT create the HYP yet.** |
| **document** | Diff is final on `task/<id>` branch. **Now** create the HYP (`rack hyp create`), cross-link, commit.                           |

Filing the HYP at plan-stage is a **precommitment anti-pattern**.
The HYP must describe what shipped — not what was
*planned* to ship.

### Why post-ship, not plan-stage

1. **Falsifiability is bound to the real diff.** At plan stage the
   final shape of the change is not yet known: scope can drift,
   alternatives can be rejected mid-execute, wording can be rewritten.
   A plan-stage HYP describes *intent*, not *shipment*.
2. **Observation window opens at merge.** Until the change is on
   `master` (or at minimum frozen on the task branch), `validation_log`
   has nothing to accumulate. A plan-stage HYP sits `active` with no
   evidence — noise in the active-HYP inbox.
3. **Precommitment bias.** A plan-stage HYP starts pulling the
   implementation toward its own framing: «we already said
   Vertical-slicing-directive will reduce bulk-tests» makes it harder
   in execute to honestly say «this isn't enough — promote to a full
   skill». Post-ship HYPs are observers, not advocates.
4. **Orphan HYPs from cancelled tasks.** If a task dies in execute
   (cancelled, scope shifted to another card), a plan-stage HYP
   becomes a sibling artefact needing cleanup. Post-ship HYPs only
   exist when there's a real change to observe.
5. **Supervisor citation.** *«Why are we filing hypotheses now? They
   should be filed after the task is done.»* — raised at one task's
   closure thread and repeated at the next.

## When to Use

Triggered when transitioning a library-curation task from **execute**
to **document** — after the implementing commits exist and the diff is
final. Library-curation = any change to a component an agent composes:
a `roles/`, `traits/`, `skills/` or `rules/` entry under one of this
project's library roots (`<project>/libraries/`, `~/.ai-hats/`, or the
shipped library if you are editing that).

Skip when: the change is to engine or harness code rather than to a
composed component, or the change was already declared "no behavior
change — pure refactor" at the plan-stage check (see Step 1).

If this project has no hypotheses backlog yet, it is seeded on first
write by `ai-hats reflect issue`, which mounts it from the packaged
definition. Reaching for `rack hyp` first on such a project answers
`unknown backlog 'hypotheses'` — the backlog is discovered by scanning
the tracker, and nothing has created it yet.

## Why companion HYPs at all

Without a companion HYP, library-curation changes ship blind:

- Behavior-changing library edits have shipped with no HYP filed. No
  data tells us whether those changes worked.
- A HYP has been filed only because the user prompted for it. The
  discipline does not survive without a checklist.
- Even when HYPs exist, their `success_criterion` text is interpreted
  qualitatively — one was marked `confirmed` on a single audit cite
  against a "≥3 of 4 transitions" criterion. The criterion text alone
  did not constrain the auditor.

This skill addresses the producer side: every behavior-changing
library edit gets a companion HYP whose YAML carries a
**`verification_protocol`** field — explicit, free-form text telling
future auditors exactly what shape of `--evidence` to emit. The
consumer side (auditor follows the protocol) lives in
**review-hypothesis**.

## Procedure

### Step 1 — Behavior delta check (plan-stage)

In your task's `plan.md`, answer two questions explicitly:

1. **Prior behavior?** What did agents observably do before this edit?
2. **Post-change behavior?** What should they observably do after?

Both "no observable change" → **pure refactor**. Record the decision
explicitly in `plan.md` or via `rack transition --log` (one line:
"no behavior change — pure refactor: <reason>"). Skip the rest of this
skill — no HYP needed, now or later.

Otherwise → continue to Step 2 **at document-stage**, not now.

### Step 2 — Author the HYP (document-stage, AFTER final commit)

**Precondition:** the final implementing commit exists on
`task/<id>` and the task has been transitioned `execute → document`.
If you find yourself reaching for `hyp create` while the task is still
`plan` or `execute`, **stop** — you're committing the precommitment
anti-pattern.

**First — is it already covered by an active HYP?** Check the active
hypotheses (the reviewer's evidence block lists them, or `rack context
HYP-NNN` per candidate) for one that already describes this change's
mechanism. If one does,
**do not file a new HYP** — that duplicates the backlog (the same
umbrella-first discipline as self-retrospective 4.5.a). Instead **fold
into it**: append an intervention marker to that HYP's `validation_log`
and cross-link both ways (task resolution names the HYP; the marker
names the task + sha):

```bash
rack hyp append-verdict HYP-NNN --session-id <id> \
  --verdict n/a --recommendation keep \
  --evidence "INTERVENTION (not an audit): <task> (<sha>) <what shipped>."
```

Use `--recommendation extend_window` instead of `keep` when your change
modifies the **very procedure/behavior the HYP is measuring** — its
window now straddles a pre/post boundary and the reviewer must
re-baseline. The `n/a` verdict + the `INTERVENTION (not an audit)`
prefix stop the marker being read as an observation. Then **stop** — no
new HYP.

Otherwise (no active HYP covers the change) → author a new companion HYP:

```bash
rack hyp create "<title>" --hypothesis "…" --baseline "…" \
  --observation-window "…" --success-criterion "…"

rack transition HYP-NNN \
  --set verification_protocol="…" \
  --link source_task:<TASK-ID>
```

`hypothesis` is the one field the schema requires; `rack` owns `id`,
`state` and `created`. A HYP missing `baseline`, `observation_window` or
`success_criterion` is unfalsifiable (see Anti-Patterns).

`verification_protocol` is undeclared by the schema, so it takes `--set`
rather than a flag of its own. Never hand-write the card: an Edit/Write
under the tracker backlog is denied, and `rack` is its only writer.

**`verification_protocol`** is a free-form string telling auditors how to
shape their `--evidence` for this specific HYP. `rack context HYP-NNN`
prints it back, and the session reviewer renders it into the auditor's
handoff. Examples below.

> **Picking the data shape.** Apply `design-minimalism`'s
> behavioral-delivery escalation ladder. An undeclared field under an
> `allow` extras policy (rung 1) is almost always sufficient; lift to a
> declared schema field (rung 6) only after a sweep shows the loose
> shape produces unreadable verdicts.

#### `verification_protocol` examples

**Strict format** (auditor evidence is a tight machine-readable block):

```yaml
verification_protocol: |
  Evidence MUST be exactly three lines, no prose:
  Line 1: "CRITERION: <verbatim string from this HYP's success_criterion>"
  Line 2: "OBSERVED: <verbatim cite from audit.md or work_log, OR 'NOT OBSERVED'>"
  Line 3: "VERDICT_REASON: satisfies | fails | silent"
```

**Loose format** (auditor evidence is short prose):

```yaml
verification_protocol: |
  Evidence: one paragraph (1–4 sentences). Address (a) what the
  success_criterion asks for, (b) what the session showed, (c) whether
  (b) satisfies, fails, or is silent on (a). Verbatim quotes encouraged
  but optional.
```

**Window-counter format** (auditor maintains a running tally):

```yaml
verification_protocol: |
  Evidence MUST start with "WINDOW: <N>/<M>" where N = current sweep
  count incl. this one, M = observation_window target. Then one line
  citing whether this session moved the counter.
```

Pick whichever protocol matches the kind of verdict you actually want
to be able to read back in 4 weeks. Write the protocol so an auditor
who has never seen the task can comply.

### Step 3 — Cross-link (document-stage)

- Task description (or work_log entry) references the new `HYP-NNN`.
- HYP `source_task` points back to this task.
- The HYP-creation commit body (or the immediately-preceding ship
  commit body) carries both IDs: e.g. `<TASK-ID> / HYP-NNN`.

## Anti-Patterns

### ✗ Plan-stage HYP filing (precommitment)

Creating `HYP-NNN` while the task is still in `plan` or early `execute`.
The HYP locks in framing that the diff may diverge from; rollback later
requires `rack transition HYP-NNN stalled` + a re-creation.

### ✗ HYP with empty baseline / observation_window

A HYP whose `baseline` is "agents do X" without specifying *which
sessions* count as the baseline, or whose `observation_window` says
"a few sessions" — both produce unfalsifiable verdicts. The fields
exist because they constrain the auditor. If you can't fill them
concretely, the HYP isn't ready.

### ✗ HYP filed for an unobservable change

If the diff has no behavior delta visible to a future agent session
(e.g. doc-only typo fix, comment rewording), there's nothing to
observe. Mark as refactor at plan-stage and skip.

### ✗ Duplicate HYP when an active one already covers it

Filing a fresh `HYP-NNN` whose mechanism is already the subject of an
active HYP. Two failure shapes: (a) a near-duplicate that the supervisor
must merge (the same churn self-retro 4.5.c prevents); (b) recording the
cross-link only on the **task** side (resolution) and leaving the
existing HYP's `validation_log` with no intervention marker — a
one-sided link a future auditor of that HYP never sees. Fold instead:
one `append-verdict` intervention marker, cross-linked both ways
(Step 2, "already covered" branch).

## Examples

### ✓ Good (post-ship discipline)

Task: rewrite `review-session` skill to add a new output field.

- **Plan-stage:** prior = "output has N fields", post = "N+1 fields".
  Recorded in `plan.md`. **No HYP yet.**
- **Execute:** edit skill, run composition smoke, commit on
  `task/<id>`.
- **Document-stage:** diff final. `rack hyp create "…" --hypothesis "…"`,
  then `rack transition HYP-NNN --set verification_protocol="…" --link
  source_task:<TASK-ID>`, the protocol reading "Evidence MUST quote the
  field name from session retro YAML and confirm it appears under
  hypothesis_verdicts[*]".

### ✓ Good (refactor exemption)

Task: dedup identical injection text between `trait-base` and
`trait-analyst-base`. Plan-stage: prior = post = same observable
behavior. Record "no behavior change — pure refactor: shared injection
text deduplicated, semantics unchanged". No HYP filed — now or later.

### ✗ Bad

Task: add a new rule to `trait-base`. Ship without companion HYP. No
record of expected behavior shift. Two weeks later, no way to tell if
the rule worked.

**Correct response:** at document-stage (after the rule is committed),
file a HYP with `verification_protocol` describing what audit.md
should show; mention both IDs in the next commit body.

### ✗ Bad (precommitment)

Task: same as above, but the agent files `HYP-XXX` at plan-stage
before any edit exists. Execute reveals that the rule alone is
insufficient; scope shifts to a new skill. The pre-filed HYP now
describes a change that never shipped — must be moved to `stalled`
(`rack transition HYP-NNN stalled`) and re-created post-ship. Wastes a
HYP slot and produces a confusing audit trail.

## Scope

This skill describes a **discipline**, not a gate. Nothing in the
framework rejects a merge that lacks a companion HYP, nor rejects a
HYP filed too early. If post-ship discipline slips repeatedly, a
follow-up task may add CI enforcement (reject `hyp create` when source
task is not yet in `document`/`review`/`done`).
