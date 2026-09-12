<p align="center">
  <img src="docs/assets/logo-256.png" alt="ai-hats" width="180" />
</p>

<h1 align="center">ai-hats</h1>

<p align="center">
  <strong>Role-based multi-harness framework with behavior feedback.</strong>
</p>

<p align="center">
  <em>Compose an agent from reusable roles, run it on any of five agent CLIs, and let every session leave evidence behind.</em><br>
  <sub>Do. Reflect. Repeat.</sub>
</p>

<p align="center">
  <a href="https://github.com/muratovv/ai-hats/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/muratovv/ai-hats/actions/workflows/ci.yml/badge.svg?branch=master"></a>
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-green.svg"></a>
  <a href="https://docs.astral.sh/uv/"><img alt="uv" src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json"></a>
  <a href="#project-status"><img alt="Status: Beta" src="https://img.shields.io/badge/status-beta-orange.svg"></a>
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

Have you ever watched the same AI agent step on the same rakes across projects? Forgetting your conventions, skipping the planning step, falling back to the same anti-pattern. Copy-pasting `CLAUDE.md` doesn't scale: edits drift across projects, and a fix in one rarely makes it back to the others. And when a lesson *is* learned, nothing decides whether it was worth keeping — memory files grow until nobody reads them.

ai-hats answers this in four parts.

- **Role-based.** A role is a composition of reusable `traits`, `rules`, `skills` and `hooks`, assembled once and injected into the system prompt. A fix to one component reaches every role that includes it on the next session — no copy-paste, no drift. Terms — [1].
- **Multi-harness.** One role set, five harnesses: `claude`, `agy` (Gemini), `cline`, `codex`, `opencode`. The composition is built per session and delivered in each harness's own dialect, so switching is `ai-hats config set -p <provider>` and nothing else.
- **Framework.** The machinery around the agent's work, not just its prompt: isolated git worktrees so a task never dirties your checkout ([2]), a backlog of tasks / hypotheses / proposals with enforced state machines behind the `rack` CLI ([3]), a consent gate that turns a declared destructive operation into a question to you and refuses self-granting, and a shipped library of roles to start from.
- **Behavior feedback.** After every session a reviewer appends a **verdict** — `confirmed`, `refuted` or `inconclusive` — to each active hypothesis's `validation_log`. Evidence accumulates across sessions; only then does a hypothesis close and a pattern become a rule or a skill. What the agent learns reaches the next prompt **after** it is proven, not when it is guessed ([4]).

The first three layers are the runtime; the fourth is the loop that changes it.

<!-- TODO(HATS-1950 S2): встроить композитную диаграмму assets/diagrams/runtime-and-loop.svg — runtime (роль-композиция · пять поверхностей · обвязка) | feedback loop -->

## Quick start

