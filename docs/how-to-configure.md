# How-To: configure ai-hats in a project

End-to-end walkthrough for wiring ai-hats into a project: provider, role, customizations, feedback policy, venv. Start here after `pip install ai-hats` and the bash launcher.

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

| Field            | Meaning                                                                                              |
| ---------------- | ---------------------------------------------------------------------------------------------------- |
| `schema_version` | Config schema version. Set by init; do not edit.                                                     |
| `provider`       | Target LLM CLI — see [1].                                                                            |
| `active_role`    | Role injected on the next `ai-hats` launch.                                                          |
| `default_role`   | Role used when `--role` is omitted (usually identical to `active_role`).                             |
| `task_prefix`    | Prefix for `ai-hats task create` IDs.                                                                |
| `customizations` | Per-role overlays — see §4.                                                                          |
| `feedback`       | Session-retro policy and threshold — see §5.                                                         |
| `library_paths`  | Extra directories ai-hats walks for components — see [3] for full precedence.                        |
| `venv_path`      | Override the framework-managed venv — see §6.                                                        |

---

## 2. Two paths: wizard vs scripted

There are two ways to drop a config into a fresh project. Pick the wizard for human-driven first-time setup; pick scripted for CI or repeatable provisioning.

### Wizard (`ai-hats self init`) — recommended

```bash
cd ~/dev/my-project
ai-hats self init
```

The CLI writes a minimal `ai-hats.yaml` (provider, `ai_hats_dir`, `schema_version`, `task_prefix=TASK`), runs `ai-hats self update` to provision the venv, then hands off to the `initial-wizard` LLM session for six steps:

| Step | What the wizard does                                                                                                                                                                                                                            |
| ---- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1    | **Language.** Asks English / Russian / other; mirrors that for the rest of the session.                                                                                                                                                          |
| 2    | **Stack detection + role recommendation.** Reads `go.mod`, `pyproject.toml`, `setup.py`, `package.json`, `Cargo.toml`, `Makefile`. Mapping: `go.mod` → `go-dev`, `pyproject.toml`/`setup.py` → `assistant`, others → `assistant` (safe default). |
| 3    | **Customization (optional).** Shows the role's composition (`ai-hats config status`). Offers to add / remove traits, rules, skills via `ai-hats config customize …`.                                                                              |
| 4    | **Task-id prefix.** Asks for 3–6 uppercase letters (e.g. `ACME`). Default is `TASK`.                                                                                                                                                              |
| 5    | **Feedback policy.** Walks the four policies (`off` / `hint` / `smart` / `always`) — see §5. Recommends `smart`.                                                                                                                                  |
| 6    | **Finalize.** Runs `ai-hats config status` and summarizes what's in the yaml.                                                                                                                                                                     |

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

| Layer                  | Roles                                                                                              | When to pick                  |
| ---------------------- | -------------------------------------------------------------------------------------------------- | ----------------------------- |
| `library/usage/roles/` | `assistant`, `architect`, `sre`, `go-dev`, `go-dev-full`                                           | Curated user-facing — pick one. |
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
ai-hats self bump
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

For more recipes (add a whole trait, switch provider without losing settings, local-library skills, minimal config for a new project) — see [2].

---

## 5. Feedback policy

After each session ai-hats can run a structured retrospective that feeds hypotheses and proposals back into the framework. Configure it per project:

```bash
ai-hats config feedback session-retro smart
ai-hats config feedback show
```

| Policy   | Behaviour                                                                                                                                                                                | Cost                                                                       |
| -------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| `off`    | No retro.                                                                                                                                                                                | Zero overhead. You contribute nothing back to the framework.               |
| `hint`   | At session end you get a one-line nudge (`run \`ai-hats reflect\``). You decide whether to actually run it.                                                                              | Lowest friction; nothing runs behind your back.                            |
| `smart`  | A small LLM judge (default `claude-sonnet-4-6`) decides if the session was substantive enough to warrant a retro. Runs in background. **Recommended.**                                   | One small LLM call per session-end; useful retros only.                    |
| `always` | Retro after every session, regardless of triviality.                                                                                                                                     | Highest signal volume — and highest noise.                                 |

Tune the smart threshold or toggle background mode:

```bash
ai-hats config feedback session-retro smart \
  --threshold "turns=5,tool_calls=10" \
  --background
```

