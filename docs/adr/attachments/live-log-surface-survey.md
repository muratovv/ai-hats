# Live-log completeness across the five surfaces

Survey date 2026-09-15. Corpora: claude 3015 transcripts (2734 top-level + 281
sub-agent) + 12 live events.jsonl; codex 128 rollouts (cli 0.146–0.153);
cline 1433 sessions (cli 3.0.3); agy 500 sessions; opencode 18 sessions
(1.18.2x). Reference vocabulary: t3code `packages/contracts/src/providerRuntime.ts`
at d29c56a5 (six providers normalised into one runtime-event list).

Legend: **S** = structured on disk in the record ai-hats follows · **s** =
structured on disk, but in a record ai-hats does not follow today · **p** =
prose / free text only · **h** = reachable only through a hook or in-process
bus · **–** = nowhere.

## The record a live follower can tail

| surface  | record ai-hats resolves today                         | tailable?                                                              | complete?                                                                                                  |
| -------- | ----------------------------------------------------- | ---------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| claude   | `~/.claude/projects/<cwd>/<sid>.jsonl`                | yes, append                                                            | content yes; sub-agents in `<sid>/subagents/agent-*.jsonl` (not followed)                                  |
| codex    | trace scrape (no parser)                              | rollout JSONL is append, monotonic ts                                  | rollout is complete; sub-agents/guardian in sibling files keyed by `parent_thread_id`                      |
| cline    | `<id>.messages.json` (parser exists)                  | **no — whole-object rewrite**; `hooks.jsonl` is append (headless only) | transcript has no errors/interrupts; those live in `hooks.jsonl` / `cline.log`                             |
| agy      | `brain/<sid>/.system_generated/logs/transcript.jsonl` | yes, append                                                            | **lossy**: every 429/503/cancel step is dropped; complete record is `conversations/<sid>.db` (proto blobs) |
| opencode | trace scrape (no parser)                              | sqlite WAL `opencode.db`, `event` table with per-session `seq`         | content yes; permission ask/reply and idle/status are bus-only                                             |

## Gap × surface

| gap                                  | t3code event                                   | shape neutral? | claude                                                                                          | codex                                                                                                                                   | cline                                                                                          | agy                                                                      | opencode                                                                              |
| ------------------------------------ | ---------------------------------------------- | -------------- | ----------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------ | ------------------------------------------------------------------------------------- |
| 1 run started / ended                | `session.started` / `session.exited{exitKind}` | yes            | – / –                                                                                           | S `session_meta` / –                                                                                                                    | S meta `started_at` / S `ended_at`,`status` (written at exit; reconciled weeks later on crash) | implicit / –                                                             | S `session.created.1` / – (`session.idle` bus-only)                                   |
| 2a question to person (open → close) | `user-input.requested` / `.resolved`           | yes            | S `AskUserQuestion` tool_use → tool_result by id (426; 0 left open; p90 62 min)                 | s `request_user_input_async` → closes **immediately** `{"accepted":true}`; the answer is the next turn, unlinked (5)                    | S `ask_question` tool_use → tool_result by id (12; 1 open for 25 h)                            | – (`ASK_QUESTION` in binary, 0 emitted)                                  | S `question` tool part `pending→running→completed` (2; 11.6 min)                      |
| 2b approval prompt (open)            | `request.opened{requestType}`                  | yes            | h: chain `ask` verdict (ours); own prompt **invisible** (Notification hook `permission_prompt`) | h: ai-hats `PermissionRequest` hook; `approvals_reviewer:user` → nothing on disk; `auto_review` → verdict JSON in sibling guardian file | – (no per-tool approval; mode is per session `yolo/act/plan`)                                  | – (`steps.permissions` empty ×2433; Notification/Stop hooks registered)  | h: bus `permission.asked` (our plugin hooks it); log `asking id=per_…`, no reply line |
| 2c approval resolved                 | `request.resolved{decision}`                   | yes            | p tool_result prose (approve = tool ran)                                                        | S `item_completed{status:declined, stderr}` / allow = `completed`                                                                       | –                                                                                              | –                                                                        | p tool `state.error="The user rejected…"` (person vs policy only by latency)          |
| 3 interrupt                          | `turn.aborted{reason}`                         | yes            | p user text `[Request interrupted by user]` (51)                                                | S `turn_aborted{reason:interrupted}` (16)                                                                                               | – (hub log `run.abort`; transcript: assistant `content:[]`)                                    | s sqlite `status=6 CANCELED`, dropped from transcript                    | S `message.error.name=MessageAbortedError` (3)                                        |
| 4 sub-agent activity                 | `task.started/completed`, parent link          | yes            | s separate files; link via parent `toolUseResult.agentId`                                       | S separate files, `session_meta.parent_thread_id`, `SubAgentActivity` items                                                             | schema only, 0 instances                                                                       | – (background tasks in-band, `invoke_subagent` 0)                        | S child `session.parent_id` + parent `task` part metadata (both ways)                 |
| 5a rate-limit pre-warning            | `account.rate-limits.updated`                  | yes            | h SDK `RateLimitEvent` only (0 in JSONL)                                                        | **S on every response**: `token_count.rate_limits.primary.used_percent/resets_at`                                                       | –                                                                                              | –                                                                        | – (0 evidence, free models)                                                           |
| 5b rate-limit wall + retry-after     | `HarnessActionRequired(WAIT, retry_after)`     | yes            | S `quotaLimits.resetsAt`                                                                        | S `task_complete.error.codex_error_info=usage_limit_exceeded`, date in prose (+ last `resets_at`)                                       | p `hooks.jsonl` "Usage limit reached… reset at …"                                              | s sqlite proto `quotaResetTimeStamp`, `retryDelay`                       | –                                                                                     |
| 6 tool denied by the surface's gate  | `tool.denied{toolName, toolUseId, reason}`     | yes            | p two prose shapes (28 classifier + 23 person)                                                  | S `status:declined` + guardian `{risk_level, outcome, rationale}`                                                                       | S `is_error` (133, all schema validation — not a person)                                       | –                                                                        | p `state.error` (5)                                                                   |
| API errors (kind)                    | `runtime.error{class}`                         | yes            | S 6-value `error` + `apiErrorStatus`                                                            | S `codex_error_info` enum (4 kinds)                                                                                                     | p free text (995 auth, 10 billing…), typed `sdk.error.error_status` only since 09-07           | s google.rpc.Status in proto, 99 sessions die silently in the transcript | S `error.{name, statusCode, isRetryable}`                                             |
| usage per response                   | `thread.token-usage.updated`                   | yes            | S per fragment (dedupe by requestId)                                                            | S `token_count` + `token_usage_record` (0.153.4)                                                                                        | S `metrics{}` per assistant msg                                                                | – (context size only, unlabeled proto)                                   | S per step-finish + per message + session cumulative                                  |
| context compacted                    | `thread.state.changed{compacted}`              | yes            | S `compact_boundary`                                                                            | S `compacted` / `context_compacted`                                                                                                     | –                                                                                              | p in-band `CHECKPOINT`                                                   | s `session.time_compacting`                                                           |
| model switched                       | `model.rerouted`                               | yes            | S `model_refusal_fallback`                                                                      | –                                                                                                                                       | –                                                                                              | –                                                                        | –                                                                                     |
| turn ended                           | `turn.completed{state}`                        | yes            | S `stop_hook_summary` (our at_stop) / `turn_duration`                                           | S `task_complete{duration_ms}`                                                                                                          | S `agent_end` (hooks.jsonl)                                                                    | implicit (planner response without tool calls)                           | S `step-finish.reason=stop` / `message.finish`                                        |

