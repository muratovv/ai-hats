# How to extract an area

Working notes for the HATS-1586 epic: the conventions an agent follows when it turns a
folder under `src/ai_hats/` into an **area** — a declared surface with its own tests and
a boundary a lint holds (ADR-0026 D5).

This file is written **as the pilot runs**, not after it. A rule lands here when a
review produced it, with the incident that produced it named — a convention nobody can
trace back to a defect is a preference, and preferences do not survive review.

Status: pilot in progress (`pipeline`, HATS-1783). The measured recipe — what moves,
which `pyproject.toml` lines change, what a slice costs — lands at pilot acceptance.

## 1. Before you touch code

### Read the debt file

`src/ai_hats/debt.py` names every value the code carries without having decided its
type. Read it **first**, and when you meet an untyped value:

- if it is already named there — use that name;
- if it is not — add it there with the card that will retire it, never a local alias.

Two failures this prevents, both from the HATS-1783 review: a module declaring its own
`TracerFactory = object` because it never read the neighbour that already had one, and
the next agent guessing whether that neighbour's `Tracer` and this one's
`TracerFactory` are the same value.

### Collect the open TODOs into your context

The TODOs that matter to a slice are the ones that name it. Run:

```bash
scripts/todo_context.sh                 # every TODO with a card, grouped by card
scripts/todo_context.sh HATS-1785       # only the ones that card will retire
scripts/todo_context.sh src/ai_hats/pipeline   # only the ones inside a path
```

A slice that touches a line carrying a TODO either resolves it or leaves it truthful.
A TODO without a card is a defect in itself (`dev_rule_comment_discipline`): the script
lists those separately, and they are work, not noise.

## 2. Describing an interface

The public surface of an area is a reviewed artefact (ADR-0026 D14), so it is written
for a reader who has none of your context. What review demanded, three times, on the
same file:

- **Say why a field exists, not what the code does with it.** "Where the run happens:
  resolved once by the entry point and passed down" describes a process; "every path a
  step touches hangs off it, and it is resolved once so nothing below rediscovers it"
  says why the reader should care.
- **Name the reader of every field.** A field's comment names the step that consumes
  it. A field whose reader cannot be named is then visible as a field nobody reads —
  which is how `model`, `isolation` and `ticket` turned out to be Automate-only and
  moved off the session.
- **Never illustrate with something the contract does not know.** A comment on
  `exit_code` that explains "None for `init` and `reflect-session`" re-imports the
  application vocabulary the type was just cleared of. Describe the condition
  ("the run launched no session"), not the instances.
- **A name with no decided type is a named alias with a card**, not `object` inline —
  see the debt file above.
- **A value the area only carries is typed as such and said so.** Carrying is not
  knowing: write that the steps read it and the area does not.

## 3. Where a thing lives

The question is answered by the reader, not by the topic:

| The thing                                                | Lives with                | Because                                                   |
| -------------------------------------------------------- | ------------------------- | --------------------------------------------------------- |
| The mechanism (chain sessions, run steps, load a config) | the area                  | it is the same for every user of it                       |
| The policy (which role, which model, what context)       | the application           | it is what *this* product does with the mechanism         |
| The funnel vocabulary (`prompt_path`, `session_mgr`, …)  | the steps that declare it | the literal is the step's own contract declaration        |
| The catalog of pipelines / roles / surfaces              | the application           | knowing the list is knowing how the product uses the area |

The pilot's own case: `pipeline` keeps chaining sessions and telling HITL from
non-HITL, because a chain mixes the two; everything about what a session is *for* lives
in `session_policy.py` next to the steps that read it.

## 4. Gates that hold the boundary

`tests/test_area_boundary.py` — five gates, each verified to go red under its own
violation before being committed:

| Gate                        | Asserts                                            | Shape                    |
| --------------------------- | -------------------------------------------------- | ------------------------ |
| deep entries                | no name enters the area past its `__init__`        | pinned set, shrinks only |
| pipelines assembled in code | no pipeline is built from anything but its YAML    | pinned set, shrinks only |
| modules in a cycle          | no area module sits in a non-trivial import SCC    | empty set                |
| catalog                     | shipped YAML == the application's declared catalog | set equality             |
| registration by import      | importing the shipped tree registers no step       | empty set                |

Three of the five are absolutes rather than ratchets. The cycle gate reached its D12
target of 0 when the step registry stopped resolving by import (HATS-1783), and the
registration gate holds the mechanism that got it there — it imports the same tree in
a fresh process and asserts the step registry came out empty, because an AST gate
watching calls to `register` would miss `_REGISTRY[name] = cls` and every other
spelling of the same side effect.

A pinned set **fails when an entry leaves** as well as when one arrives: converting a
consumer means re-pinning in the same commit, so the ground won is kept, and a swap —
one converted, one added — is red even though the count did not move. On failure the
gate prints the diff and a paste-ready literal, because a pin nobody can regenerate
becomes a pin someone edits by hand.

