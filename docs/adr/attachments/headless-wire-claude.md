# Headless wire — claude, measured on the live binary

Attachment of ADR-0038. §1–9 were measured by the PoC HATS-2014 (its `session.py` /
`drive.py` / `probe_thinking.py` live on that card), §10 by the PoC HATS-2025 (its probes
live on that card). This file is the observation the ADR cites and may be re-measured when
the CLI moves. No session recordings here — schema and redacted fragments only.

§1–9 pinned to: `claude` **2.1.278**, `claude-agent-sdk` **0.2.126** (oracle), macOS, 2026-09-21.
§10 pinned to: `claude` **2.1.281**, macOS, 2026-09-24.
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

| order | `type/subtype`             | notes                                                                                                                                                                                                                                                                          |
| ----- | -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 1     | `system/init`              | **re-emitted on every turn** (new `uuid`, same `session_id`); carries `model`, `permissionMode`, `tools`, `session_id`, `claude_code_version`, `apiKeySource` (`none` on a subscription login — the binary is authenticated by `HOME`, not by a key in the env) …              |
| 2..n  | `assistant`                | one per model message; `message.content` blocks: `text` / `tool_use` (with **full** `input` — no delta assembly needed at this level)                                                                                                                                          |
|       | `user`                     | tool result fed back (`message.content[].type == "tool_result"`, plus a top-level `tool_use_result`)                                                                                                                                                                           |
|       | `control_request`          | inbound question to the writer; only seen `subtype: can_use_tool` (see §3)                                                                                                                                                                                                     |
|       | `rate_limit_event`         | first turn only; `rate_limit_info.unifiedWindows.{five_hour,seven_day}.utilization`                                                                                                                                                                                            |
|       | `system/permission_denied` | when `--permission-prompts none` denies a tool (see §3)                                                                                                                                                                                                                        |
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

## 10. The HITL argv of a real role, interrupt, EOF, hooks, the record (2.1.281)

Measured with the argv `session_plan.launch` builds for a HITL session of the role `maintainer`
(`ai-hats -r maintainer --dry-run-json --materialize`, tree copied out), with the wire flags in
front of it, the way `LaunchFlags.extra_args` is placed:
`claude --input-format stream-json --output-format stream-json --verbose --permission-prompt-tool stdio --system-prompt-file <root>/prompt.md --plugin-dir <root>/plugin --settings <root>/settings.json --session-id <uuid>`.
The child env is the §1 allowlist only — no `AI_HATS_*` — and user settings are NOT cut: this
argv passes no `--setting-sources`. Mechanics probes (interrupt, EOF, permissions) ran on the §1
argv with `--model sonnet`.

**The role's argv works over the wire.** The session keeps three turns in one pid under the
`session_id` passed in `--session-id`. The model quotes the role prompt's `# ROLE:` heading and
recalls turn 1 in turn 3; `system/init` lists the role plugin (`source: "<name>@inline"`) and
all of its skills (`<plugin>:<skill>`). Negative control: without `--system-prompt-file` and
`--plugin-dir` the heading is `NONE` and no role skill is listed. `permissionMode` is the
user's `defaultMode` (`auto` here), inherited through the user settings.

**Hooks fire.** Hooks from `--settings` and from the user settings run in stream-json mode. A raw `command` hook on `PreToolUse`/`PostToolUse` wrote its marker; the
ai-hats dispatcher, left without its env, refused Bash and `Write` with exit 2. The refusal
reached the model as a `tool_result` with `is_error: true`, text
`PreToolUse:<Tool> hook error: [<the whole hook command>]: <stderr>`, and put the call into
`result.permission_denials[]`. `--include-hook-events` adds `system/hook_started` and
`system/hook_response` (`hook_name`, `exit_code`, `outcome`, `stdout`, `stderr`) to stdout.

