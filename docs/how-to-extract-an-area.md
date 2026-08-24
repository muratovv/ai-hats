# How to extract an area

Working notes for the HATS-1586 epic: the conventions an agent follows when it turns a
folder under `src/ai_hats/` into an **area** — a declared surface with its own tests and
a boundary a lint holds (ADR-0026 D5).

This file is written **as the pilot runs**, not after it. A rule lands here when a
review produced it, with the incident that produced it named — a convention nobody can
trace back to a defect is a preference, and preferences do not survive review.

Status: pilot complete (`pipeline`, HATS-1783). §7 carries the measured recipe ADR-0026
D12 asks for — the numbers, the sequence, and what a slice cost. The second area
(`consent`) is scoped by comparing its own numbers against §7 **before** work starts.

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
- **An interface offers exactly one way to perform one action.** Two spellings of the
  same read are a defect even when both work, because the second one is what the next
  migration standardises on by accident — nobody chooses it, it is simply the one that
  was in front of the agent doing the conversion. `PipelineResult` typed `exit_code`
  and `session` *and* handed the same entries out in `produced`, the raw final funnel;
  the fix was not to document which to prefer but to delete the choice. `from_state`
  now **lifts** the keys the contract answers for out of the funnel instead of copying
  them beside it, so a typed field has no raw twin, and everything else — the exit code
  included — is read once, through the typed readers in `session_policy.py`. The rule
  is testable in that form and only in that form: the area pins its typed field list
  (`pipeline/tests/test_pipeline_result_contract.py`), and a field added without
  lifting its key is red.

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
2026-08-21) — a boundary blind to them is blind to two edges in five. Re-measured at
pilot acceptance with the definition stated: 39% of the name-edges entering the area
before the epic were deferred or `TYPE_CHECKING`, 38% now, and 32% of every import in
the shipped tree is. An earlier draft of this line said 45% and named no definition;
the number is replaced rather than defended, and §7 carries the measurement.

## 5. How a gate goes green without the property

Every entry below was found on **this epic's own gates**: the first eight by review,
after the gate was written and believed, the ninth at design time, and the tenth by a
red check that read green. Read it before calling a gate done, and add the row your own
incident produces — a taxonomy grows by incident, not by imagination.

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
   was the headline C9 defect ADR-0026 names. Two of the three known sites could never
   have been counted; both were `presets.py`, which is deleted, so the count the
   qualifier hid was two thirds of the breach.
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

6. **An exemption wider than the sanctioned case.** `loader.py` was exempt as a
   *module*, so a second, non-YAML assembly added inside it stayed invisible — proven
   with a `build(name="probe_bypass")` appended to the file, which read green.
   *Fix:* exempt the call, not the file. The exemption is now `(module, function)`
   pairs, and it still lets past an assembly written inside `load_pipeline` itself —
   an exemption says what it covers or it is a hole.

7. **A bypass around the measured act.** A prebuilt object handed out — `from .presets
   import execute_pipeline` — never calls the constructor the gate counts. It was closed
   only because the producer was pinned; deleting `presets.py` removed the producer, and
   the shape reopens the moment a pinned assembly is converted without its importers.
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

10. **The violation was never constructed.** Not a way the gate is wrong — a way the
    *proof* is. A red check edits the tree, reads red, and is believed; when the edit
    did not land, it reads green and is believed just the same, and what has been
    certified is a gate that never bites. Two forms, both on this epic: the injected
    line was anchored on text that was not in the file, so nothing changed at all; and
    the injected line landed but was semantically inert — putting `from . import steps`
    back into `loader.py` left the cycle gate green, because `steps/__init__.py` no
    longer imports anything, so the edit restored the spelling of the old edge and none
    of its effect.
    *Fix:* a red check proves its violation landed before it reads the result — grep
    the marker it injected, and where the violation is semantic rather than textual,
    assert the property is actually broken first (here: the registry is non-empty after
    the import) and only then run the gate.

Standing rule behind all ten (ADR-0026 D3): a gate closes a row only by asserting the
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

