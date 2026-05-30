# How to extend ai-hats with your own components

Add your own roles, traits, rules, skills, and pipelines to ai-hats — without forking the package. For `ai-hats.yaml` overlay tweaks (add a skill to an existing role, change provider, etc.) see [1]. For implementing a custom pipeline step in Python, see [2] §1.

## The shipped library: core vs usage

When you `pip install ai-hats`, two layers ship as built-in content:

- **`library/core/`** — engine fundament. System roles invoked by name from engine code (`initial-wizard`, `session-reviewer`, `auditor-for-role`, `judge`, `judge-for-role`, `hypothesis-intake`, `test-agent`), base traits (`trait-base`, `trait-agent`, `trait-analyst-base`, `base-judge`, `base-auditor`, `trait-reflect-mode`), global rules, foundational skills (`backlog-manager`, `git-mastery`, `context-*`, `review-*`, etc.), and all reflect-pipeline YAML. Without these, `ai-hats init` / `ai-hats self init` / reflect pipelines do not work.
- **`library/usage/`** — curated content catalog. Opinionated roles (`assistant`, `dev-python`, `maintainer`, `architect`, `sre`, `go-dev`, `go-dev-full`), domain traits (`trait-se-mindset`, `dev::python`, `dev::shell`, `dev::go-*`, `env::proxmox`, …), and ~55 optional skills (golang, terraform, ansible, observability, system-design, …).

The split is informational — both layers are loaded at runtime. You can override either from your own library path.

## Override points (last-wins precedence)

When resolving a component by name, ai-hats walks these paths in order; **later paths win** over earlier ones:

| # | Path | Owner |
|---|------|-------|
| 1 | `<pkg>/ai_hats/library/core/` | built-in (shipped) |
| 2 | `<pkg>/ai_hats/library/usage/` | built-in (shipped) |
| 3 | `~/.ai-hats/` | user-global |
| 4 | each path in `ai-hats.yaml: library_paths:` | project-config |
| 5 | `<project>/libraries/` | project-local |
| 6 | CLI `--library-path` extras (rarely used) | session-scoped |

So a `~/.ai-hats/roles/my-role/` is visible to every project on your machine; a `<project>/libraries/roles/my-role/` is visible only to that project; both override anything with the same name in the built-in layers.

> **Composition layer vs library layer.** The table above is about *where
> ai-hats finds component definitions* (the body of a trait, the markdown
> of a skill, the yaml of a role). Separately, there's a **composition
> overlay** mechanism (`customizations:`) that lets you add/remove
> components from a role without forking it; that overlay is also
> layered — global (`~/.ai-hats/customizations.yaml`) before project
> (`<project>/ai-hats.yaml`). See `docs/how-to.md` §4b for global
> overlays and `docs/how-to-configure.md` §4 for full CLI reference. Use
> the library layers below for new components; use the composition
> overlays when you only need to tweak which existing components a role
> pulls in.

## Worked example: add your own role

Add `code-reviewer` to one project:

```bash
mkdir -p libraries/roles/code-reviewer
cat > libraries/roles/code-reviewer/config.yaml <<'YAML'
name: code-reviewer
priorities:
  - Specificity
  - Constructive-tone
  - Brevity
composition:
  traits:
    - trait-base
    - trait-agent
    - trait-se-mindset
  rules: []
  skills:
    - audit-reviewer
    - systematic-debugging
  hooks: {}
injection: |
  # ROLE: CODE REVIEWER

  You review code for correctness, security, and clarity. Quote exact
  lines with file:line refs. Lead with the finding, not the methodology.
YAML

ai-hats config set -r code-reviewer
ai-hats self init
```

`ai-hats list roles` will now show `code-reviewer` alongside the built-in roles.

To make the role visible to **every** project on the machine, put the same file under `~/.ai-hats/roles/code-reviewer/config.yaml` instead.

## Adding your own trait

A trait bundles rules + skills + an injection text. Same layout, under `traits/` instead of `roles/`:

```bash
mkdir -p libraries/traits/my-domain
cat > libraries/traits/my-domain/config.yaml <<'YAML'
name: my-domain
composition:
  traits: []        # traits cannot include other traits
  rules: []
  skills:
    - my-skill      # name resolved via the same precedence chain
  hooks: {}
injection: |
  ## MY-DOMAIN BEHAVIOR

  <text injected into the role's prompt when this trait is composed>
YAML
```

Reference it from a role's `composition.traits`:

```yaml
composition:
  traits:
    - trait-base
    - my-domain
```

