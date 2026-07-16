# ADR-0016: Skill↔tool dependency model — portable `SKILL.md` + declared `requires`

## Status

Accepted (HATS-984, 2026-07-11).

**Amends ADR-0014 §"Engine-owned skills"** (`0014-…:361-377`): that section placed
engine-owned skills *inside* the engine package's `skills/` directory. This ADR
supersedes the *placement + ownership* half of that decision (the open-registry
`ai_hats.skills` discovery mechanism from ADR-0014 / HATS-871 is **retained**).
Triggered by the HATS-871 (T11) review.

## Context

T11 (HATS-871) opened an `ai_hats.skills` entry-point registry and, following
ADR-0014, **moved the `backlog-manager` skill *into* `ai-hats-tracker`** (the CLI
engine package). Review surfaced that this **inverts the dependency arrow**: a
skill *uses* a tool, so the skill should depend on the tool — but co-locating the
skill inside the tool package makes the tool package *own* the skill (a passenger),
and couples the engine's release to skill edits (moving one markdown file bumped
the engine 0.5.0 → 0.6.0).

ai-hats targets three provider surfaces — Claude, **Gemini CLI**, **Cline** — so the
model must be cross-surface-consistent, not Claude-specific. Two research passes
established that the surfaces **converge**, not diverge:

- **The portable capability unit is `SKILL.md`** (the Agent Skills open standard).
  Claude is the origin [1]; Gemini CLI is "based on the Agent Skills open
  standard" [2]; Cline is native since v3.48.0 (Jan 2026) and even discovers
  `.claude/skills/` [3][4]. Each surface *also* has an always-on layer
  (`CLAUDE.md`/`GEMINI.md`/`.clinerules`) and an on-demand command layer, but the
  shared, portable unit is `SKILL.md`.
- **Tool dependencies are handled by declare + reference + separate install.** On
  **no** surface does a skill carry a dependency/`requires`/auto-install field; a
  skill may vendor a script but still assumes the runtime/CLI is present [1][5][6].
  The only shared auto-provisioning mechanism is **MCP servers launched via
  `npx`/`uvx`** (fetch-at-launch) — the identical `command`/`args`/`env` config on
  all three [2][5][7]. **No surface auto-pulls a CLI as a skill dependency**, and
  every surface gates tool installation behind explicit user consent.

The one thing all three formats *lack* — a declared, machine-checkable statement of
"this skill needs tool X" — is where ai-hats can add value above the surfaces.

## Decision

**A skill is portable content that DECLARES its tool need; the tool is a separate,
pure engine that is provided (not owned by the skill, not owning the skill).**

1. **Skill = portable `SKILL.md` in the content layer**, declaring a
   provider-neutral block:

   ```yaml
   ai_hats:
     requires:
       cli:
         - name: ai-hats-tracker
           check: "ai-hats-tracker --version"     # presence probe
           hint: "pip install ai-hats-tracker"    # actionable install guidance
       mcp: []                                     # neutral command/args/env/transport
   ```

   ai-hats **verifies `requires` at compose/session time and warns** (opt-in
   block) with the `hint` — it **never auto-installs**. `requires.mcp` is written
   once in ai-hats' neutral form and **compiled to each surface's native MCP
   channel** (`.mcp.json` / `cline_mcp_settings.json` / `gemini-extension.json`),
   the single place a genuine per-surface bundle exists.

2. **Engine = pure tool.** `ai-hats-tracker` keeps only schema + FSM + `task`/
   `attach` CLI, and **exposes a `[project.scripts]` console entry** so
   `requires.cli: ai-hats-tracker` is satisfiable on `PATH`. It ships **no skill**
   and declares **no** `ai_hats.skills` entry-point.

3. **`ai_hats.skills` entry-point (T11) is retained** — as the discovery seam for
   **out-of-tree skill sources** (third-party skill packages). Engine-owned skills
   bind to their engine via `requires`, **not** by physical co-location inside the
   engine package.

4. **Modernize `GeminiProvider`.** Materialize skills into `.gemini/skills/<name>/`
   the way `ClineProvider` does for `.cline/skills/`, retiring the "Gemini has no
   native skill registry" text-index workaround (`paths/gemini.py:5-7`,
   `providers.py:342`) — Gemini CLI now discovers `.gemini/skills/` natively [2].

