---
name: composition-verification
description: Prove a role/trait/rule/skill edit reached the composed prompt. Use after editing any library component, before reporting a composition change landed, and when a rule or skill looks absent from a role that should carry it.
license: MIT
---

# Composition Verification

Prove the edit arrived — by reading the render, not the file you just edited.

## When to Use

Not for judging whether the change is *good*: auditing a composed role for
contradictions is `role-coherence-protocol`, run by `ai-hats reflect role <name>`.
This skill answers the narrower question that must come first — did the change
reach the prompt at all?

It is the last of three steps, and owns only its own. Deciding that prose is the
right mechanism and writing it is `prompt-authoring`; placing the component in a
root this project reads and wiring it to a role is `library-editing`. Arrive
here once the edit is made.

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

It prices the same composed plan `show-prompt` renders — overlays from
`~/.ai-hats/customizations.yaml` and the project's `ai-hats.yaml` included.
The `Tokens` total sums skill bodies too, and those are **not** resident: the
figure to quote is the `Always-on` footer (injections, rule bodies, and each
skill's name + description). Quote the number this run produced.

Confirm you priced the tree you meant: the table should list a component that
exists only there. Pricing the wrong library returns a plausible number with no
error, which is the one failure this step cannot detect on its own.

### 6. When you need the object graph, compose in-process

Only when the CLI's output is not enough — you want `errors`, the resolved
components, or to compose a library root other than the one cwd implies. A
ready script sits next to this skill: `examples/compose-in-process.md`. Copy it,
set the role, run it with the interpreter of the checkout you mean.

The one thing to carry without reading it: **always pass overlays**. Omitting
them silently skips user-global customizations and the project's own overlays —
the usual cause of a false "the rule is not there".

To force a specific library root instead:

```bash
export AI_HATS_LIBRARY_ROOT=<path to a library root>
```

### 7. From a worktree, read-only and writing commands disagree

Run the read-only command from inside the worktree you edited: `config
show-prompt`, `--dry-run`, `config status`'s role tree, or a `list` subcommand
that reads the library (`list providers` reads none). It composes from that
worktree when the root you edited is one of:

- the built-in layers — the worktree is an ai-hats source checkout;
- the project's `libraries/`;
- a user-global or configured root (`~/.ai-hats`, an entry of
  `~/.ai-hats/library_paths.yaml` or of `ai-hats.yaml: library_paths`) whose
  MAIN checkout the worktree belongs to.

Any other root is read where it is listed.

Two things in that output still answer about the PROJECT, and are not bugs:
`config status`'s **Health** block (version, venv, materialized prompt) reports
what is installed here, and its `Library:` line comes from `importlib` rather
than the resolver — so it can disagree with the tree the same command just
composed from.

A command that **writes** — `self init`, anything materializing into the agent
directory — targets the project, which for a linked worktree is the MAIN
checkout, so tracker operations reach the one live backlog. What it composes
follows the list above for `libraries/` and same-repo roots; only the built-in
layers differ — a writer takes them from the project's own source checkout
(cwd only when the project names none). So `self init` run inside a worktree
ships the branch's `libraries/` and same-repo roots into MAIN's shared
artifacts: run it from MAIN unless that is what you want.

To force either side: `AI_HATS_LIBRARY_ROOT` pins the built-in root, and a path
passed in-process (step 6, `Assembler(project, library_paths=[<root>])`) outranks
every listed root; neither ever consulted cwd.

### 8. Know when the edit takes effect

Composition happens at launch and there is no sync command — `library-editing`
§4 has it. What matters here: that is why verification is a read-only command
and never "restart and see".

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