**Prerequisite:** [uv](https://docs.astral.sh/uv/) is the single host requirement — the env engine that also provisions Python. The one-command install auto-installs it if absent.

```bash
# 1. install the launcher, create the venv, wire this project
curl -LsSf https://github.com/muratovv/ai-hats/raw/master/scripts/bootstrap.sh | bash -s -- -r <role> -p <provider>

# 2. start a session with the composed role
ai-hats
```

`ai-hats` is a **host tool**, driven by a ~30-line bash launcher in `~/.local/bin/ai-hats` that exec's `python -m ai_hats`. It is never installed as a dependency of your project's own venv, and there is no `<venv>/bin/ai-hats` console script.

### Installation

| Method                | Command                                                                     | When to use                            |
| --------------------- | --------------------------------------------------------------------------- | -------------------------------------- |
| **One command**       | `curl -LsSf .../scripts/bootstrap.sh \| bash -s -- -r <role> -p <provider>` | fresh host — installs uv if missing    |
| **Zero-install**      | `uvx ai-hats self init`                                                     | try it, or wire one project (needs uv) |
| **Launcher per host** | `curl -sSL .../scripts/install-launcher.sh \| bash`                         | persistent `ai-hats` on `$PATH`        |

Then wire a project: `ai-hats self init` runs an interactive wizard that detects your stack, recommends a role, and configures the feedback policy. Scripted variant — `ai-hats self init -p claude -r go-dev --no-wizard`. Full walkthrough — [5]; alternative install paths and `ai-hats.yaml` overlay recipes — [6].

If `self update` ever can't repair a broken install in-band, recover out-of-band with `bash -s -- --repair` on the bootstrap script ([6] §10).

## Harnesses

Five harnesses ship in-tree, discovered through the `ai_hats.providers` entry point — an open registry, so an out-of-tree package can add one. `gemini` is an accepted alias for `agy`.

| Harness    | Agent CLI            |
| ---------- | -------------------- |
| `claude`   | Claude Code          |
| `agy`      | Antigravity / Gemini |
| `cline`    | Cline                |
| `codex`    | Codex                |
| `opencode` | opencode             |

Support is not uniform across all five — runtime hooks, transcript-backed observability, sub-agents and the consent gate each land differently per harness.

<!-- TODO(HATS-1950 S3): ссылка на матрицу статуса поддержки docs/surfaces.md — «per-capability support matrix — [N]» -->

List what your host has: `ai-hats list providers`.

## CLI

Nine top-level groups: `agent`, `config`, `execute`, `list`, `reflect`, `self`, `session`, `wait`, `wt`. The backlog is not one of them — cards live behind the `rack` CLI ([3]).

The full reference with descriptions and options is the tool itself: `ai-hats --tree` (subtrees: `ai-hats --tree wt`, or deeper: `ai-hats --tree config feedback`).

```bash
ai-hats                                    # session with current settings
ai-hats -p claude -r architect             # override harness and role
ai-hats agent sre --task "investigate XYZ" # sub-agent in an isolated worktree
ai-hats config status                      # health-check the composition
ai-hats self update                        # update the package (self-healing)
```

## Customization

The shipped library splits into `core/` (engine fundament) and `usage/` (curated content). You change behaviour by composing or replacing roles rather than editing core code — add your own role, override a built-in like `session-reviewer`, point ai-hats at an external library repo, or ship a role as a one-liner shell alias. Recipes and the override-precedence chain — [7].

Internal model, directory layout, skill format, session lifecycle and the reflection loop — [8]. Documentation entry-point — [9].

## Project status

**Beta.** Until `v1.0.0` the version reads `0.MAJOR.MINOR`: a breaking change lands on a MAJOR bump and ships with a migration guide ([9]); MINOR is additive or fix-only. See [Releases](https://github.com/muratovv/ai-hats/releases) and [CHANGELOG.md](CHANGELOG.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) — development setup, library layout, diagram house style. Security policy: [SECURITY.md](SECURITY.md). License: [MIT](LICENSE).

## References

**[1]** — [`docs/glossary.md`](docs/glossary.md) — naming source-of-truth for core terms (role, provider, session, reflect, backlog, …).

**[2]** — [`docs/how-to-advanced.md`](docs/how-to-advanced.md) — advanced flows: custom pipeline steps (§1), worktree workflow (§2).

**[3]** — [`docs/how-to-hatrack.md`](docs/how-to-hatrack.md) — day-to-day `rack` / `rack hyp` / `rack proposal` recipes.

**[4]** — [`docs/how-to-feedback-loop.md`](docs/how-to-feedback-loop.md) — feedback policies, verdicts, and the reflection internals.

**[5]** — [`docs/how-to-configure.md`](docs/how-to-configure.md) — narrative walkthrough for first-time setup (wizard, role pick, customization, feedback policy, venv).

**[6]** — [`docs/how-to.md`](docs/how-to.md) — `ai-hats.yaml` overlay recipes and alternative install paths.

**[7]** — [`docs/how-to-extend.md`](docs/how-to-extend.md) — shipped library layout, override precedence, recipes for your own roles / traits / rules / skills.

**[8]** — [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — internal model, directory layout, skill format, sample `config.yaml`.

**[9]** — [`docs/INDEX.md`](docs/INDEX.md) — documentation catalog and entry-point, including the per-version migration guides.
