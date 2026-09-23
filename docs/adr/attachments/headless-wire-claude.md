# Headless wire — claude, measured on the live binary

Attachment of ADR-0038. Measured by the PoC HATS-2014 (its `session.py` / `drive.py` /

`probe_thinking.py` live on that card); this file is the observation the ADR cites and may be

re-measured when the CLI moves. No session recordings here — schema and redacted fragments only.




Pinned to: `claude` **2.1.278**, `claude-agent-sdk` **0.2.126** (oracle), macOS, 2026-09-21.
The wire is undocumented; everything below is observed on live runs (`drive.py`,
`probe.py`, `oracle_sdk.py`). Raw captures are NOT attached (session recordings).

## 1. What works raw — no SDK, no `-p`, no handshake

```
claude --output-format stream-json --verbose --input-format stream-json \
       --setting-sources "" --system-prompt "" [--permission-prompt-tool stdio]
```

- Spawned from Python with a **scrubbed env** (`PATH HOME USER LOGNAME SHELL TERM LANG
  LC_ALL TMPDIR` only — no `CLAUDECODE`, no `CLAUDE_CODE_ENTRYPOINT`) in a fresh temp
  cwd. Works. No folder-trust dialog, no `initialize` control_request needed, `-p` not
  needed (`--input-format stream-json` implies print mode).
- One process, one `session_id`, **four turns**, context kept across turns (negative
  control: the same recall in a fresh session fails the assert — the check can fail).
- Closing stdin ends the process cleanly (`exit 0`, ~immediately). A SIGTERM to the
  driver leaves no orphaned `claude` when the driver kills the child in its handler
  (`session.py`; `finally` alone is skipped on SIGTERM — the PTY-relay PoC learned the same).

## 2. Wire contract as observed

**In (stdin, one JSON per line):**

```json
{"type":"user","message":{"role":"user","content":"…"},"parent_tool_use_id":null,"session_id":"default"}
{"type":"control_response","response":{"subtype":"success","request_id":"<id>","response":{"behavior":"allow","updatedInput":{…}}}}
{"type":"control_response","response":{"subtype":"success","request_id":"<id>","response":{"behavior":"deny","message":"…"}}}
{"type":"control_response","response":{"subtype":"error","request_id":"<id>","error":"…"}}
```

`session_id: "default"` is what the SDK sends; the CLI mints the real UUID and reports it
in every event.

**Out (stdout, one JSON per line), per turn, without partial messages:**

| order | `type/subtype`             | notes                                                                                                                                                                                                                                                     |
| ----- | -------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1     | `system/init`              | **re-emitted on every turn** (new `uuid`, same `session_id`); carries `model`, `permissionMode`, `tools`, `session_id`, `claude_code_version`, `apiKeySource` (`none` on a subscription login — the binary is authenticated by `HOME`, not by a key in the env) …                                                                                           |
| 2..n  | `assistant`                | one per model message; `message.content` blocks: `text` / `tool_use` (with **full** `input` — no delta assembly needed at this level)                                                                                                                     |
|       | `user`                     | tool result fed back (`message.content[].type == "tool_result"`, plus a top-level `tool_use_result`)                                                                                                                                                      |
|       | `control_request`          | inbound question to the writer; only seen `subtype: can_use_tool` (see §3)                                                                                                                                                                                |
|       | `rate_limit_event`         | first turn only; `rate_limit_info.unifiedWindows.{five_hour,seven_day}.utilization`                                                                                                                                                                       |
|       | `system/permission_denied` | when `--permission-prompts none` denies a tool (see §3)                                                                                                                                                                                                   |
| last  | `result/success`           | **the turn boundary**. Fields: `is_error`, `stop_reason`, `terminal_reason`, `result` (final text), `num_turns`, `duration_ms`, `duration_api_ms`, `total_cost_usd`, `usage`, `modelUsage`, `permission_denials`, `result_index`, `queued_turn_count`, `ttft_ms`, `session_id` |

Semantics that bite:

- **`total_cost_usd` and `duration_api_ms` are session-cumulative**, not per turn
  (0.152 → 0.180 → 0.202 → …; api_ms 3417 → 5086 → 10364). `duration_ms` and
  `num_turns` are per turn. `result_index` counts turns from 0. A per-turn cost is a
  delta the reader computes.
- `result.subtype` stays **`success` even when a tool was denied** — denial shows up
  only in `permission_denials[]` (and the `system/permission_denied` event). A driver
  that checks `subtype` alone reads a blocked action as a good turn.