**Cross-surface mapping (why one abstraction fits all three):**

| ai-hats concept              | Claude                         | Gemini CLI                           | Cline                     |
| ---------------------------- | ------------------------------ | ------------------------------------ | ------------------------- |
| portable `SKILL.md`          | `.claude/skills/` + plugin-dir | `.gemini/skills/` (native)           | `.cline/skills/` (native) |
| `requires.cli` (verify+warn) | ai-hats-side (above provider)  | ai-hats-side                         | ai-hats-side              |
| `requires.mcp` (compile)     | `.mcp.json`                    | `gemini-extension.json` `mcpServers` | `cline_mcp_settings.json` |

## Consequences

**Positive.**

- The dependency arrow is correct and explicit: the skill declares → the engine is
  a plain tool. Skill iteration no longer forces an engine release.
- One provider-agnostic abstraction (`SKILL.md` + `requires`) instead of
  per-surface bespoke wiring — because the surfaces already share both halves.
- T11 is **not wasted**: the `ai_hats.skills` registry, resolver wiring, and the
  advisory aggregate check are all reused; the aggregate check's cross-package
  detection generalizes naturally to validating `requires`.
- Retires a stale Gemini workaround and gains progressive-disclosure token savings.

**Negative / what it revises.**

- **Reverts part of T11.** `backlog-manager` returns to the content layer as
  portable data declaring `requires.cli: ai-hats-tracker`; the library→tracker
  co-location (HATS-871 commit `99f4b3d`) is undone. `ai-hats-tracker` reverts to a
  skill-free engine (its 0.6.0 skill-bearing release is superseded before publish).
- Adds a new schema surface (`ai_hats.requires`) + a verifier — new code to own.
- `requires` is descriptive, not enforcing at install time (by design) — a missing
  tool surfaces as a warning, not a hard pip failure. This matches every surface
  and their consent-gated security model, but is weaker than "one `pip install`
  brings everything." An **optional** per-domain meta-package MAY layer one-command
  install on top (out of scope here).

**Neutral.** Where genuine auto-provisioning is wanted, it is confined to
**MCP-via-`npx`/`uvx`** (the one mechanism all three already share), never to
arbitrary system CLIs.

## Alternatives considered

- **Keep ADR-0014 as-is (skill inside the engine, Model A).** Rejected: it is the
  inverted-ownership the review flagged, and no surface distributes skills as
  passengers of a tool package.
- **Skill = pip package that depends on and auto-pulls its CLI (`ai-hats-backlog`
  → `ai-hats-tracker`).** Rejected: *no* surface has a skill→CLI pip-dependency
  pattern; it is non-portable and fights every surface's consent-gated install
  model. (A per-domain meta-package remains available as an *optional* convenience
  layer, not the core model.)
- **Plugin-`bin/` / MCP-launcher bundle only.** The launcher idea is sound for
  MCP (adopted via `requires.mcp`), but wrapping the whole `ai-hats-tracker` CLI as
  a vendored launcher is an extra layer over ordinary `PATH` install for our
  uv-workspace; declared `requires.cli` + a console script is simpler.

## References

1. Anthropic — Equipping agents for the real world with Agent Skills — https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills
2. Gemini CLI — Skills (Agent Skills standard) — https://geminicli.com/docs/cli/skills/ ; Extensions reference — https://github.com/google-gemini/gemini-cli/blob/main/docs/extensions/reference.md
3. Cline 3.48.0 — Skills — https://cline.bot/blog/cline-3-48-0-skills-and-websearch-make-cline-smarter
4. cline/skills (Agent Skills spec; reads `.claude/skills/`) — https://github.com/cline/skills
5. Agent Skills specification (frontmatter, `allowed-tools`, `compatibility`) — https://agentskills.io/specification
6. Cline — Skills docs — https://docs.cline.bot/customization/skills
7. Claude Code — MCP quickstart (npx/uvx/HTTP) — https://code.claude.com/docs/en/mcp-quickstart
8. ADR-0014 §"Engine-owned skills" (amended here) — `docs/adr/0014-composable-component-decomposition.md`
9. HATS-871 (T11) — open-registry skill sources + aggregate check (the trigger); HATS-984 — this decision.