## 7. The measured recipe

ADR-0026 D12 makes this section the pilot's deliverable: not "it moved", but numbers a
next area is estimated against before anyone starts. Everything below was measured with
one instrument — the helpers in `tests/test_area_boundary.py`, run against a clean
checkout of the pre-epic tree and against this one, so "an import" means the same thing
on both sides.

### The numbers

| Measure                                      | Before the epic (`a5b62f7d`) | Now              | Gate          |
| -------------------------------------------- | ---------------------------- | ---------------- | ------------- |
| Deep entries (imports past `__init__`)       | 107                          | 3                | D12: 0        |
| Area modules in a non-trivial SCC            | 8                            | 0                | D12: 0        |
| Pipelines assembled in Python, not from YAML | 3                            | 1                | HATS-1784     |
| External modules importing the area (fan-in) | 11                           | 13               | measured only |
| Incoming name-edges                          | 107                          | 21               | measured only |
| … of them deferred or `TYPE_CHECKING`        | 42 (39%)                     | 8 (38%)          | D8 precond. 2 |
| Tests inside the area                        | 0                            | 74, in 7 files   | D7            |
| Tests crossing into it                       | 38 files                     | 297, in 32 files | D7            |
| Area suite wall time vs the full suite       | —                            | 0.19 s vs 155 s  | D12           |
| `python -m ai_hats --help` (min of 5)        | 0.17 s                       | 0.17 s           | D12: ≤ 0.25 s |
| `import ai_hats.pipeline.loader`             | 135.7 ms                     | 29.8 ms          | measured only |
| Area tests in the built wheel                | none                         | none             | D11: none     |

Four of these say something the counts alone do not:

- **Fan-in went up, not down.** 11 modules imported the area before, 13 do now, because
  two of the new importers are the modules the epic created to hold what left it
  (`pipeline_catalog`, `session_policy`). Fan-in is the wrong headline: the edges are
  what moved — 107 name-edges to 21, and *every one* of the 107 was a deep entry, which
  is what "the facade exists and is bypassed" looked like as a number.
- **The deferred share did not move** (39% → 38%). It is a property of how this codebase
  imports, not of the extraction, so D8's precondition 2 is not something an extraction
  earns — measure it, do not expect to change it. A boundary lint blind to deferred and
  `TYPE_CHECKING` imports would still miss two edges in five.
- **`--help` never moved** because it never reached the loader. The 106 ms the loader
  shed is real and is paid by anything that runs a pipeline; it is invisible to the
  gate D12 chose. Keep the gate — it is a ratchet against a regression — but do not
  read it as the pilot's benefit.
- **The area suite is 0.12% of the full run.** That is the number T2 was missing: a
  selector that ran only the area's own tests would save 155 s and answer for 74 of
  4 443 tests. The 297 crossing tests take 4.3 s and are the ones an area change
  actually risks — so the honest T2 unit here is "area + crossing" (4.5 s), not "area".

### The sequence a next area follows

Ten steps, in the order the pilot had to do them; each one is one commit.

1. **Read the debt file and the open TODOs** (§1). Free, and it is what stops the next
   agent inventing a second name for a value that already has one.
2. **Type the entry points.** Give the area a `Config` value and a `RunParams`-shaped
   protocol its callers implement, and convert one caller. ~200 lines, one commit.
3. **Write the boundary gates before converting anything else**, pinned at whatever the
   tree says today (§4). Every pin regenerated from a failing run, never edited by hand.
   The pin is the work list; a slice that does not lower it did nothing.
4. **Move policy out.** Everything about what the area's mechanism is *for* leaves for a
   `<area>_policy.py` and a `<area>_catalog.py` beside the application (§3). This is the
   biggest slice — 405 lines across 7 files on the pilot — and it is what makes the
   remaining deep entries visible as a short list rather than a hundred.
5. **Convert the callers**, re-pinning in the same commit so a swap cannot hide (§4).
6. **Cut the cycles.** On the pilot every one of the 8 ran through a single import whose
   only job was a registration side effect. Look for that shape first: an import kept
   for what it *does*, not for what it *names*.
