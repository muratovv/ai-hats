# Synthetic session artifacts

Realistic-shape fixtures of what ai-hats writes per session. **Synthetic** —
the session id `20260406-034154-1` is fabricated; the file *structure* is
the same as a real session under `<ai_hats_dir>/sessions/runs/`.

| File | Schema | What it contains |
|---|---|---|
| [`audit.md`](audit.md) | none — human-readable | Enriched turn-by-turn log written by `AuditWriter` after session end (user/tool/assistant icons, command lines, response excerpts). |
| [`metrics.json`](metrics.json) | none — fixed keys | `exit_code`, `turns`, `tool_calls`, `tokens` (input/output/cache_read/cache_creation), per-model breakdown. Read by `auto_retro` to decide whether to fire reflect-session. |
| [`transcript.txt`](transcript.txt) | none — append-only | Raw timestamped REQ/RES/TOOL stream, written during the session by `SidecarTracer`. Not enriched — useful for debugging. |
| [`session-review.md`](session-review.md) | `hats-session-review/v1` | Per-session retrospective written by the `session-reviewer` role: facts (pure-Python merge), summary + observations (LLM), `hypothesis_verdicts[]`, `proposal_actions[]`, `self_problems[]`. |

See [`docs/ARCHITECTURE.md`](../../../docs/ARCHITECTURE.md#session-lifecycle)
for where these sit in the session flow, and
[`docs/how-to-feedback-loop.md`](../../../docs/how-to-feedback-loop.md)
for the lifecycle of the session-review artifact.
