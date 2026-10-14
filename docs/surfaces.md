# Surfaces — what works where

Six harnesses ship in-tree. They are not equally capable, and the gaps are not
failures: each agent CLI exposes a different amount of itself, and ai-hats can
only use what is exposed. This page says which is which, so a choice of harness
is made with the trade-off visible rather than discovered three weeks in.

Naming: **harness** is the pitch word, **provider** is what you type
(`-p/--provider`, the `ai-hats.yaml` key), **Surface** is the code name. All
three are the same thing — see [1]. The `harness:` block in `ai-hats.yaml` is a
different thing entirely: it pins the install channel.

## The matrix

| Harness    | Hook verdicts beyond a plain refusal | Session log → audit | Sub-agents        | Consent wrappers | Readiness probe |
| ---------- | ------------------------------------ | ------------------- | ----------------- | ---------------- | --------------- |
| `claude`   | ask · cancel-after · nudge           | parsed              | native SDK engine | yes              | auth            |
| `agy`      | ask · cancel-after · nudge           | parsed              | subprocess        | **no**           | **none**        |
| `cline`    | nudge only                           | parsed              | subprocess        | yes              | **none**        |
| `codex`    | cancel-after · nudge                 | **trace-log only**  | subprocess        | yes              | CLI             |
| `kimi`     | **none (refusal only)**              | parsed              | subprocess        | yes              | auth · CLI      |
| `opencode` | nudge only                           | **trace-log only**  | subprocess        | yes              | **none**        |

Every harness receives the composed role — that column would read `yes` six
times and is left out. *How* it is delivered differs per harness (a system-prompt
flag, a rules directory, a config dir); that map is [2] §Materialization.

## What each column means

**Hook verdicts beyond a plain refusal.** Any harness can refuse a tool call
before it runs. What differs is everything else a gate might want to say, and
each value below is a field of that harness's `Dialect` (`hook_channel.py`,
rows in `surfaces/profiles.py`):

- **ask** — put a question to the human and wait (`can_ask`). Where it is
  absent, a gate that wanted to ask is downgraded once, centrally, rather than
  each surface inventing its own fallback.
- **cancel-after** — cancel a call that already ran, on `PostToolUse`
  (`can_deny_after`).
- **nudge** — carry advisory text onward (`can_carry_nudges`). Where it lands
  differs — usually the model's own context. On `kimi` a non-blockable hook
  event's stdout never reaches the model at all, so the row reads none: a gate
  refuses (exit 2) or stays silent, and composed nudge hooks log a plan-time
  warning instead of pretending to deliver.

Read the rows as sentences: on `cline` and `opencode` a gate can only advise —
it never puts a question to you and cannot take a call back. On `codex` it can
undo a call after the fact, but it still cannot ask first. On `kimi` it can
only refuse before the fact — `PostToolUse` there is observation-only, so what
a gate says about a call that already ran reaches the gate log, not the model.

**Session log → audit.** `parsed` means the harness writes a structured session
log and ships a `TranscriptParser` for it, so `audit.md` and `usage.json` are
derived from what actually happened (`transcript_parser` + `resolve_transcript`).
`trace-log only` means it overrides neither, and audit falls back to ai-hats'
own trace log — non-empty, but coarser: no per-turn tool detail, no token usage.

**Sub-agents.** `ai-hats agent <role>` runs everywhere. On `claude` it is a
native SDK engine, multi-turn in-process (`supports_sdk_engine`), and
`setting_sources: []` keeps the project's own CLAUDE.md out of the child.
Elsewhere it is a real subprocess whose stdout is captured — on `kimi` the
engine parses the `stream-json` stream for the answer text and the harness's
own session id (the `session.resume_hint` meta line), since kimi mints its ids
itself — see [3]. That subprocess starts in the session's worktree, and kimi
loads the AGENTS.md chain from git root down to its cwd with no off-switch —
so a `kimi` automate sub-agent carries the worktree's AGENTS.md on top of the
inlined role prompt, a context the other surfaces' sub-agents do not see.

**Readiness probe.** What refuses an unattended `ai-hats agent` before it
takes a worktree (`readiness_findings`): `auth` — the surface is not logged in;
`CLI` — the binary is missing or broken. The refusal says why in the session's
`events.jsonl`; a HITL session warns and launches. Codex's login check only
warns: `codex login status` cannot see `CODEX_API_KEY`, which `codex exec`
honours. `none` means an unattended run on that harness fails after launch
instead — see [5] §Readiness probe. Kimi's probe reads the credentials
directory under `$KIMI_CODE_HOME` (default `~/.kimi-code`); international
(kimi.ai) accounts log in with `KIMI_OAUTH_HOST=https://auth.kimi.ai`.

**Consent wrappers.** Whether a HITL child inherits an authoritative session
`PATH`, which is what session-local command wrappers ride on
(`supports_session_command_wrappers`). Where it is `no`, a role that declares
`apps.consent_gate` fails **before the agent starts**, rather than running with
an unwrapped command — a loud refusal, not a silent hole.

## MCP

A skill declares the MCP servers it needs once, in a neutral form, and the
declaration is compiled to each wired harness's native channel — `.mcp.json`,
`cline_mcp_settings.json`, `gemini-extension.json`. `codex` takes its servers as
CLI arguments instead (`mcp_form_cli_args`). `kimi`'s `mcp.json` channel is not
wired yet — a composed `requires.mcp` binding has no target there. The model is
the Skill↔tool dependency entry in [1]; the frontmatter field you actually
write is in [4].

## Keeping this page honest

The values above were read out of the classes themselves — which method a
surface overrides, what its profile row says — not recalled. They are still
maintained by hand, and a sixth harness will not add its own row: that is what
happened to the per-surface table in [2], which sat at three rows of five for
several releases. Automating this page is filed as its own task.

Ask your own installation rather than trusting a page: `ai-hats list providers`.

## References

**[1]** — [`docs/glossary.md`](glossary.md) — Provider / Surface / harness, and the unrelated `harness:` config block.

**[2]** — [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) — the materialization map: where each surface receives context, skills and hooks.

**[3]** — [`docs/how-to-orchestration.md`](how-to-orchestration.md) — sub-agent orchestration, session tags, JSON output.

**[4]** — [`docs/how-to-extend.md`](how-to-extend.md) — skill frontmatter, including the `requires` block a skill declares its CLI and MCP dependencies in.

**[5]** — [`docs/session-start-notices.md`](session-start-notices.md) — the readiness probe as a HITL warn producer, and the seam it shares with the Automate refusal.
