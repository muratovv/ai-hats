# How-To: configure ai-hats in a project

End-to-end walkthrough for wiring ai-hats into a project: provider, role, customizations, feedback policy, venv. Start here after installing the bash launcher and running `ai-hats self init` (or `uvx ai-hats self init`) on the project.

> Core terms (role, provider, session, …) are defined in [1]. This doc is the practical narrative — pair it with [2] for the cookbook of overlay snippets and with [3] when you need to ship your own roles or skills.

---

## 1. What's in `ai-hats.yaml`

The single config file at the repo root, written by `ai-hats self init` and edited via `ai-hats config …`. Never hand-edit it during a wizard session — but reading it is fine, and direct edits work outside of init.

```yaml
schema_version: 2
provider: claude               # claude | gemini
active_role: assistant         # the role used for the next `ai-hats` session
default_role: assistant        # fallback when `--role` is omitted
task_prefix: ACME              # task ids → ACME-001, ACME-002, …

customizations:                # per-role overlays (add / remove / inject)
  assistant:
    add:
      skills:
        - kubernetes-ops
    injection_append: |
      ## PROJECT NOTES
      - All infra changes go through ArgoCD PRs.

feedback:
  session_retro:
    policy: smart              # off | hint | smart | always
    background: true
    threshold:
      turns: 5
      tool_calls: 10

library_paths:                 # extra component sources (last wins)
  - .agent/library
# venv_path: /opt/shared/ai-hats-venv   # optional override (see §6)
```

| Field              | Meaning                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| ------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `schema_version`   | Config schema version (yaml format). Set by init; do not edit.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `migration_step`   | Counter for one-shot migrations replayed at bump time (HATS-471). Set by init / advanced automatically by `ai-hats self update`; do not edit.                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `provider`         | Target LLM CLI — see [1].                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| `active_role`      | Role injected on the next `ai-hats` launch.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `default_role`     | Role used when `--role` is omitted (usually identical to `active_role`).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `task_prefix`      | Prefix for `ai-hats task create` IDs.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| `ai_hats_dir`      | Framework state directory (library, tracker, sessions, STATE.md). Default `.agent/ai-hats`. To relocate post-init: `ai-hats config set --ai-hats-dir <PATH>` — do **not** hand-edit (it leaves orphaned files).                                                                                                                                                                                                                                                                                                                                                 |
| `customizations`   | Per-role overlays — see §4.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `feedback`         | Session-retro policy and threshold — see §5.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `library_paths`    | Extra directories ai-hats walks for components — see [3] for full precedence.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `venv_path`        | Override the framework-managed venv — see §6.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `manage_gitignore` | When `true` (default) ai-hats adds `<ai_hats_dir>/` to `.gitignore` once at init. Toggle via `ai-hats config set --no-manage-gitignore`.                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `harness`          | Install-source channel `ai-hats self update` pulls from (HATS-764): `channel: local` (editable working tree, `path:`), `edge` (own repo HEAD, `repo:`), or `stable` (latest PyPI release — the default; omitted from yaml when unset). Set via `ai-hats config set --channel {local\|edge\|stable} [--repo URL] [--path DIR]` — do **not** hand-edit. On a dev host whose ai-hats is an editable clone, `ai-hats self init` auto-seeds `channel: local` (HATS-938; override with `self init --channel …`). `AI_HATS_REPO_URL` overrides the edge repo. See [1]. |
| `worktree`         | Base ≠ merge-target for the worktree FSM (fork/dogfood workflows, HATS-942). Optional; unset ⇒ today's `master`/`main` behavior. See [The `worktree` block](#the-worktree-block--fork-workflows-base--merge-target) below.                                                                                                                                                                                                                                                                                                                                      |

---

## 1a. The `worktree` block — fork workflows (base ≠ merge target)

For a fork/dogfood repo whose dev trunk is **not** the upstream default branch — e.g. `main` is a pristine mirror of upstream and `fork-main` is the local integration trunk — point the worktree FSM at the split (HATS-942):

```yaml
worktree:
  base_branch: main        # task worktrees are cut FROM this (clean upstream base)
  merge_target: fork-main  # `wt merge` / `task transition <id> done` land HERE
```

