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
  <a href="https://github.com/muratovv/ai-hats/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/muratovv/ai-hats/actions/workflows/ci.yml/badge.svg?branch=master"></a>
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
  <sub>Reading in another language? Chrome / Edge / Safari Reader Mode translate this site cleanly — no separate translations are maintained.</sub>
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

> Names and core terms (role, session, reflect, backlog, …) — see [1].

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
ai-hats self init                              # interactive wizard (recommended)
```

`ai-hats self init` is the human-friendly bootstrap. It:

1. Pulls the latest ai-hats from GitHub.
2. Asks for a provider (smart default by `~/.claude` / `~/.gemini` presence) and writes a minimal `ai-hats.yaml`.
3. Hands off to the `initial-wizard` LLM session, which detects your stack, recommends a base role, helps with customizations, and configures the feedback (session-retro) policy — all via `ai-hats config …` commands.

**Scripted / CI variant** — pass both flags to skip the wizard:

```bash
ai-hats self init -p claude -r go-dev --no-wizard          # writes ai-hats.yaml directly
```

Bootstrap-time flags that are tedious to change later (the wizard also
asks about these in an opt-in "advanced setup" branch):

```bash
ai-hats self init -p claude -r go-dev --no-wizard \
  --ai-hats-dir .ai \                  # framework directory (default: .agent/ai-hats)
  --venv ~/.venvs/myproj \             # point at an existing venv instead of the managed one
  --no-manage-gitignore                # do not auto-add ai-hats entries to .gitignore
```

### 3. Use it

```bash
ai-hats                       # start a session with current settings
ai-hats --resume              # flags pass through to the provider (claude / gemini)
ai-hats config status         # health-check the composition
ai-hats self bump             # rebuild the prompt after library changes
ai-hats self update           # update ai-hats and auto-bump
```

`ai-hats self update` is self-healing: if a system Python upgrade breaks the venv, it is rebuilt automatically (default venvs only; override venvs are user-owned).

Full configuration walkthrough (wizard, role pick, customization, feedback policy, venv) → [2].

Alternative install paths (bash bootstrap from a clone, override venv, developing ai-hats itself) live in [3].

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
| `task`    | Backlog: task / hyp / proposal cards with a state machine — recipes in [4] |
| `wt`      | Git worktrees: create / merge / discard / exec / env — recipes in [5] §2 |

Common scenarios:

```bash
# Interactive session with role injection
ai-hats                                    # current settings
ai-hats -p claude -r architect             # override provider and role
ai-hats --tag client=acme                  # custom tags in metrics.json

# Sub-agent in an isolated worktree
ai-hats agent sre --task "investigate alert XYZ"

# Lifecycle
ai-hats self init                          # interactive bootstrap (wizard) — new projects
ai-hats config set -r <role> -p <provider> # change role / provider in an existing project
ai-hats self update && ai-hats self bump   # update ai-hats and rebuild the prompt
ai-hats config status                      # health-check the composition
```

Full reference — `ai-hats --tree`.

## Customization

The shipped library splits into `library/core/` (engine fundament) and `library/usage/` (curated content). To override a built-in role, add your own skill / rule / trait, or replace a system role like `session-reviewer` — see [6] for the override-precedence chain and worked examples.

Advanced flows — custom pipeline steps, isolated worktrees, parallel sub-agents — live in [5].

## Update notification

When the installed `ai-hats` SHA lags upstream `master`, a three-line **Update banner** appears under the **Session summary** at the end of each interactive session. It tells you the current and latest short SHAs, suggests `ai-hats update`, and prints the opt-out env var on the dim third line.

The probe is non-blocking: a detached background subprocess fires at session start and caches the result for 24h under `<ai_hats_dir>/.cache/update-check.json`. The banner reads whatever's currently in the cache (stale-while-revalidate) — first probe results land in the next session, not the current one.

Suppress both probe and banner with `AI_HATS_NO_UPDATE_CHECK=1` (useful for CI / scripted invocations). Term definitions — see [1].

## Architecture

Roles compose from traits + rules + skills, a flat model, a task state machine, multi-provider injection. The full tour of the internal model, directory layout, skill format, and a sample `config.yaml` — see [7].

<table>
<tr>
  <td align="center" width="33%">
    <a href="docs/ARCHITECTURE.md#session-lifecycle"><img src="docs/assets/diagrams/session-lifecycle.svg" alt="Session lifecycle" height="160"></a><br>
    <sub><a href="docs/ARCHITECTURE.md#session-lifecycle"><b>Session lifecycle</b></a><br>launch → trace → finalize → retro</sub>
  </td>
  <td align="center" width="33%">
    <a href="docs/ARCHITECTURE.md#reflection-loop"><img src="docs/assets/diagrams/auto-reflect-session.svg" alt="Reflection loop" height="160"></a><br>
    <sub><a href="docs/ARCHITECTURE.md#reflection-loop"><b>Reflection loop</b></a><br>verdicts on HYP, votes on PROP</sub>
  </td>
  <td align="center" width="33%">
    <a href="docs/ARCHITECTURE.md#composition-flow"><img src="docs/assets/diagrams/composition-flow.svg" alt="Composition flow" height="160"></a><br>
    <sub><a href="docs/ARCHITECTURE.md#composition-flow"><b>Composition flow</b></a><br>role → traits → materialize</sub>
  </td>
</tr>
<tr>
  <td align="center" colspan="2">
    <a href="docs/ARCHITECTURE.md#backlog-state-machines"><img src="docs/assets/diagrams/backlog-task-fsm.svg" alt="Backlog state machines" height="120"></a><br>
    <sub><a href="docs/ARCHITECTURE.md#backlog-state-machines"><b>Backlog state machines</b></a> · task / HYP / PROP lifecycles</sub>
  </td>
  <td align="center">
    <a href="docs/ARCHITECTURE.md#reflection-loop"><img src="docs/assets/diagrams/manual-reflect-all.svg" alt="Manual reflect-all" height="160"></a><br>
    <sub><a href="docs/ARCHITECTURE.md#reflection-loop"><b>Manual reflect-all</b></a><br>periodic backlog triage</sub>
  </td>
</tr>
</table>

## References

**[1]** — [`docs/glossary.md`](docs/glossary.md) — naming source-of-truth for ai-hats core terms (role, session, reflect, backlog, …).

**[2]** — [`docs/how-to-configure.md`](docs/how-to-configure.md) — narrative walkthrough for first-time setup (wizard, role pick, customization, feedback policy, venv).

**[3]** — [`docs/how-to.md`](docs/how-to.md) — `ai-hats.yaml` overlay recipes and alternative install paths.

**[4]** — [`docs/how-to-backlog.md`](docs/how-to-backlog.md) — day-to-day `ai-hats task` / `task hyp` / `task proposal` recipes.

**[5]** — [`docs/how-to-advanced.md`](docs/how-to-advanced.md) — advanced flows: custom pipeline steps (§1), worktree workflow (§2).

**[6]** — [`docs/how-to-extend.md`](docs/how-to-extend.md) — shipped library layout, override precedence, recipes for your own roles / traits / rules / skills.

**[7]** — [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — internal model, directory layout, skill format, sample `config.yaml`.