Edge counting includes deferred and `TYPE_CHECKING` imports (ADR-0026 D5, F4 ruling of
2026-08-21) — a boundary blind to them is blind to 45% of this graph.

## 5. How a gate goes green without the property

Every entry below was found on **this epic's own gates** — all but the last by
review, after the gate was written and believed. Read it before calling a gate done,
and add the row your own incident produces — a taxonomy grows by incident, not by
imagination.

1. **Cardinality instead of the set.** The assert compares a count, so converting one
   offender while adding another leaves it green — and the offender list, which only
   prints on failure, never prints.
   *Fix:* pin the set; print the diff plus a paste-ready literal.
   *Incident:* `BASELINE_DEEP_ENTRIES` compared `len(entries)`; a swap was invisible.

2. **A proxy instead of the subject.** The gate asserts something correlated with the
   property — who imported which module — while the row's subject is the property
   itself: a pipeline assembled from something other than its YAML.
   *Fix:* write the assert about the subject, in whatever spelling reaches it.
   *Incident:* the dispatcher gate listed three modules, so `from ai_hats.pipeline
   import build` was a second path it could not see.

3. **A word in the gate's own name that excises half its subject.** "Foreign
   dispatchers" scanned only modules *outside* the area — and `presets.py`, inside it,
   is the headline C9 defect ADR-0026 names. Two of the three known sites could never
   have been counted.
   *Fix:* ask what the qualifier excludes, and whether the property holds there too.

4. **Reading a different tree than the one under test.** The catalog gate resolved the
   shipped pipelines through `AI_HATS_PROJECT_DIR`, which in a worktree points at the
   main checkout: it compared one tree's YAML against another tree's catalog. A
   worktree that added a pipeline went green.
   *Fix:* resolve what is under test from the test's own location.

5. **A gate that can skip itself.** `pytest.skip` when the library root did not
   resolve. In a suite summary, silence and success are the same colour.
   *Fix:* an unresolvable precondition is a failure that names what it could not
   resolve.

6. **An exemption wider than the sanctioned case.** `loader.py` is exempt as a *module*,
   so a second, non-YAML assembly added inside it stays invisible.
   *Fix:* exempt the call, not the file.

7. **A bypass around the measured act.** A prebuilt object handed out — `from .presets
   import execute_pipeline` — never calls the constructor the gate counts. Closed today
   only because the producer is pinned, and it reopens the moment a pinned assembly is
   converted without its importers.
   *Fix:* count the artefact where it is produced, and pin the producers.

8. **A gate closable without touching the second implementation.** The precedent is in
   ADR-0026 itself: C5's import-time budget assert was replaced by a test, the gate went
   green, and the budgets stayed ownerless in four places.
   *Fix:* if it can go green while the second path lives, it is measuring something
   else — see 2.

9. **A fallback in the production code that makes the gate's subject unobservable.**
   Caught at design time on this epic's own e2e, not after: the built-in step ids
   moved to `[project.entry-points]`, and the tempting safety net was a module-path
   table in `registry.py` for the uninstalled case. With it, deleting the whole
   entry-point block leaves every pipeline running — so the e2e whose subject is
   "the declarations reached the built distribution" passes on a distribution that
   carries none. The second path here is in the *code*, not the gate, and it makes
   the property unobservable rather than unasserted.
   *Fix:* refuse loudly instead, and say at the refusal site why there is no
   fallback. A gate you cannot break by deleting the mechanism is measuring
   something else — see 2 and 8.

Standing rule behind all nine (ADR-0026 D3): a gate closes a row only by asserting the
**absence of a second path**. A test that asserts a property of behaviour stays true
with several owners, so it cannot close anything.

## 6. What review keeps catching

Kept as a checklist because each line cost a round:

- a field typed for a consumer that has not migrated yet is a guess — `errors` was
  typed `tuple[str, ...]` when the runner records `dict[step, exception]`, and nothing
  would have caught it, since the reader was not converted;
- an enum of application pipelines inside the area is the same defect as an import of
  one — the area does not know how it is used;
- an id that reaches into another subsystem (a backlog card id in a launch contract) is
  an implicit dependency: hand over the rendered result instead (HATS-1786);
- a `str` that is a mode is an enum;
- "we will type it when the consumer migrates" is how a wrong type ships — describe the
  shape from its writer, not from the caller you imagine.

## References

- [1] `docs/adr/0026-capability-ownership-and-the-project-value.md` — D5 (what an area
  is), D7 (area test vs crossing test), D12 (pilot gates and measurements), D14 (the
  public contract as a reviewed artefact).
- [2] `src/ai_hats/debt.py` — the undecided types, with the card that retires each.
- [3] `tests/test_area_boundary.py` — the three gates.
- [4] `scripts/todo_context.sh` — TODOs by card, for the agent's context.