**A hook that answers `ask` becomes a wire question.** With `--permission-prompt-tool stdio`, a
`PreToolUse` hook printing `permissionDecision: "ask"` yields a `control_request`
`can_use_tool` with `decision_reason: <the hook's reason>` and `decision_reason_type: "hook"`,
answered by the same `control_response` as any other question: allow runs the tool, deny
returns the message as an error `tool_result`. `auto` mode does not swallow the hook's
question. Without a handler it turns into `system/permission_denied` with
`decision_reason_type: "hook"`.

**`interrupt`.** `{"type":"control_request","request_id":<new>,"request":{"subtype":"interrupt"}}`
is answered at once with
`{"type":"control_response","response":{"subtype":"success","request_id":<same>,"response":{"still_queued":[]}}}`.
Then:

| When                    | What follows on stdout                                                                                                                                                                               | `result`                                                                                                       |
| ----------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| the model is generating | `assistant` with the partial text, `user` `[Request interrupted by user]`                                                                                                                            | `subtype: error_during_execution`, `is_error: true`, `stop_reason: null`, `terminal_reason: aborted_streaming` |
| a tool is running       | `tool_result` "The user doesn't want to proceed with this tool use…", `user` `[Request interrupted by user for tool use]`, `system/task_notification` `status: stopped` — the tool process is killed | `subtype: error_during_execution`, `is_error: true`, `stop_reason: tool_use`, `terminal_reason: aborted_tools` |
| no turn is running      | nothing                                                                                                                                                                                              | none                                                                                                           |

The next `prompt` is accepted in every case; `result_index` keeps counting. `interrupt` does
not stop background tasks (`run_in_background`).

**EOF on stdin.** Accepted turns run to the end, and the process exits 0. A question open at EOF
fails as a `tool_result` error
"Tool permission request failed: AbortError: Tool permission stream closed before response received";
every later question fails at once with "AbortError: Stream closed" and never reaches stdout. The
model retried each refused call one to three times before it ended the turn, so the failure
costs API calls, not a hang. Background tasks are stopped about 5 s after EOF; the turn their
completion would have started never runs.

**No handler, no TTY** (`--permission-prompts host`, no `--permission-prompt-tool`). Under the
user's `auto`, `Write` in the cwd ran without a question. Under `--permission-mode manual`
(reported as `default` in `system/init`) it was refused as with `--permission-prompts none`
(§3). Neither hung.

**The record.** The transcript lands at `~/.claude/projects/<key(realpath cwd)>/<session_id>.jsonl`
— exactly the path `resolve_transcript` returns. Per prompt it holds `queue-operation`
(`enqueue`, `dequeue`) and a `user` record with `promptSource: "sdk"`. Per model message it
holds one `assistant` record per content block, each already carrying the final
`message.stop_reason`, then `user` records with `tool_result`s. It holds no turn-end record:
`system/turn_duration` is absent in this mode, and `system/stop_hook_summary` appears only
when a Stop hook is configured. The turn's last `assistant` record is on disk when `result`
reaches stdout (7 of 7 turns, read synchronously on `result`). An `EventLogWriter` following
it lags `result` by at most one tick (median 0.13 s, max 0.26 s, 28 turns). `PromptReceived`
comes about 1 s after the prompt is written, when the binary starts the turn, with
`origin: harness`.

**The turn boundary is missing from the live log.** The live claude reader closes a response
only when a response with another `requestId` starts, on an interrupt marker, or on `close()`.
So the `ResponseEnded` of a turn's last response reaches `events.jsonl` only when the next
turn's first response does, or at `close()` after EOF — in 24 of 25 turns measured. The one
exception was a turn cut by `interrupt`, whose marker closes it. A client that waits for
`response_ended` before sending the next `prompt` waits forever. The wire `result` is the
boundary, and the record is complete by then.

**New on the wire since 2.1.278.**

- `system/thinking_tokens`.
- `system/hook_started` / `hook_response`, with `--include-hook-events`.
- `system/task_started` / `task_updated` / `task_notification` / `background_tasks_changed`.
- A turn with no `prompt`: when a background task completes, the binary starts a turn of its
  own, from `system/init` to `result`. The count of `result`s is not the count of prompts.
