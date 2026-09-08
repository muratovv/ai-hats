---
name: library-editing
description: Change what an agent composes in this project — author a component in the project's own library root, wire it to a role persistently or for one session, and know when the edit takes effect. Use before adding or removing a trait, rule or skill, and when a component you wrote is not reaching the agent.
license: MIT
---

# Library Editing

Put a component where this project reads it, and wire it to the role that needs it.

## When to Use

Two neighbours own the steps on either side of this one. **Writing** the
component — its structure, its description, its validation scenario — is
`skill-template`, and deciding that prose is the right mechanism at all is
`prompt-authoring`. **Proving** the wiring worked is `composition-verification`;
this skill stops at "the edit is made", it does not certify it arrived.

## Procedure

### 1. Put the component in a root this project reads

Your own roots are **flat** — no layer directory under them:

```
<project>/libraries/{roles,traits,rules,skills}/<name>/     # project-local
~/.ai-hats/{roles,traits,rules,skills}/<name>/              # user-wide
```

Any further root listed in `~/.ai-hats/library_paths.yaml` has the same shape.
`~/.ai-hats/core/rules/foo/` resolves to nothing — the `core/` / `usage/` split
exists only inside the shipped library. Roots layer last-wins, so a component
whose name matches a shipped one overrides it.

### 2. Wire it — persistently, or for one session

Persistent, written into `ai-hats.yaml` (project) or
`~/.ai-hats/customizations.yaml` (global):

```bash
ai-hats config customize <role> --add-trait <name> --project
ai-hats config customize <role> --add-skill <name> --remove-skill <name> --global
ai-hats config customize <role> --injection-append "<text>"
```

Ephemeral, for this launch only — a **role spec**, and nothing is written:

```bash
ai-hats -r "dev-python + skill-engineer"      # quotes when spaces are used
ai-hats -r dev-python+skill-engineer          # compact form, same thing
ai-hats -r "maintainer - trait-base + trait-base-star"
```

Reach for the role spec when you are testing whether a component changes
behaviour at all. Persist it once the answer is yes — a spec nobody wrote down
is a result nobody can reproduce.

### 3. The `+` / `-` grammar, and the two traps in it

The base role comes first, then operations. Both operators take exactly one
component name.

- **`-` is an operator only when surrounded by spaces.** `trait-base` is one
  identifier; `- trait-base` removes it. A hyphenated name never splits.
- **A removal resolves against the COMPOSED set**, not against the role's own
  declaration: you can drop a rule a trait brought in, without editing the trait.
- A repeated operand, or a trailing `+` with nothing after it, is refused with a
  parse error rather than silently ignored.

The spec becomes a third overlay layer — `[global, project, runtime]` — so it
wins over both persistent layers for that session.

### 4. Know when the edit takes effect

There is no sync command. Roles are composed fresh at every launch, so an edit
to a component body, to `ai-hats.yaml`, or to a customization is live for the
**next** session — including a component you just wrote by hand. `ai-hats self
init` validates the config and refreshes the project scaffold; it is not how a
composition change lands.

Never hand-copy component files into a provider's own directory. Those trees are
materialized per session from the resolved library; a hand-made copy drifts from
the source and earns a warning about an orphan managed-marker on every run.

### 5. Then prove it arrived

An edit that the composer never resolved leaves the file looking perfect. Go to
**composition-verification** before reporting the change landed.

## Completion

- The component sits in a root this project actually reads.
- It is wired either persistently (and the command is recorded) or by an explicit
  role spec for a deliberately ephemeral test.
- The next launch is understood to be when it takes effect.
- Arrival is confirmed through `composition-verification`, not assumed.

**Validation scenario (RED).** An agent asked to give a working role the
authoring craft writes a trait into `~/.ai-hats/core/traits/`, mirroring the
shipped library's layer layout. The path resolves to nothing, the composer
reports no error, and the role is unchanged — while the file on disk looks
exactly right. Step 1 prevents it; step 5 catches it if step 1 was skipped.

## Anti-Patterns

- Mirroring the shipped `core/` / `usage/` layers inside your own root.
- Testing a component by editing the shipped library instead of overriding it by
  name from your own root.
- Leaving a promising role spec ephemeral, so the result cannot be reproduced.
- Writing `- trait-base` as `-trait-base` and wondering why the parse failed.
- Copying skill files into the provider's directory by hand.
