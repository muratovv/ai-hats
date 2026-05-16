<p align="center">
  <img src="docs/assets/logo-256.png" alt="ai-hats" width="180" />
</p>

<h1 align="center">ai-hats</h1>

<p align="center">
  <strong>Do. Reflect. Repeat.</strong>
</p>

<p align="center">
  <em>Compose AI agents from reusable roles, then run an automatic retrospective after every session.</em><br>
  <em>One role set works for both Claude and Gemini.</em>
</p>

<p align="center">
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-green.svg"></a>
  <img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-blue.svg">
  <img alt="Status: Beta" src="https://img.shields.io/badge/status-beta-orange.svg">
  <a href="https://github.com/muratovv/ai-hats/commits/master"><img alt="Last commit" src="https://img.shields.io/github/last-commit/muratovv/ai-hats"></a>
  <a href="https://github.com/muratovv/ai-hats/issues"><img alt="Open issues" src="https://img.shields.io/github/issues/muratovv/ai-hats"></a>
</p>

<p align="center">
  <img src="docs/assets/demo.gif" alt="ai-hats — composition + real sessions + active hypotheses" width="900" />
</p>

<p align="center">
  <strong>English</strong> · <a href="docs/README.ru.md">Русский</a>
</p>

## Why ai-hats?

Have you ever watched the same AI agent step on the same rakes across projects? Forgetting your conventions, skipping the planning step, falling back to the same anti-pattern. Copy-pasting `CLAUDE.md` doesn't scale: edits drift across projects, and a fix in one rarely makes it back to the others.

ai-hats answers this with two things:

- **Roles as compositions of reusable components** — `traits`, `rules`, `skills`, and `hooks` are assembled into a role once and injected into the system prompt of any provider (Gemini / Claude). A fix to one component reaches every role that includes it via `ai-hats self bump`.
- **Deep reflection after every session** — a structured retrospective with a factual layer (metrics, files, commits) plus an LLM narrative that delivers verdicts on active hypotheses and votes on improvement proposals. Patterns observed across 3–5 sessions become new rules and skills, and the loop closes.

```
roles/assistant ── trait-base + trait-agent + dev::python
                   ├── rules: git_workflow, tdd
                   ├── skills: backlog-manager, git-mastery
                   └── injection → GEMINI.md / CLAUDE.md
```

## Quick start

A bash launcher in `~/.local/bin/ai-hats` (one-time per host) plus a per-project venv in `<ai_hats_dir>/.venv/`. Get help for any command with `ai-hats --help`. View the full CLI tree with `ai-hats --tree`.

### 1. Install the launcher (once per host)

```bash
curl -sSL https://github.com/muratovv/ai-hats/raw/master/scripts/install-launcher.sh | bash
```

Drops a ~30-line bash launcher into `~/.local/bin/ai-hats`. If `~/.local/bin/` isn't on `$PATH`, the installer prompts you to add it.

### 2. Wire ai-hats into a project

```bash
cd ~/dev/my-project
ai-hats self update                            # creates the venv at .agent/ai-hats/.venv + installs ai-hats
ai-hats config set -r go-dev -p claude         # pick a role + provider (auto-initialises the project)
```

`config set` writes `ai-hats.yaml` and `CLAUDE.md` / `GEMINI.md` for the chosen composition.

### 3. Use it

```bash
ai-hats                       # start a session with current settings
ai-hats --resume              # flags pass through to the provider (claude / gemini)
ai-hats config status         # health-check the composition
ai-hats self bump             # rebuild the prompt after library changes
ai-hats self update           # update ai-hats and auto-bump
```

`ai-hats self update` is self-healing: if a system Python upgrade breaks the venv, it is rebuilt automatically (default venvs only; override venvs are user-owned).

Alternative install paths (bash bootstrap from a clone, override venv, migrating from pipx, developing ai-hats itself) live in **[docs/how-to.md](docs/how-to.md)** and **[docs/migration.md](docs/migration.md)**.

## CLI

> **The full command reference with descriptions and options — `ai-hats --tree`**
> (equivalent to `ai-hats --help --tree`).
>
> Subtrees: `ai-hats --tree <group>` (e.g. `ai-hats --tree wt`)
> or deeper: `ai-hats --tree task hyp`.

Eight top-level groups:

| Group     | What it does                                                            |
| --------- | ----------------------------------------------------------------------- |
| `agent`   | Run a role as a sub-agent inside an isolated worktree                   |
| `config`  | Read / edit `ai-hats.yaml` (provider, role, customizations, feedback)   |
| `list`    | Discovery: roles / skills / rules / traits / providers / tokens         |
| `reflect` | Feedback loop — per-session vote and bulk triage of HYP / PROP          |
| `self`    | Tool lifecycle: init / bump / update / clean / rollback                 |
| `session` | Observability: list / show / audit / retro for sessions                 |
| `task`    | Backlog: task / hyp / proposal cards with a state machine               |
| `wt`      | Git worktrees: create / merge / discard / exec / env                    |

Common scenarios:

```bash
# Interactive session with role injection
ai-hats                                    # current settings
ai-hats -p claude -r architect             # override provider and role
ai-hats --tag client=acme                  # custom tags in metrics.json

# Sub-agent in an isolated worktree
ai-hats agent sre --task "investigate alert XYZ"

# Lifecycle
ai-hats config set -r <role> -p <provider> # pick role and provider (auto-init)
ai-hats self update && ai-hats self bump   # update ai-hats and rebuild the prompt
ai-hats config status                      # health-check the composition
```

Full reference — `ai-hats --tree`.

## Architecture

Roles compose from traits + rules + skills, a flat model, a task state machine, multi-provider injection. The full tour of the internal model, directory layout, skill format, and a sample `config.yaml` — see **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**.