- `can_use_tool` fields: `display_name`, `description`, `blocked_path`, `decision_reason`,
  `decision_reason_type`, `permission_suggestions` (`addRules`, `addDirectories`, `setMode`).

### 10.1 Second round: gates that ask, interrupt with a question open, EOF and SIGTERM, modes

**The role's real gates over the wire.** The dispatcher was armed with a `SessionIdentity` env
and the manifest pointed at the copied tree. `git push --force` and `git push` to a
throwaway local remote got the shared-state gate's `ask`, which arrived as `can_use_tool` with
`decision_reason_type: "hook"` and the gate's full text as `decision_reason`. `answer` allow
**ran the push**. `sed -i` got the destructive gate's `deny`: no question, the gate's text in
an error `tool_result`, the call in `permission_denials`. The dispatcher writes
`GateVerdict(before_tool)` for every call, and for an `ask` also `PersonAsked(call_id=<tool_use_id>)`,
to the session's `events.jsonl` before the question reaches stdout.

**Precedence.** A hook's `ask` still reaches the wire under `--permission-mode bypassPermissions`
and next to `permissions.allow: ["Bash"]`. A hook's `allow` runs `Write` under `manual` with no
handler and no question. A JSON `deny` from a hook comes back as
`PreToolUse:<Tool> hook error: <reason>`; the whole hook command was echoed only when a hook
exited 2 (the dispatcher left without its env). An
`answer` allow with an edited `updatedInput` runs the edited call, and neither the model nor
the `tool_result` shows the edit.

**`interrupt` with a question open.** The binary withdraws its own question first —
`{"type":"control_cancel_request","request_id":<the can_use_tool's id>}` — then closes the turn
as for a running tool (`aborted_tools`). A late `control_response` to the withdrawn
`request_id` is ignored without a line in reply. Turns already queued run after the
interrupted one; `still_queued` came back empty although a prompt was waiting.

**EOF and SIGTERM.**

- A deny sent right before EOF ("the session is ending, do not retry") cut the retries to 0
  and the time from EOF to exit to 1.8–2.4 s, against 2–3 retries and 5.9–9.0 s with the
  question left open (two runs each).
- EOF while a foreground tool runs waits for the tool and finishes the turn, then exits 0.
- SIGTERM to the binary exits 143 within a second with no `result` line. The binary kills a
  running tool first (`tool_result` "Exit code 137"), and a partial answer is not written to
  the transcript.

**Modes with no handler and no TTY.** Under the user's `auto` with `stdio`, none of `Write` in
the cwd, `Write` outside it (another temp dir), `rm -rf ./dir`, `curl -sI` or `chmod -R 777 .`
reached the wire; all ran. `acceptEdits` and `bypassPermissions` ran `Write` and `mkdir`.
`dontAsk` refused what no allow rule covered (`system/permission_denied`,
`decision_reason_type: "mode"`). `plan` wrote a plan file and refused the rest, with
`ExitPlanMode` unavailable, so leaving it takes `set_permission_mode` from outside.

## 11. The wire as the main agent's record (2.1.281, three probes)

Measured to make the wire the only source of the main agent's events in headless (ADR-0038
D4), with the holder's three flags `--replay-user-messages`, `--include-partial-messages` and
`--include-hook-events`. A scrubbed copy of one session, seen from both the wire and its
transcript, is the fixture `packages/ai-hats-observe/tests/fixtures/wire/`.

**Who a turn answered.**

- The binary keeps the `uuid` of an input line: it is the transcript record's `uuid`, the
  wire's echo (`isReplay: true`) and an entry of `result.user_message_uuids`.
- It takes any string there, including a non-UUID. A repeated `uuid` is dropped with no turn,
  no `command_lifecycle` and no record, but the echo still comes.
- A prompt that arrives before a tool boundary joins the running turn: one `result`, both ids
  in `user_message_uuids`. The joined prompt has no `user` record of its own in the transcript,
  only an `attachment/queued_command`.
- A prompt that arrives while the model writes without a tool call waits. Its echo comes after
  the running turn's `result`.
- A turn the binary starts on its own (a background task finished) has no echo, and its
  `user_message_uuids` is `[]`. Its notification is in the transcript only.