## What this says

1. **Every gap has a surface-neutral shape.** t3code carries all six as
   first-class events across six providers; nothing in the proposal is a
   Claude concept wearing a neutral name.
2. **What is surface-specific is the source, not the event.** Three source
   classes recur: the record on disk (codex is the richest, agy's transcript
   the poorest), a hook / bus (the only place any surface shows an approval
   prompt *before* it resolves), and nowhere.
3. **Approval-waiting has one neutral producer: ai-hats' own chain.** No
   surface persists its own approval prompt before resolution (codex with
   `user` reviewer, cline, agy: nothing; opencode: bus; claude: Notification
   hook). The dispatcher (`hook_dispatch._say`) is already the seam every
   surface passes through, so `PersonAsked(kind=permission)` belongs beside
   `GateVerdict(ask)` there, with a native hook per surface only where one
   exists. The closer is the tool result keyed by call id on four of five
   surfaces (agy keys by step).
4. **Question-waiting is a tool call on four of five surfaces** (claude
   `AskUserQuestion`, cline `ask_question`, opencode `question`, codex
   `request_user_input_async`) — the reader maps the surface's tool name onto
   `PersonAsked(kind=question)`. Codex breaks the close-by-call-id contract
   (the answer arrives as a new prompt); agy has none.
5. **The pre-warning is a threshold event on claude and a continuous gauge on
   codex** (`used_percent` on every response). `Notice(APPROACHING_LIMIT)`
   needs a threshold policy for codex, or a gauge event beside it.
6. **Run lifecycle has no reliable end record on any surface** — the writer
   is the right producer, and it is surface-independent by construction.
7. **Interrupt is structured on codex and opencode, prose on claude, absent
   from cline's and agy's transcripts.**
8. **Three of five surfaces cannot be followed through the record ai-hats
   resolves today**: cline rewrites the file (follow `hooks.jsonl` instead),
   agy's transcript drops every failure (follow the sqlite), opencode has no
   reader at all (sqlite `event` table with a per-session `seq` cursor). That
   is HATS-1968's per-surface split, not this card.

## Claude-specific in the vocabulary as it stands

- `GateVerdict(point=at_stop)` is produced from Claude's `stop_hook_summary`;
  agy has a Stop hook, codex/cline/opencode have no stop-hook notion.
- `Notice(MODEL_SWITCHED)` has a producer only on claude.
- `raw_code` and `source` values are the surface's own by design.
- Everything else (`PromptReceived`, `ResponseStarted/Ended` + usage,
  `ItemEmitted`, `ToolResultReceived` by call id, signals by obligation) has
  a producer on ≥4 surfaces.