Pipeline architecture, what the retro emits, and how `reflect all` consumes it — see [4].

---

## 6. Venv ownership

By default ai-hats lives in a **dedicated** venv at `<ai_hats_dir>/.venv/` (default `.agent/ai-hats/.venv/`). The venv is created automatically by `ai-hats self update` or `bash bootstrap.sh`. The launcher (`~/.local/bin/ai-hats`) resolves the venv by precedence:

1. `AI_HATS_VENV` env var (absolute path, for tests / sandbox).
2. `venv_path:` field in `ai-hats.yaml` (relative or absolute).
3. Default `<ai_hats_dir>/.venv`.

### Use case A: shared system venv

```yaml
# ai-hats.yaml
venv_path: /opt/shared/ai-hats-venv
```

```bash
# One-time setup — user-owned
python3 -m venv /opt/shared/ai-hats-venv
/opt/shared/ai-hats-venv/bin/pip install "ai-hats @ git+ssh://git@github.com/muratovv/ai-hats.git"

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
- **Override venv** (`venv_path:` set) — user-owned. ai-hats never deletes or recreates it automatically; it only does `pip install -U` into it.

---

## 7. Verify & apply

Any edit to `ai-hats.yaml` is applied with one command:

```bash
ai-hats self bump          # rebuild CLAUDE.md / GEMINI.md and .claude/* from the config
ai-hats config status      # confirm provider, role, composition, feedback policy
```

If you want to see what the bump changed:

```bash
git diff CLAUDE.md ai-hats.yaml
```

Rerun `self bump` after: yaml edits, `ai-hats self update`, or any change under `library_paths`. `ai-hats config status` is a health-check — green means the next session will get the composition you expect.

---

## 8. Common pitfalls

- **Stale prompt.** Edited `ai-hats.yaml` and didn't see the change in the next session? You forgot `ai-hats self bump`. Add it to your apply-step muscle memory.
- **Overlay warnings.** `Overlay: cannot remove 'X' — not in base role` is a soft warning. The build still succeeds; the overlay is just inert. Fix by spelling the component name as it appears in `ai-hats config status`.
- **`library_paths` precedence.** Later paths win; project-local `<project>/libraries/` overrides the shipped library. Full precedence table — see [3].
- **Override venv updates.** Once `venv_path:` is set, ai-hats only does `pip install -U` into that venv — never recreates it. If it breaks (e.g. corrupted site-packages), you fix it manually.
- **Wizard re-run.** Wizard is one-shot — there is no replay flag. To rerun from scratch: `rm ai-hats.yaml && ai-hats self init`. To tweak individual fields without restarting: `ai-hats config set …` or `ai-hats config customize …`.
- **`Overlay` vs base edit.** Don't edit `library/usage/roles/<name>/config.yaml` directly — the change is lost on `ai-hats self update`. Always overlay via `customizations:` (§4).
- **Broken venv / launcher.** For symptom-to-command recovery (`command not found`, `venv missing`, corrupted site-packages, override venv broken) — see [2] §10.

---

## References

**[1]** — [`docs/glossary.md`](glossary.md) — core terms (role, provider, session, backlog, …).

**[2]** — [`docs/how-to.md`](how-to.md) — overlay cookbook: full recipes for §4, plus the recovery table referenced from §8.

**[3]** — [`docs/how-to-extend.md`](how-to-extend.md) — library layout (`library/core/` vs `library/usage/`), override precedence, recipes for adding your own roles / traits / rules / skills.

**[4]** — [`docs/how-to-feedback-loop.md`](how-to-feedback-loop.md) — reflect-session and reflect-all in practice; policy threshold tuning; what the retro emits.

**[5]** — [`docs/ARCHITECTURE.md#component-model`](ARCHITECTURE.md#component-model) — formal composition rules.

**[6]** — [`docs/reflect.md`](reflect.md) — retrospective pipeline architecture and schema dispatch.

**[7]** — [`library/core/roles/initial-wizard/config.yaml`](../library/core/roles/initial-wizard/config.yaml) — wizard source. If §2 drifts from this file, this file is the ground truth.

[1]: glossary.md
[2]: how-to.md
[3]: how-to-extend.md
[4]: how-to-feedback-loop.md
[5]: ARCHITECTURE.md#component-model
[6]: reflect.md
[7]: ../library/core/roles/initial-wizard/config.yaml
