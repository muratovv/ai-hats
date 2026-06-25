# Orchestration — session tags, JSON, exit codes

When you fan out ai-hats sessions via parallel, xargs, CI, or webhook orchestrators, you need tagged metadata, machine-readable output, and stable exit codes. This guide covers all three.

## Which command: `ai-hats agent` vs `ai-hats execute`

For sub-agents and fan-out, reach for **`ai-hats agent <role>`**. The role is required, so you cannot launch a roleless session by accident, and you get `--task`, `--ticket`, `--json`, and friendly role errors out of the box. Every example below uses it.

`ai-hats execute` is the **low-level primitive** behind it — a dual-mode launcher (`--interactive`, the default, is the same path as bare `ai-hats`; `--batch` is the same path as `ai-hats agent`). Use it directly only for knobs the wrapper does not expose, such as a provider override or an initial-injection prompt resolved by name:

```bash
# power case: provider override + initial-injection prompt by name
# (knobs `agent` does not expose) — a role is still REQUIRED for --batch
ai-hats execute --role <role> --batch --provider <provider> --prompt <injection-name>
```

`execute --batch` without `-r/--role` is a usage error (it would build the invalid worktree branch `agent//<sid>`); the CLI redirects you to `ai-hats agent <role>`.

## Session tags & queryable history

Custom `k=v` metadata on sessions — for orchestrators (autosre, CI, batch),
cost attribution, pipeline tracking, A/B experiments. Tags land in
`metrics.json` under the `tags` key and are indexed via `session list`.

```bash
# Write — tags at launch time (repeatable flag, up to 20 per session)
ai-hats agent sre-diagnoser --task "..." \
    --tag alert_fp=abc123 \
    --tag alertname=ImmichContainerDown \
    --tag client=home-lab

# Same for an interactive session
ai-hats --tag client=acme --tag project=migration-v2

# Query — filters + machine-readable JSON for piping into jq/parallel
ai-hats session list --tag alert_fp=abc123 --json | jq .
ai-hats session list --role sre-diagnoser --since 2026-04-20 --json
ai-hats session list --tag client=acme --tag project=X --all --json
```

**Validation (strict, raises on violation):**

- Key: `^[a-zA-Z_][a-zA-Z0-9_.\-]*$`, max 64 chars.
- Value: max 256 chars, non-empty.
- Max 20 tags per session.
- Reserved keys (shadowing forbidden): `role`, `provider`, `exit_code`, `model`,
  `timed_out`, `error`, `isolation_mode`, `turns`, `tokens`, `models`,
  `tool_calls`, `session_id`, `session_dir`, `started_at`.

**JSON output** — `--json` emits a plain list of dicts. The shape of each
item is all `metrics.json` fields plus computed `session_id`, `session_dir`,
`started_at` (ISO-8601). Consumers pick what they need via `jq`.

**Dedup recipe for the orchestrator** (replaces the idea of an `--idempotency-key`):

```bash
# Before kicking off a new diagnosis — check whether a session with this fp already exists
fp="$1"
existing=$(ai-hats session list --tag alert_fp="$fp" --since "$(date -u +%Y-%m-%d)" --all --json \
            | jq -r '.[] | select(.exit_code == 0) | .session_id' | head -n1)

if [ -n "$existing" ]; then
    echo "Already diagnosed in session $existing — skipping"
    exit 0
fi
ai-hats agent sre-diagnoser --tag alert_fp="$fp" --task "..."
```

Atomicity of check-and-spawn (a race between two parallel webhooks) is the
orchestrator's responsibility: filelock / redis / whatever suits.

## Machine-readable run

For fan-out via `parallel`/`xargs`/CI:

```bash
ai-hats agent <role> --task "..." --json
# → stdout: {"session_id":"...","exit_code":0,"role":"...","duration_s":12.3,"tags":{...},...}
```

The shape matches an element of `session list --json` — same parsing on
the orchestrator side. `--json` mode **fully suppresses** the rich summary
in stdout; the human-readable mode (without `--json`) is unchanged.

**Exit codes** (stable contract, propagated from the sub-agent):

| Code           | Meaning                                                                      |
| -------------- | ---------------------------------------------------------------------------- |
| 0              | success (sub-agent exited 0)                                                 |
| 1              | agent/runtime error (subprocess exit 1, generic exception in runtime)        |
| 2              | CLI usage error (bad flags — Click default)                                  |
| 124            | timeout (sub-agent exceeded the wall-clock limit) — GNU coreutils convention |
| other non-zero | forwarded from the provider (claude/gemini exit code)                        |

Fan-out example:

```bash
# N parallel calls, collect all results, filter the successful ones
cat tasks.jsonl | jq -r '.task' | parallel -j 3 \
    'ai-hats agent diagnoser --task {} --json' \
  | jq -s 'map(select(.exit_code == 0))'
```

If you need the exit code of a single session, you don't need to parse stdout — `$?` is enough:

```bash
ai-hats agent diagnoser --task "..." --json > result.json
echo "exit=$?"   # matches .exit_code in result.json
```