- `command_lifecycle` (`queued` → `started` → `completed`, by `command_uuid`) tells the same
  story line by line.

**The same `message`, other field names.** Every content line of the wire has a transcript
record with the same `uuid`: 6 of 6, 35 of 35 and 14 of 14 in the three probes. Some fields
are spelled differently on the wire:

| Wire                         | Transcript                  |
| ---------------------------- | --------------------------- |
| `request_id`                 | `requestId` (equal, 9 of 9) |
| `is_api_error_message: true` | `isApiErrorMessage: true`   |
| `isSynthetic: true`          | `isMeta: true`              |
| echo: no `promptSource`      | `promptSource: "sdk"`       |

`timestamp` is on every `assistant` and `user` line of the wire, and on nothing else.
`apiErrorStatus` is only in `result.api_error_status`.

**A response's end.** An `assistant` line's `stop_reason` is null, and its `usage` is the one
of `message_start` (`output_tokens` 1–4). The final `usage` — all four counters, equal to the
transcript in 4 responses of 4 — and `stop_reason` come in `stream_event` `message_delta`. That
event comes after every fragment of the response and before its `message_stop`. It is on the
wire only with `--include-partial-messages`. A turn that failed on the API has no
`message_start` or `message_delta`.

**Hooks.** With `--include-hook-events` each hook run gives `system/hook_started` and
`system/hook_response`: `hook_name`, `hook_event`, `exit_code`, `outcome`, `stdout`, `stderr`,
and no command. A Stop hook's `hook_name` is `"Stop"`. The transcript records all Stop hooks of
one stop in one `stop_hook_summary`. A hook that exited 2 is `outcome: error`, and the
transcript puts its text in `hookErrors` with `preventedContinuation: false`. After a Stop hook
that blocked, the wire carries a `system/notification` for the UI.

**Sub-agents.** A sub-agent's lines carry `parent_tool_use_id`. The wire misses the sub-agent's
first thinking and its whole final answer, so the sub-agent's own record stays the source. Its
hooks' lines carry no `parent_tool_use_id`.

## 12. Questions answered over the wire (2.1.282, haiku)

Measured for the `answer` command, with `--permission-prompt-tool stdio` and one run per
case. The questions came to the driver as `control_request` `can_use_tool`, and it answered
each with `control_response`.

**A hook's rewrite rides on the question.** The case was a PreToolUse hook that answers `ask`
and rewrites the command in `updatedInput`, the way a consent point adds its ticket.

- The question's `input.command` is the rewritten command. The hook itself saw the original.
- An allow that echoes `input` as `updatedInput` runs the rewritten command.
- An allow with no `updatedInput` at all runs the rewritten command too.
- In both cases the model reported that it ran the original command.

**Plan mode is not a dead end with a question channel.**

- Under `--permission-mode plan` the model calls `ExitPlanMode`, and the call comes as
  `can_use_tool` with `input.plan`, `input.planFilePath` and the key
  `requires_user_interaction`.
- An allow is followed by `system/status` with `permissionMode: default`. The next `Write`
  is asked as usual and runs on allow.
- With the person's own settings loaded, the mode after the allow is still `default`, not the
  settings' `auto`.
- The dead end in §10.1 was measured without a question channel.

**The model's own question.**

- `AskUserQuestion` comes as `can_use_tool` with `input.questions[]` and
  `requires_user_interaction`.
- An allow that echoes `input` gives the model the tool result "The user did not answer the
  questions.", and the model asks again in words.
- An allow whose `updatedInput` adds `answers` (question text → chosen label) gives "The user
  answered: …", and the model goes on with that answer.

**A gate's question reaches the log before the wire.**

- On a live maintainer session the shared-state guard's `GateVerdict(ask)` and
  `PersonAsked(source: chain)` were in `events.jsonl` before the `can_use_tool` line reached
  the holder.
- A client that answered from the log did so in that gap.
- About six seconds into an unanswered question the `Notification` hook fired
  `permission_prompt`, with no call id.
