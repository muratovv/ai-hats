# Extracting an area

Working notes for the HATS-1586 epic: the conventions an agent follows when it turns a
folder under `src/ai_hats/` into an **area** — a declared surface with its own tests and
a boundary a lint holds (ADR-0026 D5).

This file is written **as an area runs**, not after it. A rule lands here when a
review produced it, with the incident that produced it named — a convention nobody can
trace back to a defect is a preference, and preferences do not survive review.

Status: two areas done — the pilot (`pipeline`, HATS-1783) and `surfaces` (HATS-1826).
§7 carries the measured recipe ADR-0026 D12 asks for, now with both columns: the second
area is what turns "the pilot's numbers" into a range a third one can be estimated
against. `consent` is still unrun, and still the area that would prove D10.

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

The public surface of an area is a reviewed artefact (ADR-0026 D14), written for a
reader who has none of your context. The shapes themselves — say why a field exists,
name the reader of every field, never illustrate with what the contract does not know,
one home for an undecided type, one way to perform one action — are the skill
**interface-design**. What belongs here is what this epic measured on its own contract:

- **The three fields with no nameable reader were real.** `model`, `isolation` and
  `ticket` had no step that consumed them on the session path; naming the reader is what
  made that visible, and all three turned out to be Automate-only and moved off.
- **A value the area only carries is typed as such and said so.** Carrying is not
  knowing: write that the steps read it and the area does not.
- **The "one way" rule became testable here, and only in this form.** `PipelineResult`
  typed `exit_code` and `session` *and* handed the same entries out in `produced`, the
  raw final funnel. The fix was not documenting which to prefer: `from_state` now
  **lifts** the keys the contract answers for out of the funnel instead of copying them
  beside it, so a typed field has no raw twin, and everything else — the exit code
  included — is read once through the typed readers in `session_policy.py`. The area
  pins its typed field list (`pipeline/tests/test_pipeline_result_contract.py`), and a
  field added without lifting its key is red.

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

Five gate *kinds*, but since HATS-1826 the two import gates — deep entries and modules
in a cycle — run **once per area**, and the file names each area's pin separately. When
you add an area, both take an `area` argument or the new boundary is half-watched; that
half-watching is what the second area shipped with (§6).

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
after the gate was written and believed, the ninth at design time, the tenth by a red
check that read green, the eleventh by a status file that read green two days after
it was written, and the twelfth by running one test file on its own. Read it before
calling a gate done, and add the row your own incident
produces — a taxonomy grows by incident, not by imagination.

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

11. **The verdict was never produced by this run.** Row 10's sibling one step later:
    there the violation never lands, here the *result* never came from the run it is
    read against. `/tmp/f_suite.rc` was read for a suite verdict and answered green —
    the file was two days old, left by an earlier session, and the suite it was
    supposed to describe was still running. It was caught only by the file's mtime and
    a `pgrep`; no wrong conclusion was drawn, but what a green like that certifies is
    what row 10 certifies, a gate that never bit. Nothing in the shape is `/tmp`'s: a
    stale wheel, a cached import and a baseline JSON from another branch all read as
    evidence of a run that did not produce them.
    *Fix:* an artefact read as evidence is proven to belong to this run before any
    conclusion rests on it — a fresh path per run, or its mtime checked against the
    run's start.

    *Measured (HATS-1798), in the Bash tool's own shell — zsh 5.9, `$BASH_VERSION`
    empty — with `false` standing in for the runner. The status of a compound
    command is the status of the last command it ran; every row below is that one
    rule, and a wrapper script is a compound command too:*

    | command | status | | command | status |
    | -------------------------------- | -----: | | ----------------------------------------- | -----: |
    | `false` | 1 | | `false && true` | **1** |
    | `false; true` | 0 | | `set -o pipefail; false \| tail` | 1 |
    | `false \| tail` | 0 | | `false \| tail; exit "${PIPESTATUS[0]}"` | **0** |
    | `false \| tee log` | 0 | | `false \| tail; exit "${pipestatus[1]}"` | 1 |
    | `false \|\| true` | 0 | | `false > log; echo $? > rc` | 0, `rc`=1 |

    The bare `false` returns 1 to the harness, which reports it faithfully — the
    harness never misreports, it is handed the last command's status. The two bold
    cells are the traps: `&&` needs no exception because it short-circuits, and
    `${PIPESTATUS[0]}` is bash-only, so in zsh `exit "${PIPESTATUS[0]}"` is
    `exit ""` → 0 for every run, silently. Re-measure rather than trusting this
    table: `echo "$ZSH_VERSION / $BASH_VERSION"`.

12. **The precondition was inherited, not established.** The gate is written about the
    right subject and it does bite — but only from a state a neighbour left. Moving the
    built-in steps to entry points made them resolve lazily, so `_REGISTRY` starts a
    process empty and a project step registering `compose_role` hit no collision at all.
    Both tests that assert the refusal (`test_conflict_with_builtin_raises`,
    `test_harness_user_step_collision_aborts_before_namespace_setup`) kept passing in
    the suite, because an earlier module had resolved the built-in and filled the
    registry; run alone, each read `DID NOT RAISE`. CI never ran them alone, so a real
    regression in the shipped contract — a user step silently shadowing a built-in —
    read green for eleven commits.
    *Fix:* a test states the state it starts from instead of inheriting it, and the
    verdict is taken **twice** — alone and in the suite — with the two required to
    agree. Where the state is process-global (a registry, a memo, a module cache), a
    reset in the fixture is the statement; where the production code made the state
    lazy, check what asserted the property *eagerly* before the change and is now
    reading an empty container.