7. **Give each remaining reach-in a name.** A caller importing three of your modules is
   usually one capability you never declared — `run_subpipeline` and `warm` were 14 of
   the pilot's last 17 deep entries. Re-export a type rather than move it when the area
   is the value's *writer*; moving it inverts the edge you just cleaned.
8. **Move the area's own tests inside it** (D7): the ones that need the public surface
   plus stdlib and nothing else. On the pilot 64 of 74 moved on this criterion, and a
   34-file `tests/<area>/` turned out to be the session tier wearing the area's name.
9. **Prove every gate red under its own violation**, and prove the violation landed
   (§5 row 10).
10. **Take the numbers** (this section) and hand them to the next area.

### The `pyproject.toml` lines

Four places, and no others:

```toml
[tool.hatch.build.targets.wheel]
exclude = ["src/ai_hats/**/tests"]        # 1. D11 — one glob covers every area

[tool.pytest.ini_options]
testpaths = [..., "src/ai_hats/<area>/tests"]   # 2. one line per area; a glob is unreliable

[project.entry-points."ai_hats.<things>"]  # 3. only if the area resolves plugins by id
<id> = "<module>:<Class>"

[project.optional-dependencies]            # 4. only if the area ships its own deps
```

Line 1 is written once and covers every area after the first. Line 2 is one line per
area, forever. Line 3 is the expensive one: it is a **non-import edge** (ADR-0026 D15),
so it is outside test selection by design and changing it runs the full suite — and it
is unobservable from the source tree, so it needs the e2e that installs a built wheel
(`tests/e2e/test_step_entry_point_resolution.py`, ~1.2 s).

### The gates that get re-pinned

`PINNED_DEEP_ENTRIES` on every conversion commit; `PINNED_PYTHON_ASSEMBLED` when a
Python-built pipeline becomes a YAML one; `PINNED_AREA_MODULES_IN_A_CYCLE` once, to
empty, and then never again — it is an absolute. `scripts/test_isolation_baseline.json`
whenever the area's tests move or grow. Regenerate all of them from the failing run's
paste-ready literal.

### What a slice costs

Given with the command that re-takes them: the previous version of this paragraph was
exact at one commit and stale at the next, which is the same defect as a pin nobody can
regenerate. Measured at `96befa45` — the pilot minus the commit carrying this line —
with `git diff --shortstat master...` and `git diff --numstat master...`, splitting the
changed lines by path: **test** is `tests/**` plus `src/**/tests/**`, **doc** is `*.md`,
**production** is everything else, `pyproject.toml` and `scripts/` included.

**22 commits, 83 files, +2 934 / −974 lines**, so 3 908 changed lines split 1 907
production / 1 639 test / 362 doc — **roughly half the lines are gates and prose**, and
the production half is itself mostly contract comment, because the contract is a
reviewed artefact (D14). Budget accordingly: the code move is the small part. Median
slice ≈ 180 changed lines across 4–5 files; the outliers were the entry-point cut (771,
of which 190 is one e2e), the CLI conversion (631) and the gate file's rewrite (419).

The next area is cheaper on three counts and dearer on one. Cheaper: the wheel exclude
exists, the gate file exists and takes a second area as new constants rather than new
code, and §5 is already written. Dearer: `consent` crosses a distribution boundary and
is reached by console script and subprocess (ADR-0026 D10, D15), so its equivalent of
the entry-point e2e has to be built, not copied.

## References

- [1] `docs/adr/0026-capability-ownership-and-the-project-value.md` — D5 (what an area
  is), D7 (area test vs crossing test), D12 (pilot gates and measurements), D14 (the
  public contract as a reviewed artefact).
- [2] `src/ai_hats/debt.py` — the undecided types, with the card that retires each.
- [3] `tests/test_area_boundary.py` — the five gates.
- [4] `scripts/todo_context.sh` — TODOs by card, for the agent's context.