- `assistant.message.stop_reason` is `null` in the snapshot; the real stop reason is on
  `result.stop_reason` (`end_turn`).

## 3. Permissions — "out of the box" verified

`Read` never asks (default read-only allow). `Write` does:

- **With `--permission-prompt-tool stdio`** → the CLI writes a `control_request`
  `{"subtype":"can_use_tool","tool_name":"Write","input":{…},"tool_use_id":…,
  "permission_suggestions":[{"type":"setMode","mode":"acceptEdits","destination":"session"}]}`
  and **blocks the turn** until the writer answers with a `control_response`
  (`behavior: allow|deny`). Answered allow → file written, `permission_denials: []`.
  This is the whole handshake; ~20 lines in `session.py`.
- **With `--permission-prompts none`** (no handler) → auto-deny: a
  `system/permission_denied` event (`decision_reason_type: "asyncAgent"`, message text
  tells the model not to retry for the rest of the session), `permission_denials`
  populated on `result`, turn still `success`. Nothing hangs.
- Without either flag (default `--permission-prompts host`) and no writer that answers —
  not tested; the SDK path always sets `stdio` when a callback exists.

Verdict: the permission path works out of the box on the raw wire; the only design
decision is who answers `can_use_tool`.

## 4. Raw wire vs `ClaudeSDKClient` (oracle)

`oracle_sdk.py` ran the same turns through the SDK. Its argv is the raw one
(`--output-format stream-json --verbose --system-prompt '' --permission-prompt-tool stdio
--setting-sources= --input-format stream-json`), plus env `CLAUDE_CODE_ENTRYPOINT=sdk-py`.
Same event kinds (`SystemMessage RateLimitEvent AssistantMessage UserMessage ResultMessage`),
same cumulative cost semantics, same `can_use_tool` round-trip.

What the SDK adds on top of the wire:

- an `initialize` control_request first (response: `account`, `models`, `commands`,
  `agents`, `output_style`, `pid`) — **optional**, the raw wire works without it;
- typed messages + `can_use_tool` / `interrupt` / `set_permission_mode` / `set_model` /
  `get_context_usage` as methods — all just `control_request` subtypes
  (`_internal/query.py:725-804`), reachable raw with the same envelope;
- hook callbacks and in-process MCP servers over the same control channel.

Cost of going raw: none observed for this scope. Cost of the SDK: Python-in-process only,
one more dependency layer between a future holder and the wire.

## 5. `--include-partial-messages`

Same four turns: 18 → 117 lines. Extra per model message: `stream_event` with
`message_start`, `content_block_start`, `content_block_delta`
(`text_delta` / `input_json_delta` — 49 of the 117 lines were tool-input JSON fragments),
`content_block_stop`, `message_delta` (carries the real `stop_reason` and usage),
`message_stop`; plus `system/status {"status":"requesting"}` before each request. The
turn boundary and the `assistant` snapshots are unchanged — a reader that ignores
`stream_event` sees exactly the §2 stream. Turn it on only for token-level streaming.

## 6. Latency / cost (opus, 1M context, default model of the account)

Per turn: text-only 1.7–4.6 s wall; one tool round-trip 5.3–6.9 s. Cumulative cost after
4 turns ≈ $0.12–0.20 (per-turn deltas $0.02–0.03). `ttft_ms` ≈ 900 ms on `message_start`.

## 7. Recommendation for the headless epic (taken up by ADR-0038)

- **API shape = this wire, as is.** A session holder is a process that owns the child's
  stdin/stdout and re-exposes exactly these lines (plus its own `attach`/`detach`); no
  translation layer is needed for phase 2. Keep `control_request` inbound and
  `control_response` outbound in the holder's contract from day one — that is where
  permissions, interrupt and mode changes live.
- **Turn boundary = `result`.** Never silence. Per-turn cost = delta of `total_cost_usd`.
- **Read `permission_denials`**, not `subtype`, to know whether an action happened.
- Phase 2 open questions the PoC did not touch (by scope): cross-process resume
  (`--session-id`/`--resume`), `interrupt` mid-turn, `--bg` (docs: TTY `attach` only),
  and whether the ai-hats canonical event vocabulary (`ai_hats_observe.canonical`) should
  map this stream in phase 3.
- Pin the wire by CLI version in the holder; it is undocumented and can move.