Standing rule behind all twelve (ADR-0026 D3): a gate closes a row only by asserting the
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

From the second area (HATS-1826), where the review found each of these in a slice that
had already gone green:

- **a contract that was moved is not a contract that was reviewed** — `surfaces/contract.py`
  was 580 of its 594 lines byte-identical to the module it left, and the only new
  authorship in it was its own docstring. D14 calls the public contract a reviewed
  artefact; a relocation passes every gate and reviews nothing. Ask what the contract
  *stopped carrying*, not whether it compiles;
- **the `debt.py` entry your card makes retirable is your card's to retire** —
  `TranscriptResolver` became typeable the moment the contract sat behind one facade,
  and shipped untouched beside a second spelling of the same value on
  `CompositionPayload`. That is the exact failure `debt.py` exists to prevent, committed
  by the card that removed the reason for it;
- **a gate parameterized for one area is a gate for one area** — the deep-entry gate
  took an `area` argument; the cycle gate beside it kept the pilot hard-coded, so the
  new area had its entrances watched and its exits not. When you generalize one gate,
  grep for its siblings;
- **a boundary gate that walks only `src/` cannot see a test reaching in** — a top-level
  test imported `surfaces.contract._extract_frontmatter_description`, which is precisely
  the breach the gate forbids everyone else. The area's own tests are the sanctioned
  reach-in; a test outside it is a deep entry that no pin counts;
- **a green suite after a contract change means the contract has no test** — four fields
  left `SurfaceRunResult` and the pass count did not move, because nothing exercised
  `SubagentEngine.run` at all. A number that should have moved and did not is a finding
  about the instrument, not a reassurance about the change.

### What landing the slice keeps catching

Review is not the last gate; two of the second area's defects appeared only when the
branch met master.

- **A rebase across a rename resurrects deleted code.** Master deleted a reader while
  the branch was moving that same reader to a new file. Git resolves per file, so the
  deletion landed on the file the branch had emptied and the moved copy survived —
  importing a type that no longer existed anywhere, which took the whole area down at
  import. After any rebase that moved files, grep the destinations for what the upstream
  deleted; a clean `git rebase` is not evidence.
- **Renaming `[project.entry-points]` values wedges the merge commit itself.** The git
  hook builds an `Assembler`, which resolves surfaces through *installed* metadata — and
  that metadata cannot be current until the merge lands. The way out without
  `--no-verify`: stage the merge, reinstall the editable (the working tree already
  carries the merged `pyproject`), then commit. Carded as HATS-1851.

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

### The second area, measured the same way

`surfaces` (HATS-1826), same instrument, run against this tree. It is the column that
turns the pilot's numbers into a **range**, and the two differ far more than "one area
is bigger" would predict.

| Measure                             | `pipeline` (pilot) | `surfaces`               |
| ----------------------------------- | ------------------ | ------------------------ |
| Deep entries — before / after       | 107 → 3            | **2 → 0**                |
| Area modules in a non-trivial SCC   | 8 → 0              | 0                        |
| External modules importing the area | 13                 | 6                        |
| Incoming name-edges                 | 21                 | 7                        |
| … deferred or `TYPE_CHECKING`       | 8 (38%)            | 4 (57%)                  |
| Tests inside the area               | 74, in 7 files     | 243, in 18 files         |
| Tests crossing into it              | 297, in 32 files   | 607, in 49 non-e2e files         |
| Area suite vs the full suite        | 0.19 s vs 155 s    | 2.2 s vs 187 s           |
| Area tests in the built wheel       | none               | none                     |
| Slice size                          | epic, many slices  | 236 files, +2814 / −3837 |

The instrument is the one §7 used — the helpers in `tests/test_area_boundary.py`,
re-run on this tree. It reproduces every pilot figure above exactly except the area
test count, now 75: one test has landed there since. The pilot column here is §7's
recorded number, so the two tables agree.

Three things this column says that the pilot's alone could not:

- **Inherited deep entries, not size, decide whether the gate is reachable.** `surfaces`
  is the bigger area by tests (243 vs 74) and took the D12 "0 deep entries" gate in one
  slice; `pipeline` is smaller and has not taken it across a whole epic. The difference
  is 2 versus 107 at the start. **Count a candidate's deep entries before anything else
  about it** — that number, not LoC, is the estimate.
- **An area can be net-negative.** Every other slice of this epic promised growth,
  because extraction wraps a mechanism in a port. This one deleted 1 023 lines net: the
  surfaces did not need a port, they needed to stop being four distributions. When a
  candidate's problem is *packaging*, extraction is a removal, and predictive accounting
  should say so before the work rather than after.
- **The deferred share went up, not down** (38% → 57%). §7 already said that share is a
  property of how this codebase imports rather than something extraction earns; a second
  area with an even higher share, and a smaller absolute count, confirms it. Do not read
  it as a quality signal in either direction — it is D8's precondition 2 and nothing else.

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
