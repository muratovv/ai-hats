# Orchestration — session tags, JSON, exit codes

When you fan out ai-hats sessions via parallel, xargs, CI, or webhook orchestrators, you need to know what each sub-agent is handed, plus tagged metadata, machine-readable output, and stable exit codes. This guide covers all four.

## Which command: `ai-hats agent` vs `ai-hats execute`

For sub-agents and fan-out, reach for **`ai-hats agent <role>`**. The role is required, so you cannot launch a roleless session by accident, and you get `--task`, `--ticket`, `--json`, `-p/--provider`, and friendly role/provider errors out of the box. Every example below uses it.

```bash
# pick the surface per run, without editing ai-hats.yaml
ai-hats agent <role> --task "..." -p <provider>
```

**Runtime role specs.** The role argument in `ai-hats agent "<role-spec>"` accepts expressions for ad-hoc behavioral variants (e.g. `ai-hats agent "maintainer + worker" --task "..."`). Quotes are required when spaces are used. `worker` and `leader` are real traits (HATS-1491) — but note that a sub-agent is a *batch* primitive with no wake channel, so the paired-session protocol those traits describe (two live sessions sleeping on `ai-hats wait` and waking each other through a card) needs two interactive sessions instead. See [how-to-configure.md](how-to-configure.md#paired-sessions-leader--worker).

`ai-hats execute` is the **low-level primitive** behind it — a dual-mode launcher (`--interactive`, the default, is the same path as bare `ai-hats`; `--batch` is the same path as `ai-hats agent`). Both run the same pipeline through the same wiring, so reach for `execute` only for the knobs the wrapper still does not expose — chiefly an initial-injection prompt resolved by name:

```bash
# a role is still REQUIRED for --batch
ai-hats execute --role <role> --batch --prompt <injection-name>
```

`execute --batch` without `-r/--role` is a usage error (it would build the invalid worktree branch `agent//<sid>`); the CLI redirects you to `ai-hats agent <role>`.

## What a sub-agent is handed

Two channels, and they answer different questions. The **role composition** —
rules, skills, injections — says *who the agent is*, and is the same for every
spawn of that role. The **first turn** says *what this run is about*, and is
built per task:

| Section            | Filled from                    | Present when    |
| ------------------ | ------------------------------ | --------------- |
| `# TICKET_CONTEXT` | the card's raw `task.yaml`     | `--ticket <id>` |
| `# LINKED_CONTEXT` | the cards that ticket links to | `--ticket <id>` |
| `# TASK`           | your `--task` string           | `--task "..."`  |

Empty sections are omitted, so a `--task`-only spawn gets exactly one heading.

**`--ticket` is not just the card.** It pulls the ticket's **direct** links —
one level, no transitive walk — in the order `parent_task → depends_on →
related → see_also`. Each linked card arrives trimmed (id, title, state,
description) with **only its latest** `work_log` entry. The one exception is
the parent epic, which carries its whole `plan.md`, **uncapped**: nothing in
this path truncates. A 900-line epic plan is 900 lines in every child's
prompt, so if fan-out feels expensive, look there first.

Inspect the real bytes before spending a run on them:

```bash
# what this spawn WOULD send — no session, no worktree, no ownership hold
ai-hats agent <role> --task "..." --ticket HATS-123 --dry-run

# what a spawn DID send
cat .agent/ai-hats/sessions/runs/session_<id>/meta_prompt.txt
```

Both come from the same assembly the runner uses, so the report is about the
launch that actually happened (HATS-1552).

**Two engines, one contract.** On claude the turn runs in-process through the
Agent SDK and ends when the SDK reports the turn complete; on the CLI surfaces
(agy, cline, codex, opencode) it is a real subprocess whose stdout is
JSON-parsed on a best-effort basis. You do not choose, and you do not need to
care: both land the same artifacts under `session_<id>/` — `transcript.txt`,
`reasoning.log`, `metrics.json`, `audit.md` — and the same exit codes
([below](#machine-readable-run)).

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

## Waiting for an event

`ai-hats wait` blocks until something happens, then returns — one call, same
session, no model tokens burned while it waits. Wait on a backlog card's state,
or on any shell predicate:

```bash
# a card reaches a state
ai-hats wait --task HATS-123 --until review

# ...or any of several states — repeat the flag, they OR together
ai-hats wait --task HATS-123 --until execute --until done

# an arbitrary predicate: exit 0 = happened, 1 = not yet, >1 = broken
ai-hats wait --until-cmd 'test -f /tmp/build.done' --poll 10 --timeout 900
```

Read the exit code — the three outcomes are the reason to prefer this over a
hand-rolled `until` loop:

| Exit code | Meaning                                                                                                                         |
| --------- | ------------------------------------------------------------------------------------------------------------------------------- |
| 0         | the event happened; a summary line with the timestamp goes to stdout                                                            |
| 124       | `--timeout` elapsed first — GNU coreutils convention, as above                                                                  |
| 2         | the predicate itself is broken (bad shell, unknown card, no project), or a bad flag value (`--poll <= 0`, negative `--timeout`) |

That last row is the point. A shell `until` loop treats every non-zero probe as
"not yet", so a typo'd predicate waits forever and the silence is
indistinguishable from a successful wait. `wait` refuses to keep waiting on a
predicate that cannot answer.

`--timeout 0` (the default) waits indefinitely. On Claude Code, run the command
in the **background** rather than the foreground: the harness wakes you with a
single notification when it exits, so the wait costs one message instead of
growing your context for its whole duration — and the foreground Bash tool caps
out at 600 s anyway.

When two agents hand work back and forth through one card, name **every** state
that should wake you. A worker parked on `--until execute` (expecting rework)
never wakes when the leader accepts the card straight to `done`.
