# ADR-0004: Per-session plugin-dir materialization for sub-agent skills

## Status

Accepted (HATS-307, 2026-05-18).

## Context

Sub-agent sessions spawned via `ai-hats reflect role` / `execute` cannot invoke role-specific skills through the Skill tool. Example: `Skill('role-coherence-protocol')` raises `Error: Unknown skill` inside `auditor-for-role` / `judge-for-role`, even though the skill is in the spawned role's composition.

Root cause: the project's `.claude/skills/` mirror is populated by `Assembler.set_role` and reflects the *active* role (typically `assistant`). Spawned roles run in the same `project_dir` and therefore read the active role's mirror — so any skill that lives only in the spawned role's composition is physically absent from Claude Code's Skill registry.

Two spawn paths exist, both broken:

1. **`WrapRunner`** (interactive `--print -p`, PTY-proxied) — used by `ai-hats execute`. Calls `Provider.build_override` to produce a temporary `--system-prompt-file` but does not refresh skills.
2. **`SubAgentRunner`** (subprocess, used by pipelines and `ai-hats reflect role`) — builds the role prompt inline as `meta_prompt` and invokes `claude --print -p <meta_prompt>`. Never touched skills at all.

The bug was originally observed via `SubAgentRunner` (path 2) during HATS-303 Pass B sessions.

## Decision

Materialize the spawned role's skills into an ephemeral plugin directory under `/tmp/ai-hats-plugin-*` and pass it to `claude` via the `--plugin-dir` CLI flag (session-scoped, repeatable, documented at https://code.claude.com/docs/en/plugins-reference.md). Both spawn paths use the new `Provider.materialize_runtime_skills(project_dir, result)` hook:

- `ClaudeProvider.materialize_runtime_skills` → `["--plugin-dir", <tmp>]`, dir contains `.claude-plugin/plugin.json` + `skills/<name>/SKILL.md` per composed skill, with HATS-380 placeholder expansion applied.
- `Provider.materialize_runtime_skills` (base) → `[]`. Gemini inherits this no-op — see HATS-367 for the cross-provider story.

Cleanup is the caller's responsibility:

- `WrapRunner.run` cleans up in its `finally` block via `_cleanup_plugin_dir(override_args)`.
- `SubAgentRunner.run` cleans up in a new `finally` inside the worktree-managed subprocess block.
- SIGKILL / kernel panic leaves orphans in `/tmp/`; macOS reclaims them on reboot. No periodic sweep ships in v0.6.

The primary user session — `Assembler.set_role` → `.claude/skills/` mirror via `Provider.export_skills` — is unchanged. Two-track skill delivery is intentional: the persistent mirror serves the user's day-to-day session; the ephemeral plugin-dir serves per-spawn role overrides.

## Rejected Alternatives

**A. Re-export `.claude/skills/` on each spawn.** Would mutate user-visible project state every time `reflect role` runs; risk of races between parallel spawns and the primary session; complicates cleanup if a spawn crashes mid-write.

**B. Inline skill bodies in the system prompt.** Defeats the on-demand skill model, loses Skill-tool invocation semantics, bloats the prompt (skills are otherwise index-only per `Provider.build_system_prompt`).

**C. Refactor `runtime.py` to allocate the session id before `build_override` and name plugin-dirs `/tmp/ai-hats-plugin-<sid>/`.** Larger blast radius (touches session-creation order) for a cosmetic naming win. `tempfile.mkdtemp` already gives a unique path and matches the existing override-file convention.

**D. Remove the `.claude/skills/` mirror entirely (plugin-dir for everything).** Possible, but breaks "user runs raw `claude` in the project dir without `ai-hats execute`" — they would lose Skill discovery. Out of scope for HATS-307; revisit under HATS-278 if the mirror proves redundant.

## Collision behavior

A skill name can in principle appear in both `.claude/skills/<X>/` (active-role mirror) and `--plugin-dir <Y>/skills/<X>/` (spawned-role plugin-dir). Both copies derive from the same library source through `Composer._resolve_skills` and apply the same `expand_path_placeholders` transform — content is byte-equivalent in the normal flow.

Empirically verified (HATS-307 Step 1 PoC, `notes-poc.md`):

| Scenario                                                | Observed                                          |
| ------------------------------------------------------- | ------------------------------------------------- |
| Same name in `.claude/skills/` AND `--plugin-dir`       | Project mirror wins; no fatal error; no warning.  |
| Skill name unique to `--plugin-dir`                     | Invokable via Skill tool; resolves correctly.     |

Conclusion: the project-mirror-wins behavior is acceptable because content equivalence makes "who wins" functionally irrelevant in normal flow. Edge case to track: a future change that lets per-role skill content diverge (e.g. role-scoped skill customization) would silently get the active role's version on collision. Mitigation when that lands: either freeze skill content as immutable per session, or drop the mirror per alternative D.

## Consequences

- Bug fixed in both spawn paths. `Skill('<role-specific>')` now resolves in spawned sessions.
- `Provider.materialize_runtime_skills` becomes the canonical extension point for per-provider runtime skill discovery. Gemini sub-agent skills (HATS-367) hook in here.
- One additional `tempfile.mkdtemp` + `shutil.copytree` per spawn (~5-30 ms for typical role compositions; negligible).
- `--plugin-dir` is a Claude Code CLI dependency. If the flag is removed or its semantics change, sub-agent skills regress to the pre-fix state. CI smoke check against `claude --help` output is recommended as a follow-up.

## Update — HATS-294 (2026-05-20) realized Alternative D

The two-track design described above (permanent `.claude/skills/` mirror **plus** per-session plugin-dir) is no longer current. **HATS-294 implemented Alternative D**: `Provider.export_skills` / `cleanup_skills` / `skills_export_dir` were removed; skills now live exclusively in the per-session plugin-dir under `<ai_hats_dir>/.cache/sessions/<sid>/plugin/`, materialized by `plugin_dir.materialize_plugin_dir` and passed to `claude` via `--plugin-dir`. The original ADR-0004 decision and analysis are retained verbatim above as historical record; the "primary user session unchanged" wording (§Decision) and the "mirror wins on name collision" row (§Edge cases) no longer reflect the system.

Follow-on consequence captured by **HATS-465**: because ai-hats never wrote to `~/.claude/skills/` (and now never writes to `.claude/skills/` either), any `.ai-hats-managed` marker found under `~/.claude/skills/` is an artefact of a pre-v0.7 manual `cp -r .claude/skills/ ~/.claude/skills/`. `self init` now surfaces such orphan markers with a WARN.

## References

- HATS-307 — this task.
- HATS-294 — per-session cache + drop of permanent `.claude/skills` export (realizes Alternative D).
- HATS-465 — `self init` WARN on orphan `~/.claude/skills/.ai-hats-managed` marker.
- HATS-380 — placeholder expansion (`expand_path_placeholders`); reused in the plugin-dir generator.
- HATS-367 — cross-provider per-step provider selection; Gemini's analog for `--plugin-dir` belongs here.
- HATS-278 — epic: role-prompt composition. Hosts the on-demand skill model and any follow-up to retire the `.claude/skills/` mirror (alternative D).
- HATS-303 — surfaced the bug during Pass B sessions.
- `notes-poc.md` under the HATS-307 task dir — empirical PoC results.
- `src/ai_hats/plugin_dir.py` — materialization helper.
- `src/ai_hats/providers.py` — `Provider.materialize_runtime_skills` hook.
- `src/ai_hats/runtime.py` — `_cleanup_plugin_dir`; wired into both `WrapRunner` and `SubAgentRunner`.