## 8. Follow-up probes after review questions

- **`system/init` is a per-turn capability snapshot**, not a start marker: `model`,
  `permissionMode`, `cwd`, `claude_code_version`, `apiKeySource`, `tools[]`,
  `mcp_servers[]` (name + `status`), `slash_commands[]`, `skills[]`, `agents[]`,
  `plugins[]`, `memory_paths`, `output_style`, `fast_mode_state`. Between turn 1 and
  turn 2 `tools` grew 28 → 36 as the account's `claude.ai Claude Docs` MCP server
  finished connecting — that is the reason it is re-emitted every turn: it is where a
  reader learns what the model can do *now* (also after `set_permission_mode`).
- **`--setting-sources ""` does not isolate the session from the account**: the
  claude.ai connectors (MCP: Claude Docs, Google Calendar), 53 slash commands, 18
  bundled skills and the `agents-md@builtin` plugin were all present. Local
  `settings.json` / `CLAUDE.md` / hooks were cut, account-level surface was not. A
  holder that wants a bare model needs more than this flag (`--bare` untested).
- **Cross-process resume works**, even after the original temp cwd was deleted:
  `claude -p --resume <session_id> --output-format json "<question>"` from another
  directory returned the secret word of the headless session and kept the same
  `session_id`. Phase-2 open question "resume between processes" — answered yes.
- **Pure-shell duplex one-liner** (one turn, exits when stdin closes):
  `printf '%s\n' '{"type":"user","message":{"role":"user","content":"…"},"parent_tool_use_id":null,"session_id":"default"}' | claude --output-format stream-json --verbose --input-format stream-json --setting-sources "" --system-prompt "" | jq -c '{type,subtype,result}'`
- **API contract note (supervisor, review):** the holder's turn result
  MUST surface `permission_denials[]` as a first-class field / non-success outcome —
  `result.subtype == success` alone is not "the action happened".

## 9. Review probes (`probe_thinking.py`)

**Model and the other harness parameters are plain argv.** The PoC runs the account
default (`init.model = claude-opus-5[1m]`); `--model sonnet` in the same argv gives
`init.model = claude-sonnet-5`. Everything ai-hats materialises for the automate path is an
`ClaudeAgentOptions` document (`src/ai_hats/surfaces/claude/provider.py:265-330`:
`system_prompt`, `settings`, `setting_sources`, `plugins`, `cwd`, `env`, `model`,
`session_id`) that the SDK renders to CLI flags one-to-one
(`subprocess_cli.py:466-648`: `--system-prompt` / `--append-system-prompt`, `--settings`,
`--setting-sources=`, `--plugin-dir`, `--model`, `--effort`, `--max-turns`,
`--max-budget-usd`, `--allowedTools` / `--disallowedTools`, `--add-dir`, `--mcp-config`,
`--session-id=` / `--resume=` / `--fork-session`); `cwd` and `env` are `Popen` arguments.
The HITL path already emits argv (`--settings <path>`). So a holder consumes the existing
materialisation without a new seam — the remaining check is empirical (one materialised
role over the raw wire), which is epic phase 3, not this PoC.

**Reasoning and the answer are separable — inside one turn, not as separate turns.**
With a thinking budget (`--max-thinking-tokens 4000`; `--thinking adaptive` chose 0 tokens
on an easy prompt) the turn carries two `assistant` events: one whose `message.content`
block is `{"type":"thinking",…}` and one with `{"type":"text",…}`; `result.usage.
output_tokens_details.thinking_tokens` counts it. With `--include-partial-messages` the
reasoning streams as `content_block_start type=thinking` → `thinking_delta`×n →
`signature_delta` → `content_block_stop`, then the text block. The turn boundary is still
the single `result`.

Visibility is a flag: `--thinking-display summarized|omitted|highlights` (not in `--help`;
choices from the CLI's own error). Default behaved as `omitted` — the `thinking` block is
present but its text is empty while `thinking_tokens` > 0; `summarized` fills the block
(563 chars for the puzzle) and the deltas. The SDK exposes the same via
`ClaudeAgentOptions.thinking = {"type": "enabled"|"adaptive", "budget_tokens", "display"}`
(`subprocess_cli.py:627-644`). ai-hats already types this distinction
(`ThinkingBlock` in `sdk_runner.format_reasoning`, `ThinkingItem` in
`ai_hats_observe.canonical`) — the raw wire gives the holder the same split.
