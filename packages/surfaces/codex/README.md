# ai-hats-codex

OpenAI Codex CLI surface plugin for
[ai-hats](https://github.com/muratovv/ai-hats). It registers `codex` through
the `ai_hats.providers` entry point and supports the normal interactive launch:

```console
$ ai-hats -p codex -r maintainer
```

## Delivery contract

- The composed role and always-on rules are passed per run through Codex's
  `developer_instructions` configuration override. No `AGENTS.md` or project
  `.codex` file is created or changed.
- Skills are copied to ai-hats' external per-session cache and exposed through
  a compact name, description, and exact `SKILL.md` path index. Each session
  gets its own skills tree, so parallel Codex sessions do not overwrite one
  another.
- Composed runtime guards are copied into that same session cache. Stable
  per-run Codex hook definitions dispatch `PreToolUse`, `PermissionRequest`,
  and `PostToolUse` to the current session's manifest; the command contains no
  project path or session ID, so concurrent sessions stay isolated and a
  reviewed hook hash remains stable.
- Interactive sessions use `workspace-write` with `on-request` approvals.
  Automated sessions use `codex ... exec --json --ephemeral` with
  `workspace-write` and `never`, because they cannot answer approval prompts.
- Dangerous bypass flags are rejected on the normal ai-hats launch path.
- The provider does not pass `-C`: ai-hats owns the child process cwd, including
  its isolated task worktree. The canonical project path may deliberately point
  at the main checkout for shared tracker operations.

Codex's existing user authentication and native user/repository configuration
remain in place. The plugin neither reads credentials nor redirects
`CODEX_HOME`.

Codex requires explicit review of non-managed hook definitions. On the first
hook-enabled launch, inspect the stable ai-hats dispatcher with `/hooks` and
trust it only if it matches the command shown there. ai-hats never passes
`--dangerously-bypass-hook-trust`; until the definition is trusted, Codex may
skip it and reports that state in the TUI.

Trusting that stable dispatcher delegates hook selection to ai-hats and the
role you launched; it is not a separate review of every composed skill version.
Review project/custom library sources before composing them. At runtime the
dispatcher only executes executable files from the current session's copied
skills mirror, rejects path or symlink escapes, validates the session identity,
and fails closed if any of those pins are missing.

## Requirements

- `codex` on `PATH`, authenticated with the normal Codex login flow.
- `ai-hats>=0.15.0`.

Before the TUI handoff the surface runs only `codex --version` and
`codex login status`. Missing CLI and logged-out states produce redacted startup
remediation; ai-hats never runs login or reads credential files itself.
