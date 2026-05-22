# Documentation index

Single source of truth for the project's documentation surface. The
[initial-wizard](../library/core/roles/initial-wizard/config.yaml) role
reads this file at session start; maintainers keep it in sync when
adding, removing, or renaming files under `docs/` (enforced by the
`pre-commit-docs-index.sh` hook from the `git-mastery` skill).

## Wizard companion docs (per step)

Primary references the [initial-wizard](../library/core/roles/initial-wizard/config.yaml)
role opens during each step of first-time configuration:

- **Step 1 — language**: (no docs).
- **Step 2 — workspace**: [how-to-configure.md §1](how-to-configure.md#1-whats-in-ai-hatsyaml) (fields),
  [§6](how-to-configure.md#6-venv-ownership) (venv).
- **Step 3 — stack + role**: [glossary.md](glossary.md) (Role definition),
  [how-to-extend.md](how-to-extend.md) (layered library, custom roles).
- **Step 4 — customization**: [how-to.md](how-to.md) (overlay recipes 1–7),
  [how-to-configure.md §4](how-to-configure.md#4-customize-a-role).
- **Step 5 — task-prefix**: [how-to-configure.md §1](how-to-configure.md#1-whats-in-ai-hatsyaml) (fields table).
- **Step 6 — feedback policy**: [how-to-feedback-loop.md](how-to-feedback-loop.md),
  [how-to-configure.md §5](how-to-configure.md#5-feedback-policy).
- **Step 7 — finalize**: [how-to-configure.md §7](how-to-configure.md#7-verify--apply).

## Companion docs catalog

Full list of documentation files maintained in this repo. Open via Read
tool when a question goes beyond built-in instructions.

| File | Topic | When to read |
|---|---|---|
| [how-to-configure.md](how-to-configure.md) | Full configuration walkthrough — fields, paths, customization, feedback, verification | Steps 2, 4, 5, 6, 7 |
| [how-to-extend.md](how-to-extend.md) | Bring-your-own roles / traits / skills / rules; layered library precedence; custom verbs via shell aliases | Step 3, 4; custom-component work |
| [how-to.md](how-to.md) | Overlay cookbook — 7 recipes for adjusting a role on the fly | Step 4 |
| [how-to-feedback-loop.md](how-to-feedback-loop.md) | Feedback policies (off / hint / smart / always) + reflection internals | Step 6 |
| [glossary.md](glossary.md) | Core terminology — role, provider, session, trait, skill, rule | Any step where a term is unclear |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Composition model, session lifecycle, reflection loop, backlog FSMs | Curious; debugging composition |
| [how-to-orchestration.md](how-to-orchestration.md) | Multi-role orchestration via sub-agents | Advanced |
| [how-to-cli-integrations.md](how-to-cli-integrations.md) | Integrating external CLIs (gcloud, gh, gWorkspace) into a role | Advanced |
| [how-to-advanced.md](how-to-advanced.md) | Edge cases, power-user knobs, escape hatches | Advanced |
| [how-to-backlog.md](how-to-backlog.md) | Task / hypothesis / proposal lifecycle and CLI surface | Advanced |
| [reflect.md](reflect.md) | Reflection-mode internals — hypothesis/proposal triage flow | Advanced |
| [RELEASING.md](RELEASING.md) | Release process — SemVer bump, CHANGELOG roll-up, tag | Maintainers |

## Other docs

- [`adr/`](adr/) — architecture decision records (one file per decision).
- [`integrations/`](integrations/) — provider-specific notes (e.g. Google Workspace CLI setup).
- [`assets/`](assets/) — diagrams, logo, demo gif (referenced from README and ARCHITECTURE).

## Maintenance

When adding, removing, or renaming a file under `docs/`:

1. Update the **Companion docs catalog** table — add row with topic / when-to-read, or remove the obsolete row.
2. If the file is relevant to a specific wizard step, update the **Wizard companion docs** section too.
3. Stage `docs/INDEX.md` alongside the doc change — the
   [`pre-commit-docs-index.sh`](../library/core/skills/git-mastery/git_hooks/pre-commit-docs-index.sh)
   hook blocks the commit otherwise.

See also: [doc-protocol](../library/usage/skills/doc-protocol/SKILL.md) skill
for the full pre-commit verification checklist for doc tasks.
