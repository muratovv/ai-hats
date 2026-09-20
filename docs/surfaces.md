# Surfaces — what works where

Five harnesses ship in-tree. They are not equally capable, and the gaps are not
failures: each agent CLI exposes a different amount of itself, and ai-hats can
only use what is exposed. This page says which is which, so a choice of harness
is made with the trade-off visible rather than discovered three weeks in.

Naming: **harness** is the pitch word, **provider** is what you type
(`-p/--provider`, the `ai-hats.yaml` key), **Surface** is the code name. All
three are the same thing — see [1]. The `harness:` block in `ai-hats.yaml` is a
different thing entirely: it pins the install channel.

## The matrix

| Harness    | Hook verdicts beyond a plain refusal | Session log → audit | Sub-agents        | Consent wrappers |
| ---------- | ------------------------------------ | ------------------- | ----------------- | ---------------- |
| `claude`   | ask · cancel-after · nudge           | parsed              | native SDK engine | yes              |
| `agy`      | ask · cancel-after · nudge           | parsed              | subprocess        | **no**           |
| `cline`    | nudge only                           | parsed              | subprocess        | yes              |
| `codex`    | cancel-after · nudge                 | **trace-log only**  | subprocess        | yes              |
| `opencode` | nudge only                           | **trace-log only**  | subprocess        | yes              |

Every harness receives the composed role — that column would read `yes` five
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
- **nudge** — carry advisory text onward (`can_carry_nudges`). All five can;
  where it lands differs — usually the model's own context.

Read the rows as sentences: on `cline` and `opencode` a gate can only advise —
it never puts a question to you and cannot take a call back. On `codex` it can
undo a call after the fact, but it still cannot ask first.

**Session log → audit.** `parsed` means the harness writes a structured session
log and ships a `TranscriptParser` for it, so `audit.md` and `usage.json` are
derived from what actually happened (`transcript_parser` + `resolve_transcript`).
`trace-log only` means it overrides neither, and audit falls back to ai-hats'
own trace log — non-empty, but coarser: no per-turn tool detail, no token usage.

**Sub-agents.** `ai-hats agent <role>` runs everywhere. On `claude` it is a
native SDK engine, multi-turn in-process (`supports_sdk_engine`). Elsewhere it
is a real subprocess whose stdout is captured — see [3].

**Consent wrappers.** Whether a HITL child inherits an authoritative session
`PATH`, which is what session-local command wrappers ride on
(`supports_session_command_wrappers`). Where it is `no`, a role that declares
`apps.consent_gate` fails **before the agent starts**, rather than running with
an unwrapped command — a loud refusal, not a silent hole.

## MCP

A skill declares the MCP servers it needs once, in a neutral form, and the
declaration is compiled to each harness's native channel — `.mcp.json`,
`cline_mcp_settings.json`, `gemini-extension.json`. `codex` takes its servers as
CLI arguments instead (`mcp_form_cli_args`). The model is the Skill↔tool
dependency entry in [1]; the frontmatter field you actually write is in [4].

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
