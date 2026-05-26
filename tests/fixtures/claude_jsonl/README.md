# Claude JSONL fixtures

Hand-crafted JSONL fixtures shaped after the real
`~/.claude/projects/<key>/<uuid>.jsonl` records that the `claude` CLI
emits. Consumed by `AuditWriter._parse_jsonl` (see
`src/ai_hats/observe.py`). Their purpose is to lock the parser's output
shape so a future schema drift in the `claude` binary's session log
fails fast in CI rather than silently breaking `audit.md` observability
downstream.

## Scrub whitelist (HATS-529)

These fixtures were derived from real local sessions, then aggressively
pruned per the project rule "What NOT to commit" in `CLAUDE.md` ("Real
Claude session recordings… personal data"). Every field carried over
falls into one of two classes:

### Kept verbatim (with canary values, never real content)

| Field | Notes |
|---|---|
| `type` | `user` / `assistant` (parser dispatch key). |
| `timestamp` | Fixed `2026-01-01T00:00:0Xs.000Z`. |
| `message.role` | `user` / `assistant`. |
| `message.model` | Fixed `claude-opus-4-7` (parser tracks per-model stats). |
| `message.content[*].type` | `text` / `tool_use` / `tool_result` / `thinking`. |
| `message.content[*].text` | Canary strings (`alpha`, `beta`, `alpha-response`, `beta-done`). NEVER real prompts. |
| `message.content[*].thinking` | Short canary phrase. |
| `message.content[*].name` | Tool name (`Bash`). |
| `message.content[*].input` | Stub `{"command": "echo hi"}`. NEVER real commands. |
| `message.content[*].tool_use_id` | Fixed `toolu_001`. |
| `message.usage` | Small realistic integers (50/5/0/0, 60/30/100/0, 70/8/160/0). |

### Stripped / replaced

| Field | Action |
|---|---|
| `cwd` | Removed (contains absolute home path). |
| `sessionId` | Fixed `00000000-0000-0000-0000-000000000000`. |
| `uuid`, `parentUuid` | Fixed `00000000-0000-0000-0000-00000000000N`. |
| `requestId`, `promptId`, `entrypoint`, `userType`, `permissionMode`, `isSidechain`, `gitBranch`, `version` | Removed. |
| `message.id`, `stop_reason`, `stop_sequence`, `stop_details`, `diagnostics` | Removed. |
| `message.usage.cache_creation`, `iterations`, `server_tool_use`, `service_tier`, `inference_geo`, `speed`, `ephemeral_*_input_tokens` | Removed (parser ignores these sub-keys). |

If a future test needs a field outside this whitelist to exercise a
real parser regression, **escalate to the user** — do not widen the
whitelist unilaterally (privacy rule trumps test fidelity).

## Files

### `three_turns_with_tool.jsonl`

Exercises the three structural shapes `_parse_jsonl` must handle:

1. **Text-only turn** — `user "alpha"` → `assistant text "alpha-response"`.
2. **Text + tool_use turn (with thinking)** — `user "beta"` →
   `assistant {thinking, tool_use Bash}` → `user tool_result` →
   `assistant text "beta-done"`. All three assistant blocks should
   collapse into the same `Turn` (`current` continues until the next
   non-tool-result user message).
3. **Slash-command turn (documented exception)** — `user "/exit"`
   is filtered by `_extract_user_text` (any content starting with `/`
   is treated as a system command and skipped). Expected: no `Turn`
   created, no off-by-one against the assistant count.

Expected parser output: **2 turns**, both with their respective markers
in the rendered `audit.md`. See
`tests/test_audit_writer_parse_jsonl_fixture.py` for the assertions.