Trait names can use the `<group>::<name>` syntax (e.g. `dev::go-grpc`). On disk, that maps to `traits/dev/go-grpc/config.yaml`.

## Adding your own rule

A rule is pure behavioral constraint — no decision logic, no procedure. Two files:

```
libraries/rules/my-rule/
├── metadata.yaml     # name + description (machine-readable)
└── rule.md           # the rule body (1-2 paragraphs)
```

```bash
mkdir -p libraries/rules/my-rule
cat > libraries/rules/my-rule/metadata.yaml <<'YAML'
name: my-rule
description: One-line summary visible in role compositions.
YAML
cat > libraries/rules/my-rule/rule.md <<'MD'
# Rule: My Rule

Use this rule when you need to constrain X. Do not do Y unless Z holds.
MD
```

Attach it via a role or trait `composition.rules`.

## Adding your own skill

A skill is a procedure or checklist (something with decision logic or steps). Two files:

```
libraries/skills/my-skill/
├── metadata.yaml     # name + description + triggers
└── SKILL.md          # the procedure body
```

```bash
mkdir -p libraries/skills/my-skill
cat > libraries/skills/my-skill/metadata.yaml <<'YAML'
name: my-skill
description: When to invoke and what it does.
triggers:
  - keyword: "rebase"
  - keyword: "cherry-pick"
YAML
cat > libraries/skills/my-skill/SKILL.md <<'MD'
# Skill: My Skill

## When to use
- ...

## Procedure
1. ...
2. ...
MD
```

Attach via `composition.skills` in a role or trait.

## Declaring hooks from a skill (advanced)

Beyond prose, a skill can declare two kinds of hook in its `metadata.yaml`,
both materialized during `ai-hats self init`:

- **`git_hooks`** — scripts installed into the project's `.githooks/<event>.d/`
  (e.g. `pre-commit`, `post-merge`). The value is a bare list of script paths.
- **`runtime_hooks`** — provider runtime hooks (Claude Code `PreToolUse` /
  `PostToolUse`). Each entry carries a tool `matcher` and a `script`:

```yaml
# libraries/skills/my-skill/metadata.yaml
runtime_hooks:
  PreToolUse:
    - matcher: Bash            # Claude tool name or regex (e.g. Edit|Write)
      script: hooks/guard.sh   # path relative to the skill directory
  PostToolUse:
    - matcher: Edit|Write
      script: hooks/audit.sh
```

