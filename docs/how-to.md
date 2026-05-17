# How-To: ai-hats.yaml config examples

A collection of common tasks you hit when wiring ai-hats into a project: extending a role with a skill, removing an unneeded component, dropping in your own local skill, switching providers. Each example is a self-contained `ai-hats.yaml` fragment plus the commands to apply it.

> Full CLI reference with descriptions and options — `ai-hats --tree` (or a subtree: `ai-hats --tree config`, `ai-hats --tree task hyp`).

> All changes to `ai-hats.yaml` are applied with `ai-hats self bump` (rebuilds `CLAUDE.md` / `GEMINI.md` and `.claude/*` from the config). Built-in roles (under `library/{core,usage}/roles/` inside the installed package) should **not** be edited directly — use `customizations` (overlay) instead. To author your own roles see [how-to-extend.md](how-to-extend.md).
>
> Any overlay edit can be done in two ways:
> 1. **CLI:** `ai-hats config customize <role> --add-skill <name> | --remove-skill <name> | --add-trait <name> | --injection-append "<text>"`. The command writes into `ai-hats.yaml` itself.
> 2. **By hand:** edit `customizations:` in `ai-hats.yaml` (examples below).
>
> Both ways are equivalent. Below — the resulting YAML, so you can see what you get.

---

## 1. Add a new skill to an existing role

**Scenario:** the project uses the `sre` role, but you want an extra skill (for example, `kubernetes-ops`) that's not in the base composition.

```yaml
schema_version: 2
provider: claude
active_role: sre
default_role: sre
task_prefix: OPS

customizations:
  sre:
    add:
      skills:
        - kubernetes-ops
```

CLI equivalent:

```bash
ai-hats config customize sre --add-skill kubernetes-ops
ai-hats self bump
```

Verification: after `ai-hats self bump` the skill appears in the `## AVAILABLE SKILLS` section of the generated `CLAUDE.md`.

---

## 2. Remove an unneeded skill from a role

**Scenario:** the base `sre` role pulls in `network-documentation`, but networking is owned by another team for this project — extra noise in the prompt.

```yaml
customizations:
  sre:
    remove:
      skills:
        - network-documentation
```

If you try to remove something that's not in the base role — you get a warning `Overlay: cannot remove skill 'X' — not in base role`, but the build does not fail.

---

## 3. Combine add + remove + project notes

**Scenario:** trim down the `sre` role for a specific project and capture infrastructure specifics right in the injection.

```yaml
customizations:
  sre:
    add:
      skills:
        - kubernetes-ops
      traits: []
      rules: []
    remove:
      skills:
        - network-documentation
    injection_append: |
      ## PROJECT NOTES
      - Clusters: prod-eu, prod-us, staging
      - All infra changes go through ArgoCD PRs
      - Secrets — only in Vault, no .env in the repo
```

`injection_append` is appended **after** the base role's injection — handy for project rules without forking the role.

---

## 4. Wire a local (custom) skill from `.agent/library`

**Scenario:** the skill is project-specific and doesn't belong in the shared ai-hats library.

File layout:

```
my-project/
├── ai-hats.yaml
└── .agent/
    └── library/
        └── skills/
            └── deploy-pipeline/
                └── SKILL.md
```

`ai-hats.yaml`:

```yaml
schema_version: 2
provider: claude
active_role: sre

# Local libraries take precedence over the built-in ones
library_paths:
  - .agent/library

customizations:
  sre:
    add:
      skills:
        - deploy-pipeline
```

The skill name must match the directory name under `library_paths/skills/`.

---

## 5. Add a whole trait (e.g. dev::python)

**Scenario:** Python tooling has appeared in an SRE project, and you want to pull the entire Python rules/skills stack in one move.

```yaml
customizations:
  sre:
    add:
      traits:
        - dev::python
```

The `<group>::<trait>` syntax points to a trait inside `library/usage/traits/<group>/<trait>/` (built-in) or any user library path. The trait pulls in its own rules + skills + injection.

---

## 6. Minimal config for a new project

**Scenario:** a fresh project, no overlays — just pick a role and a provider.

```yaml
schema_version: 2
provider: claude
active_role: assistant
default_role: assistant
task_prefix: PROJ
library_paths: []

feedback:
  session_retro:
    policy: smart
```

This is equivalent to what `bootstrap.sh` generates on first install.

---

## 7. Switch provider without losing settings

```bash
ai-hats config set -p gemini   # switch to Gemini
ai-hats self bump              # rebuild GEMINI.md
```

In `ai-hats.yaml` only `provider: gemini` changes. The role composition stays the same — both providers read the same libraries.

---

## 8. Applying changes: checklist

After any `ai-hats.yaml` edit:

```bash
ai-hats self bump          # rebuild the prompt
ai-hats config status      # confirm everything was picked up
```

If there are many changes and you just want the diff:

```bash
git diff CLAUDE.md ai-hats.yaml
```

---

## Overlay structure (reference)

```yaml
customizations:
  <role-name>:
    add:
      traits: [...]    # add a whole trait
      rules:  [...]    # add individual rules
      skills: [...]    # add individual skills
    remove:
      traits: [...]    # remove a trait from the base composition
      rules:  [...]
      skills: [...]
    injection_append: |
      ## ...           # text appended AFTER the role's injection
```

Empty sections can be omitted. If `customizations.<role>` is fully empty — the overlay is not applied.

---

## 9. Configurable venv_path

Moved to the narrative walkthrough — see [`docs/how-to-configure.md` §6](how-to-configure.md) for default vs override, ownership invariant, and the two scenarios (shared system venv, re-use a project venv).

---

## 10. Recovery scenarios

| Symptom | Command |
|---|---|
| `ai-hats: command not found` (fresh host) | `curl -sSL https://github.com/muratovv/ai-hats/raw/master/scripts/install-launcher.sh \| bash` (if the repo is private — clone it and run `bash scripts/install-launcher.sh`) |
| `ai-hats: venv missing at ...` (no venv) | `ai-hats self update` |
| `ai-hats: venv exists but ai-hats binary is missing` | `ai-hats self update` |
| System Python upgrade (the Proxmox case) | `ai-hats self update` — the launcher auto-recreates the default venv |
| Import error / corrupted site-packages | `rm -rf .agent/ai-hats/.venv && ai-hats self update` |
| Override venv broken | `python3 -m venv <override-path> && <override-path>/bin/pip install 'ai-hats @ git+ssh://...'` (user-managed) |
| Full project wipe (data loss!) | `rm -rf .agent/ai-hats/ && ai-hats self update && ai-hats self init -r <role> -p <provider>` |

Detailed migration guide for projects moving from pipx → launcher: `docs/migration.md`.

---

## See also

- [`docs/how-to-configure.md`](how-to-configure.md) — narrative walkthrough for first-time setup (wizard, role pick, customization, feedback policy, venv).
- [`docs/how-to-feedback-loop.md`](how-to-feedback-loop.md) — setup and usage of the reflect-session / reflect-all cycle (policies, hypotheses, harness validation).
- [`docs/how-to-extend.md`](how-to-extend.md) — shipped library layout and recipes for your own roles / traits / rules / skills.
- [`docs/reflect.md`](reflect.md) — retrospective pipeline architecture.
