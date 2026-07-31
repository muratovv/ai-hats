# agy JSONL fixtures

Record shapes copied from a real
`~/.gemini/antigravity-cli/brain/<uuid>/.system_generated/logs/transcript.jsonl`,
with content scrubbed to canaries per the `claude_jsonl/README.md` rules.

The load-bearing property is **what is not there**: no agy record carries a
usage/token field of any kind, on any `type`. A fixture that invented one would
hide the defect these fixtures exist to pin — a surface with no token telemetry
recorded as a measured `output: 0`, which `is_zero_output` then read as "the
sub-agent produced nothing" and the harness turned into a discarded run.

## `one_turn_no_telemetry.jsonl`

Verbatim record *shape* of session `20260731-100500-1-40786` (the run whose
verdicts were discarded): one `USER_INPUT`, one `PLANNER_RESPONSE` with prose
and no `tool_calls`, plus the two `SYSTEM` types the parser must skip —
`CONVERSATION_HISTORY` (no `content` key at all) and `CHECKPOINT`.

Expected parse: **1 turn, 0 tool calls, no token counts**.
