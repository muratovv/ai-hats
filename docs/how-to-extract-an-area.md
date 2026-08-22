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

`tests/test_area_boundary.py` — three negative universals, each verified to go red
under its own violation before being committed:

| Gate                | Asserts                                            | Shape               |
| ------------------- | -------------------------------------------------- | ------------------- |
| deep entries        | no name enters the area past its `__init__`        | ratchet, falls only |
| foreign dispatchers | nothing outside the area dispatches a pipeline     | ratchet, falls only |
| catalog             | shipped YAML == the application's declared catalog | equality            |

A ratchet **fails when the count drops** as well as when it rises: converting a
consumer means lowering the baseline in the same commit, so the ground won is kept.
Edge counting includes deferred and `TYPE_CHECKING` imports (ADR-0026 D5, F4 ruling of
2026-08-21) — a boundary blind to them is blind to 45% of this graph.

## 5. What review keeps catching

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
