---
name: composition-verification
description: Prove a role/trait/rule/skill edit reached the composed prompt. Use after editing any library component, before reporting a composition change landed, and when a rule or skill looks absent from a role that should carry it.
license: MIT
---

# Composition Verification

Prove the edit arrived — by reading the render, not the file you just edited.

## When to Use

Not for judging whether the change is *good*: auditing a composed role for
contradictions is `role-coherence-protocol`, run by the `role-auditor` role.
This skill answers the narrower question that must come first — did the change
reach the prompt at all?

The general discipline of proving your verification path is not itself broken
is **positive-control**; this skill is that discipline applied to composition,
and step 3 is where the two meet.

## Procedure

### 1. Read the render, not the source

```bash
ai-hats config show-prompt --role <role>    # the prompt the agent would see
ai-hats config show-prompt --stats          # the same, as structured JSON
ai-hats config status                       # role, dependency tree, health
ai-hats --dry-run                           # what a launch would deliver
```

`--role` is an option, not a positional: `show-prompt <role>` fails with
`Got unexpected extra argument`. With no `--role` it renders the project's
active role, which is the wrong answer given quietly.

A file on disk can look perfect while the composer never resolved it: a typo in
a component name, a component placed in a layer the role does not search, or an
unknown key under `composition:` (stripped with one stderr WARN each). None of
those change the file you are re-reading.

### 2. Know what does and does not enter the prompt

| Component          | In the prompt?                                                     |
| ------------------ | ------------------------------------------------------------------ |
| trait injections   | yes — merged into one body, deduplicated by **text**               |
| role injection     | yes                                                                |
| rule bodies        | yes — appended as a separate `RULES` section, not inside the merge |
| skill bodies       | **no** — loaded on trigger                                         |
| skill descriptions | **no** — the harness indexes them for selection                    |

So grepping the render for a skill name proves nothing about whether the skill
is attached. For skills, read the resolved component list (`config status`)
instead. Two traits carrying identical injection prose contribute it **once**,
silently — a "missing" section may be a text-level duplicate, not a drop.

### 3. Every zero needs a positive control

A grep that finds nothing has two explanations: the thing is absent, or the
search is broken. Before reporting absence, run a pattern over the **same
render and the same scope** that must hit — a section heading you know is
there. No control, no verdict.

For a removal, invert it: the thing you moved must still be found where it
now lives.

### 4. Compare before and after with one interpreter

Materialize the pre-edit tree (`git archive <ref>`), compose both, and diff the
component sets — gained, lost, and `errors == []` on each side. A before-figure
recalled from an earlier session is not a measurement.

### 5. Price it, do not predict it

```bash
ai-hats list tokens <role>
```

The `TOTAL` column sums skill bodies too, and those are **not** resident. The
always-on figure is injections plus rule bodies — read those rows, not the
total. Quote the number this run produced.

**`list tokens` does not follow cwd** the way `show-prompt` does: it resolves
the project root, so run from a worktree — or any checkout that is not the
project — it silently prices the OTHER tree and returns 0. Set
`AI_HATS_LIBRARY_ROOT` to the library you mean before pricing, and confirm the
table lists a component you know is only in that tree.

### 6. When you need the object graph, compose in-process

Only when the CLI's output is not enough — you want `errors`, the resolved
components, or to compose a library root other than the one cwd implies:

```python
from pathlib import Path
from ai_hats.assembler import Assembler

project = Path(".").resolve()
a = Assembler(project)                       # this path resolves the library
overlays = a._get_overlays("<role>")         # ALWAYS pass overlays
result = a.composer.compose("<role>", overlays=overlays)
assert result.errors == []
```

Omitting `overlays` silently skips user-global customizations and project
config overlays — the usual cause of a false "the rule is not there".

To force a specific library root instead:

```bash
export AI_HATS_LIBRARY_ROOT=<path to a library root>
```

### 7. From a worktree, read-only and writing commands disagree

`show-prompt` run inside a worktree composes THAT worktree's library: it keys
off cwd, so your edit is what you see. Do not generalize that to every
read-only command — `list tokens` does not (step 5).

A command that **writes** — init, anything materializing into the agent
directory — deliberately still keys off the project, which for a linked
worktree is the MAIN checkout, so tracker operations reach the one live
backlog. Do not expect a worktree edit to reach a materialized artifact.

Passing the path explicitly (step 6) or `AI_HATS_LIBRARY_ROOT` is how you force
either side; neither ever consulted cwd.

### 8. Know when the edit takes effect

There is no sync command. Roles are composed fresh at every session launch, so
an edit is live for the next one. That is why verification is a read-only
command and never "restart and see".

## Completion

- The claim "the change reached the prompt" is backed by the render, not the file.
- Every reported absence carries its positive control.
- Any cost figure comes from this run's output.

**Validation scenario (RED).** An agent adds a skill to a trait, re-reads the
trait's `config.yaml`, sees its own line, and reports the role now carries it —
while the composer never resolved the name and the role's prompt is unchanged.
The same agent then greps the render for the skill's body text, finds nothing,
and concludes the composition is broken — when skill bodies never enter the
prompt at all. Both halves of that session are wrong for opposite reasons, and
both are prevented by steps 1–2.

## Anti-Patterns

- Verifying by re-reading the file you just edited.
- Grepping the merged injection for a skill and reading the zero as a defect.
- Reporting an absence with no positive control on the search.
- Quoting a token figure from memory instead of this run's output.
- Composing in-process without overlays, then diagnosing a "missing" rule.