- **`base_branch`** — the `git worktree add` start-point. Unset ⇒ cut from the main-repo HEAD (today's behavior).
- **`merge_target`** — where `wt merge` lands, and the branch the create-time HEAD guard requires. Unset ⇒ falls back to `base_branch`, else the `master`/`main` set-membership.
- **Operating rule.** Stay on `merge_target` (`fork-main`) for the whole task lifecycle — both the create-time and merge-time guards require HEAD there. `base_branch` (`main`) is only read as a start-point; you never check it out for dev, so it stays a clean mirror.
- **Contract.** Both keys optional; **both unset ⇒ behavior byte-identical to the historical hardcoded `("master","main")`**. A configured branch that does not exist in the repo fails loud.

---

## 2. Two paths: wizard vs scripted

There are two ways to drop a config into a fresh project. Pick the wizard for human-driven first-time setup; pick scripted for CI or repeatable provisioning.

### Wizard (`ai-hats self init`) — recommended

```bash
cd ~/dev/my-project
ai-hats self init
```

The CLI asks for the provider, writes a minimal `ai-hats.yaml` (provider, `ai_hats_dir`, `schema_version`, `task_prefix=TASK`), runs `ai-hats self update` to provision the venv, then hands off to the `initial-wizard` LLM session for seven steps:

| Step | What the wizard does                                                                                                                                                                                                                                                      |
| ---- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1    | **Language.** Asks English / Russian / other; mirrors that for the rest of the session.                                                                                                                                                                                   |
| 2    | **Workspace setup (optional).** Single y/N gate; on `y` walks through `ai_hats_dir`, `venv_path`, `manage_gitignore` with trade-off explanations and applies via `ai-hats config set --ai-hats-dir / --venv / --no-manage-gitignore`. Most users skip.                    |
| 3    | **Stack detection + role recommendation.** Reads `go.mod`, `pyproject.toml`, `setup.py`, `package.json`, `Cargo.toml`, `Makefile`. Mapping: `go.mod` → `go-dev`, `pyproject.toml`/`setup.py` → `dev-python` (clean Python baseline), others → `assistant` (safe default). |
| 4    | **Customization (optional).** Shows the role's composition (`ai-hats config status`). Offers to add / remove traits, rules, skills via `ai-hats config customize …`.                                                                                                      |
| 5    | **Task-id prefix.** Asks for 3–6 uppercase letters (e.g. `ACME`). Default is `TASK`.                                                                                                                                                                                      |
| 6    | **Feedback policy.** Walks the four policies (`off` / `hint` / `smart` / `always`) — see §5. Recommends `smart`.                                                                                                                                                          |
| 7    | **Finalize.** Runs `ai-hats config status` and summarizes what's in the yaml.                                                                                                                                                                                             |

> Authoritative source for the steps above is [7]. If the wizard drifts from this doc, the wizard is right — open a PR to sync the doc.

### Scripted (`--no-wizard`) — for CI

```bash
ai-hats self init -p claude -r assistant --no-wizard --task-prefix ACME
```

Either `--no-wizard` or non-TTY stdin disables the LLM step; the CLI writes the yaml directly with the provided flags. Combine with the bootstrap-time flags that are tedious to change later:

```bash
ai-hats self init -p claude -r go-dev --no-wizard \
  --ai-hats-dir .ai \                  # framework directory (default: .agent/ai-hats)
  --venv ~/.venvs/myproj \             # point at an existing venv (§6)
  --no-manage-gitignore                # don't auto-add ai-hats entries to .gitignore
```

### Re-running

Wizard is one-shot — to change provider / role / prefix later use `ai-hats config set` and `ai-hats config customize`. To restart cleanly: `rm ai-hats.yaml` and rerun `ai-hats self init`.

---

## 3. Pick a role

A role is a composition of traits + rules + skills + injection — definition in [1]. The shipped library is layered:

| Layer                  | Roles                                                                                                                  | When to pick                                       |
| ---------------------- | ---------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------- |
| `library/usage/roles/` | `assistant`, `dev-python`, `dev-web`, `architect`, `sre`, `go-dev`, `go-dev-full`                                      | Curated user-facing — pick one.                    |
| `library/core/roles/`  | `initial-wizard`, `session-reviewer`, `judge`, `judge-for-role`, `auditor-for-role`, `hypothesis-intake`, `test-agent` | Engine-internal — do **not** pick as your primary. |

Bring-your-own roles go under `~/.ai-hats/roles/<name>/` or `<project>/libraries/roles/<name>/`. Override precedence and full library layout — see [3].

```bash
ai-hats config set -r assistant       # change role
ai-hats list roles                    # see all available
ai-hats config status                 # show the composition that will be injected
```

---

## 4. Customize a role

You can overlay add / remove / inject without forking the base role. Two equivalent forms — CLI and YAML.

**Scenario.** Project uses `sre` but needs an extra `kubernetes-ops` skill, doesn't need the default `network-documentation`, and has its own infra notes that should land in the system prompt.

**CLI form:**

```bash
ai-hats config customize sre --add-skill kubernetes-ops
ai-hats config customize sre --remove-skill network-documentation
ai-hats config customize sre --injection-append "All infra changes via ArgoCD PRs."
ai-hats self init
```

**YAML form** (same end state):

```yaml
customizations:
  sre:
    add:
      skills:
        - kubernetes-ops
    remove:
      skills:
        - network-documentation
    injection_append: |
      All infra changes via ArgoCD PRs.
```

Inspect or reset:

```bash
ai-hats config customize sre --show
ai-hats config customize sre --reset
```

> If `--remove-skill X` references a skill not in the base composition, ai-hats emits an `Overlay: cannot remove ...` warning and continues. Not an error — just an alert that the overlay is silently inert.

### Two layers: project and global

Every `customize` flag accepts an optional `--global` to route the edit
into `~/.ai-hats/customizations.yaml` instead of the project's
`ai-hats.yaml`. Same flags, same schema, different file. The user-level
overlay applies to **every** project you open.

```bash
ai-hats config customize sre --add-skill kubernetes-ops              # project
ai-hats config customize sre --add-skill kubernetes-ops --global     # user-wide
```

Inspect each layer:

```bash
ai-hats config customize sre --show               # both layers
ai-hats config customize sre --show --global      # only user-level
ai-hats config customize sre --show --project     # only project
```

`--global` and `--project` are mutually exclusive on writes (a `--global`
write goes to the user file; without `--global`, the write goes to the
project).

**Compose order** is built-in role → global overlay → project overlay →
final composition. **Project wins on conflict** because it is applied
last:

| global      | project     | result             |
| ----------- | ----------- | ------------------ |
| `add: X`    | `remove: X` | no `X`             |
| `remove: X` | `add: X`    | `X` at the tail    |
| `add: X`    | `add: X`    | `X` (deduplicated) |

Putting the same name in both `add` and `remove` **within a single layer**
is a documented "move-to-end" reorder operation — see the recipe in
`docs/how-to.md` §4c.

Run `ai-hats config status` to see the merged dependency tree with a
source-tag per node — `(built-in)`, `(global)`, or `(project)` — so you
always know which layer contributed each trait, rule, and skill.

For more recipes (add a whole trait, switch provider without losing settings, local-library skills, minimal config for a new project) — see [2].

---

## 5. Feedback policy

After each session ai-hats can run a structured retrospective that feeds hypotheses and proposals back into the framework. Configure it per project:

```bash
ai-hats config feedback session-retro smart
ai-hats config feedback show
```

| Policy   | Behaviour                                                                                                                                              | Cost                                                         |
| -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------ |
| `off`    | No retro.                                                                                                                                              | Zero overhead. You contribute nothing back to the framework. |
| `hint`   | At session end you get a one-line nudge (`run \`ai-hats reflect\``). You decide whether to actually run it.                                            | Lowest friction; nothing runs behind your back.              |
| `smart`  | A small LLM judge (default `claude-sonnet-4-6`) decides if the session was substantive enough to warrant a retro. Runs in background. **Recommended.** | One small LLM call per session-end; useful retros only.      |
| `always` | Retro after every session, regardless of triviality.                                                                                                   | Highest signal volume — and highest noise.                   |

Tune the smart threshold or toggle background mode:

```bash
ai-hats config feedback session-retro smart \
  --threshold "turns=5,tool_calls=10" \
  --background
```

Pipeline architecture, what the retro emits, and how `reflect all` consumes it — see [4].

---

## 6. Venv ownership

ai-hats is a **host tool driven by the launcher** (`~/.local/bin/ai-hats`) and `python -m ai_hats` — not a dependency of your project's application venv. The launcher resolves a per-project **managed venv** and exec's `<venv>/bin/python -m ai_hats`. There is **no `<venv>/bin/ai-hats` console script** (removed in HATS-790); the only `bin/ai-hats` is the host launcher itself. A stale `ai-hats` once `pip install`ed into a project app-venv is a *stray shadow* — if it runs ahead of the launcher, ai-hats refuses-and-instructs (the self-location guard, exit 3) instead of running mis-resolved. Recovery for a shadowed / unrunnable install — see [8].

By default ai-hats lives in a **dedicated** venv at `<ai_hats_dir>/.venv/` (default `.agent/ai-hats/.venv/`). The venv is created automatically by `ai-hats self update` or `bash bootstrap.sh`. The launcher (`~/.local/bin/ai-hats`) resolves the venv by precedence:

1. `AI_HATS_VENV` env var (absolute path, for tests / sandbox).
2. `venv_path:` field in `ai-hats.yaml` (relative or absolute).
3. Default `<ai_hats_dir>/.venv`.

To set or change `venv_path` post-init:

```bash
ai-hats config set --venv ~/.venvs/myproj   # point at a custom venv
ai-hats config set --no-venv                # reset to managed default
```

The wizard's Step 2 (workspace setup) drives the same commands interactively.

### Use case A: shared system venv

```yaml
# ai-hats.yaml
venv_path: /opt/shared/ai-hats-venv
```

```bash
# One-time setup — user-owned
uv venv /opt/shared/ai-hats-venv
uv pip install --python /opt/shared/ai-hats-venv/bin/python "ai-hats @ git+https://github.com/muratovv/ai-hats.git"

# After that the launcher resolves the override automatically
cd ~/dev/my-project
ai-hats config status
```

### Use case B: re-use the project's own venv

```yaml
# ai-hats.yaml
venv_path: .venv          # an existing project venv at the repo root
```

⚠️ ai-hats and your project's dependencies live in the same venv — version conflicts are possible. Conscious trade-off (override = user-owned).

### Ownership invariant

- **Default venv** (`<ai_hats_dir>/.venv/`) — framework-managed. `ai-hats self update` may recreate it wholesale (for example after a system Python upgrade).
- **Override venv** (`venv_path:` set) — user-owned. ai-hats never deletes or recreates it automatically; it only does `uv pip install -U` into it.

---

## 7. Verify & apply

Any edit to `ai-hats.yaml` is applied with one command:

```bash
ai-hats self init          # rebuild CLAUDE.md / GEMINI.md and .claude/* from the config
ai-hats config status      # confirm provider, role, composition, feedback policy
```

If you want to see what the bump changed:

```bash
git diff CLAUDE.md ai-hats.yaml
```

Rerun `self init` after: yaml edits, `ai-hats self update`, or any change under `library_paths`. `ai-hats config status` is a health-check — green means the next session will get the composition you expect.

---

## 8. Common pitfalls

- **Stale prompt.** Edited `ai-hats.yaml` and didn't see the change in the next session? You forgot `ai-hats self init`. Add it to your apply-step muscle memory.
- **Overlay warnings.** `Overlay: cannot remove 'X' — not in base role` is a soft warning. The build still succeeds; the overlay is just inert. Fix by spelling the component name as it appears in `ai-hats config status`.
- **`library_paths` precedence.** Later paths win; project-local `<project>/libraries/` overrides the shipped library. Full precedence table — see [3].
- **Override venv updates.** Once `venv_path:` is set, ai-hats only does `uv pip install -U` into that venv — never recreates it. If it breaks (e.g. corrupted site-packages), you fix it manually.
- **Wizard re-run.** Wizard is one-shot — there is no replay flag. To rerun from scratch: `rm ai-hats.yaml && ai-hats self init`. To tweak individual fields without restarting: `ai-hats config set …` or `ai-hats config customize …`.
- **`Overlay` vs base edit.** Don't edit `library/usage/roles/<name>/config.yaml` directly — the change is lost on `ai-hats self update`. Always overlay via `customizations:` (§4).
- **Broken venv / launcher.** For symptom-to-command recovery (`command not found`, `venv missing`, corrupted site-packages, override venv broken, a stray shadow, or an unrunnable install needing `bootstrap.sh --repair`) — see [2] §10.
- **`WARN: ... dropping unknown field` on load.** A NEWER ai-hats wrote a field this (older) binary doesn't know. It is **preserved, not lost** — the field round-trips through `save()` (HATS-792) so a save from the older binary won't delete it; the WARN just flags that the typed model ignored it. Run `ai-hats self update` to use the field properly. By contrast, `schema_version N is newer than this ai-hats` **fails loud** (not a warning): the on-disk format is too new to read safely — update before editing.

---

## References

**[1]** — [`docs/glossary.md`](glossary.md) — core terms (role, provider, session, backlog, …).

**[2]** — [`docs/how-to.md`](how-to.md) — overlay cookbook: full recipes for §4, plus the recovery table referenced from §8.

**[3]** — [`docs/how-to-extend.md`](how-to-extend.md) — library layout (`library/core/` vs `library/usage/`), override precedence, recipes for adding your own roles / traits / rules / skills.

**[4]** — [`docs/how-to-feedback-loop.md`](how-to-feedback-loop.md) — reflect-session and reflect-all in practice; policy threshold tuning; what the retro emits.

**[5]** — [`docs/ARCHITECTURE.md#component-model`](ARCHITECTURE.md#component-model) — formal composition rules.

**[6]** — [`docs/reflect.md`](reflect.md) — retrospective pipeline architecture and schema dispatch.

**[7]** — [`ai_hats_library/core/roles/initial-wizard/config.yaml`](../packages/ai-hats-library/src/ai_hats_library/core/roles/initial-wizard/config.yaml) — wizard source. If §2 drifts from this file, this file is the ground truth.

**[8]** — [`docs/how-to.md#10-recovery-scenarios`](how-to.md#10-recovery-scenarios) — symptom→command recovery table, including the `bootstrap.sh --repair` out-of-band hatch and the stray-shadow case.