On `self init` the assembler copies each script to
`<ai_hats_dir>/library/hooks/` under a collision-free `<skill>-<basename>` name
(basename = the script's filename) and
`ClaudeProvider` wires one managed entry per `(event, matcher)` into
`.claude/settings.json`, tagged `ai-hats:<skill>:<event>:<matcher>`. Managed
entries are refreshed in place and swept when the skill leaves the role;
user-authored entries are never touched.

Two behaviours worth knowing:

- **Fail-loud validation.** The `runtime_hooks` block is validated at load and
  rejects (naming the offending skill) any of:
    - an unknown event — only `PreToolUse` / `PostToolUse` are allowed;
    - a row missing `matcher` or `script`;
    - the same `matcher` declared twice in one event — only one script per
      `(event, matcher)` is supported, so a duplicate would collapse onto a
      single hook entry and silently drop one;
    - two *distinct* scripts whose filenames share a basename — they would
      collide on the materialized `<skill>-<basename>` name (reusing the *same*
      script across events is fine).

  A silently dropped runtime hook could be a safety hole (a guard that never
  fires), so these are hard errors — unlike `git_hooks`, which skips unknown
  events silently.
- **Provider asymmetry.** Claude Code consumes runtime hooks; the Gemini
  provider is a no-op (no native `PreToolUse` channel), so do not rely on a
  `runtime_hooks` guard under Gemini.

Events recognised today are `PreToolUse` and `PostToolUse` (the set is open).
Materialized scripts run on tool use — treat them as a security surface (see
`SECURITY.md`). The shipped HATS-437 shared-state guard is the canonical
example of a wired `PreToolUse` hook.

## Custom pipelines (advanced)

Pipelines are YAML graphs of steps that wire together composition, prompt
resolution, provider launch, logging, and reflect-specific glue. The built-in
pipelines (`execute`, `human`, `reflect-{session,role,all,issue}`) live in
`library/core/pipelines/` and are invoked by the CLI behind the scenes.

You can drop your own pipeline YAML under any library path:

```yaml
# libraries/pipelines/smoke.yaml
name: smoke
steps:
  - id: compose_role
  - id: resolve_prompt
    params: {default_text: "ping"}
  - id: provider
```

Available step IDs match the registry under `src/ai_hats/pipeline/steps/`.
The post-spawn lifecycle (`make_audit` + `run_session_end`) is invoked by
the runner from its `finally` block via the `finalize-hitl` /
`finalize-subagent` sub-pipelines (HATS-535) — do NOT add those steps to
your top-level pipeline. `launch_provider` survives as a deprecated
alias for `provider`, but new pipelines should use the canonical name.

> **Limitation today**: there is no public CLI flag to invoke an arbitrary
> custom pipeline by name. Custom pipelines can only be launched from Python
> via `PipelineHarness("smoke", project_dir).run({...})`. A public
> `ai-hats pipeline run <name>` command is planned under HATS-268
> (epic HATS-095: developer experience & tooling). Until that lands,
> custom pipelines are useful mainly as scaffolding for future engine work,
> not as a day-to-day extension point.

For the step contract (inputs, outputs, failure policy) see [3].

## Custom verbs via shell aliases

If your command fits the stock `execute` pipeline — *agent loads state via its
own tools, makes decisions, calls tools to act* — you do not need a custom
pipeline YAML, a custom step, or any change to ai-hats. Ship a **role** and an
**initial-injection prompt** under any library path; wrap `ai-hats execute` in
a shell function.

This is the recommended path for plugin-style verbs while the
[generic `ai-hats run <pipeline>` command](#custom-pipelines-advanced) (HATS-268)
is in flight.

### Worked example: `rebalance long` for a finance plugin

**1. Plugin layout** (project-local, but `~/.ai-hats/` works the same):

```
libraries/
  roles/
    fin_consult/
      config.yaml                # composition: traits, rules, skills, injection
  initial_injections/
    rebalance-long.md            # startup checklist for long-strategy rebalance
    rebalance-short.md           # ditto for short
```

The `--prompt <name>` flag resolves `initial_injections/<name>.md` across the
**full `library_paths` chain** (built-in core → usage → `~/.ai-hats/` →
`cfg.library_paths` → `<project>/libraries/`), last-wins. So your plugin's
prompts are discoverable by short name without any package fork (HATS-445).

**2. Shell wrapper** (in `~/.zshrc` or `~/.bashrc`):

```bash
rebalance() {
  local strategy="${1:?usage: rebalance <long|short>}"
  case "$strategy" in
    long|short) ;;
    *) echo "rebalance: unknown strategy '$strategy'" >&2; return 2 ;;
  esac
  ai-hats execute --role fin_consult --prompt "rebalance-$strategy" "${@:2}"
}
```

Now `rebalance long` runs the `fin_consult` role with the
`rebalance-long.md` prompt as the first user-visible message. Shell handles
argument validation (`case`) and verb dispatch — no conflict possible with
built-in `ai-hats` subcommands because the alias is expanded *before*
`ai-hats` runs.

### Role of `fin_consult` vs prompt of `rebalance-long.md`

| Layer | Carries | Stable across runs? |
|---|---|---|
| **Role** (`fin_consult`) | Identity, tools, rules, behaviour ("you are a financial consultant; here are your tools and constraints") | Yes — same role for every `rebalance` invocation |
| **Initial injection** (`rebalance-long.md`) | Per-run startup checklist ("read portfolio.yaml, compare to long-strategy targets, propose trades, confirm before executing") | Per-verb, swapped via `--prompt` |

This is the cleanest separation when the run is a single agent session with
no pre/post processing outside the agent itself.

### When to graduate to a custom pipeline

The shell-alias path fits when:

- One agent session per invocation.
- Domain state (`portfolio.yaml`, market data, …) is read by the agent
  through its own tools (Read, MCP, bash).
- Output is consumed by a human, not by a downstream pipeline step.

You need a [custom pipeline](#custom-pipelines-advanced) and step plugins
(HATS-268) when:

- State must be **fetched in Python** before the agent runs
  (`load_portfolio`, `fetch_market_snapshot`, `persist_run_record`, …).
- Output must be **parsed and validated** (schema-checked trade plan,
  written to a typed store).
- Multiple agent sessions chain together with structured handoff.

## Replacing a system role (e.g. your own auditor)

The built-in `session-reviewer` and `auditor-for-role` are reachable by name from engine code. Their **content** is overrideable — drop a file with the same name in any later-precedence path:

```bash
# 1. Inspect the default (read-only — don't edit the installed file)
python -c "from importlib.resources import files; print(files('ai_hats.library') / 'core' / 'roles' / 'session-reviewer')"

# 2. Copy as a starting point
mkdir -p libraries/roles/session-reviewer
cp "$(python -c 'from importlib.resources import files; print(files("ai_hats.library") / "core" / "roles" / "session-reviewer" / "config.yaml")')" \
   libraries/roles/session-reviewer/config.yaml

# 3. Edit injection / traits / skills as needed
$EDITOR libraries/roles/session-reviewer/config.yaml

# 4. Apply
ai-hats self init
```

The next time a reflect pipeline invokes `session-reviewer` (by name), the resolver finds your version first (last-wins) and uses it.

### Limitations

- **You cannot rename a system role.** The engine looks up `session-reviewer`, `judge-for-role`, `hypothesis-intake`, etc. by literal string. Override the content, not the name.
- **You cannot run a parallel auditor.** If you want a second auditor alongside the default, file a feature request — the engine currently invokes one role per pipeline step.
- **Verify after editing.** Some pipelines pass structured marker contracts (e.g. `BEGIN_REFLECT` / `END_REFLECT`). If your override breaks the contract, the parser downstream will fail. Keep the marker block and the role's `priorities` consistent with the default unless you know what you're doing.

## Pointing ai-hats at extra library paths

If you keep a shared library outside the project (e.g. on a team-wide volume or another git repo), add it to `ai-hats.yaml`:

```yaml
schema_version: 2
provider: claude
active_role: assistant
library_paths:
  - /opt/team-shared/ai-hats-lib
  - ~/work/ai-hats-private/library
```

Paths are expanded (`~` → `$HOME`) and walked in order. Each path is treated as a library root — meaning ai-hats expects `roles/`, `traits/`, `rules/`, `skills/` subdirectories inside it.

## Inspecting what's resolved

After `ai-hats self init`, the composed role is materialized into your project. To see what got pulled in:

- `ai-hats list rules` / `ai-hats list skills` / `ai-hats list traits` / `ai-hats list roles` — flat list of everything visible.
- `ai-hats tree` (or `ai-hats list roles --tree`) — composition graph for the active role.
- Inspect `<project>/.agent/ai-hats/` — the assembled rules/skills/imports that the provider reads at runtime.

## Cookbook entries

- **Add a project-specific rule that bans `git push --force`** — make `libraries/rules/no-force-push/rule.md` with the constraint, then `ai-hats config customize <role> --add-rule no-force-push`.
- **Tweak the injection of a built-in trait** — copy `library/usage/traits/<name>/config.yaml` into your `libraries/traits/<name>/`, edit the `injection:` block, `ai-hats self init`. Same last-wins rule applies to traits.
- **Share a private skill across projects** — put it under `~/.ai-hats/skills/<name>/`. Every project on your machine sees it without further config.

## Migrating from a removed built-in component

Sometimes ai-hats removes a component that previous releases shipped (the
v0.7 example: `personal-workflow` trait — HATS-433). The component moves
into user-scope; you re-instate it for yourself in two steps. Use this
recipe whenever you see a `BREAKING` entry pointing at a component you relied on.

**1. Re-create the component locally.** Read the deleted file from the
git history of `ai-hats` at the previous tag, copy its body to your
user-scope library:

```bash
# Find the last commit that had the file:
cd $(python -c 'import ai_hats, os; print(os.path.dirname(ai_hats.__file__))')
git log --diff-filter=D --name-only --oneline -- library/usage/traits/personal-workflow

# (Outside the package, in your shell:)
mkdir -p ~/.ai-hats/traits/personal-workflow
# … paste the recovered config.yaml content into the file …
```

**2. Re-attach via a global overlay** so every project keeps loading it:

```bash
ai-hats config customize maintainer --add-trait personal-workflow --global
ai-hats config customize assistant  --add-trait personal-workflow --global

# In each project that uses these roles:
ai-hats self init
```

Verify with `ai-hats config status` — the trait should appear under the
role's `traits` branch with a `(global)` source-tag.

This pattern works for any removed trait / skill / rule: re-create under
`~/.ai-hats/{traits,skills,rules}/<name>/` and re-attach via the
`--global` overlay. No fork of the role required.

## References

**[1]** — [`docs/how-to.md`](how-to.md) — `ai-hats.yaml` overlay recipes (add a skill, change provider, switch role, project-local libraries).

**[2]** — [`docs/how-to-advanced.md`](how-to-advanced.md) — advanced flows: custom pipeline steps (§1), worktree workflow (§2).

**[3]** — [`docs/adr/0002-pipeline-subsystem-cli.md`](adr/0002-pipeline-subsystem-cli.md) — ADR-0002, step contract: inputs, outputs, failure policy.
