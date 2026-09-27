# Orchestration — session tags, JSON, exit codes

When you fan out ai-hats sessions via parallel, xargs, CI, or webhook orchestrators, you need to know what each sub-agent is handed, plus tagged metadata, machine-readable output, and stable exit codes. This guide covers all four.

## Which command: `ai-hats agent` vs `ai-hats execute`

For sub-agents and fan-out, reach for **`ai-hats agent <role>`**. The role is required, so you cannot launch a roleless session by accident, and you get `--task`, `--ticket`, `--json`, `-p/--provider`, and friendly role/provider errors out of the box. Every example below uses it.

```bash
# pick the surface per run, without editing ai-hats.yaml
ai-hats agent <role> --task "..." -p <provider>
```

**Runtime role specs.** The role argument in `ai-hats agent "<role-spec>"` accepts expressions for ad-hoc behavioral variants (e.g. `ai-hats agent "maintainer + worker" --task "..."`). Quotes are required when spaces are used. `worker` and `leader` are real traits — but note that a sub-agent is a *batch* primitive with no wake channel, so the paired-session protocol those traits describe (two live sessions sleeping on `ai-hats wait` and waking each other through a card) needs two interactive sessions instead. See [how-to-configure.md](how-to-configure.md#paired-sessions-leader--worker).

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

Both are read off the same materialization plan the runner applies — the
value, not a second assembly — so the report is about the launch that
actually happens. `--dry-run` takes the `--json` and `--materialize`
modifiers.

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

A run the surface could not serve at all — claude not logged in, the codex
CLI missing — is refused **before** a worktree or a session cache is taken and
reports as exit 1 with `error` in the envelope naming what to do (e.g.
`claude auth login`); its `events.jsonl` holds `run_started`, the
`person_must_act` signal and `run_ended`, so an orchestrator reads the cause
the same way it reads a mid-run one. The probe is the surface's readiness
probe (glossary); a probe that cannot run never refuses.

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

## Driving a session over stdin/stdout

`ai-hats headless` runs the same HITL session a bare `ai-hats` does. The role,
its hooks and gates, the log and the finalize are unchanged. Only the channel is
different: instead of a terminal it takes commands on its stdin and writes the
session's log to its stdout. It takes the same parameters as a bare `ai-hats`
(`-p`, `-r`, `-m`, `--tag`, provider flags), and a positional prompt becomes the
first turn. Only claude can be driven this way for now. The design is
[ADR-0038](adr/0038-headless-session-is-a-holder-and-one-log.md).

```bash
# all turns known up front: a file in, a file out, the outcome in $?
ai-hats headless -p claude -r maintainer < turns.ndjson > events.ndjson

# one turn, no input after it
ai-hats headless -r maintainer "summarise README.md" </dev/null > events.ndjson
```

A development venv (`uv pip install -e ".[dev]"`) has no `ai-hats` console
script, since the launcher installs that one. There, run
`python -m ai_hats headless …` with the venv's interpreter.

**stdin** takes one command per line. There are three commands:

```json
{"v":"commands/v1","cmd":"prompt","id":"3f0e2d9c-5b1a-4c2e-9d7f-0a1b2c3d4e5f","text":"read docs/INDEX.md"}
{"v":"commands/v1","cmd":"answer","call_id":"toolu_01XA8…","decision":"deny","message":"not on master"}
{"v":"commands/v1","cmd":"interrupt"}
```

In `prompt`, `id` is optional. It is a UUID in canonical form — lowercase,
hyphenated — that the session has not seen yet. It is how you find this prompt's
turn later. Without it the holder picks one, and so it does for the positional
prompt. `answer` and `interrupt` are described under
[Questions and interrupts](#questions-and-interrupts).

A line the holder cannot run is not dropped silently. It becomes a
`command_rejected` signal in the log, whose `detail` starts with `stdin line N:`,
and the session carries on. A repeated or malformed `id` is refused the same way:
claude would take any string, and it drops a repeated one without a turn, so the
wait for it would never end.

A prompt sent while a turn is still running goes to claude at once, and claude
decides what to do with it. Measured on 2.1.281, one that arrives before a tool
call finishes joins the running turn. One that arrives while the model is
writing waits for the next turn. Closing stdin means "finish what you have and
exit".

From Python, `ai_hats_observe.commands` builds and reads these lines:
`encode_command(Prompt(text, id))`, `encode_command(Answer(call_id, decision))`,
`encode_command(Interrupt())` and `decode_command(line)`.

**stdout** is all machine-readable. Line 1 is the session header:

```json
{"v":"headless/v1","session_id":"…","session_dir":"/abs/…","log":"/abs/…/events.jsonl","holder_pid":4242,"provider_session_id":"…","started_at":"…","events":"events/v1","commands":["prompt","answer","interrupt"]}
```

`events` is the log's format, `commands` the commands this holder runs.

Every line after it is a byte-for-byte copy of the session's `events.jsonl`,
from `run_started` to `run_ended`. The main agent's events come from claude's
stdout alone, so the log is in the order claude said things. Every event of a
turn comes before that turn's `turn_ended`. A prompt's `prompt_received`
(with its `prompt_id`) comes after the previous `turn_ended` whenever claude
started it after that turn.

Every prompt you send has its `prompt_received`, before anything that answers
it. Its `origin` is `person` and its `text` is exactly what you sent. Everything
else claude puts into the conversation is `origin: harness`: a skill's body, a
`<system-reminder>`, a `<task-notification>`, a sub-agent's hand-back, the
summary after `/compact`. Such a prompt has a `prompt_id` of its own that no
`turn_ended` lists, so wait for the ids you sent, never for every id you see.

A slash command is sent as a prompt. Measured on claude 2.1.282 and 2.1.283:

- one claude runs itself (`/model`, `/context`, `/usage`, `/rename`) is answered
  by a response whose `model` is `<synthetic>`;
- one it refuses here (`/tui`, `/login`, `/logout`, `/theme`, `/status`,
  `/upgrade`, `/voice`) is answered "/tui isn't available in this environment.";
- either turn ends with `raw_code: "success"`, where a model turn ends
  `"completed"`;
- any other `/x` goes to the model as an ordinary prompt.

Each turn ends with one `turn_ended`: `ok`, `raw_code`, `detail` and
`prompt_ids`, the ids of the prompts it answered. Find your turn by id: wait for
the `turn_ended` whose `prompt_ids` holds the id you sent. Do not count
`turn_ended` lines:

- a prompt that joined a running turn is answered by that turn, so one line
  lists two ids;
- claude starts turns of its own, for instance when a background task finishes,
  and those list none.

Wait on `turn_ended`, not on `response_ended`: a turn that failed before the
model answered has no `response_ended` at all.

A sub-agent's events come from its own record, as in a terminal session. Nothing
orders them against the main agent's events or against each other.

Signals worth acting on, each said once:

- `approaching_limit` — the quota is close, from claude's `rate_limit_event`;
- `wait` — the turn ran into the quota wall. `retry_after` is the epoch second
  the quota lifts, when claude said it;
- `context_cleared` — `/clear` started the conversation over, and the model
  no longer remembers what came before it. claude goes on under a new session
  id and a new transcript. The session is still one: `metrics.json` names both
  ids in `claude_session_ids`, and the audit, the usage and the sub-agents'
  events cover both sides of the clear;
- `unsupported_record` — a line from claude the holder cannot read or does not
  know yet. The session carries on.

A tool call claude refused without asking, for instance under
`--permission-mode dontAsk`, is a `tool_result_received` with `ok: false`, as in
a terminal session. The turn around it still ends `ok: true`, so read the call's
result, not the turn's.

### Questions and interrupts

The session asks its stdin owner whatever it would ask a person at the terminal:
a role gate's `ask` on a shared-state write, a consent point the role declares,
claude's own permission prompt, the model's request to leave plan mode
(`ExitPlanMode`) and the model's own question (`AskUserQuestion`). Each one is a
`person_asked` line with a `call_id`, and the call waits until you answer it.
Whoever owns stdin gives the consent: they started the session, and the gate's
question is theirs. The permission mode is the one an interactive session gets,
your own settings included: under your `auto` mode claude itself rarely asks,
while a role gate's `ask` still reaches you.

- `answer` with `decision` `allow` runs the call as it was asked, including
  whatever a gate put on it, such as a consent ticket. `deny` refuses it, and
  `message` tells the model why; the log records your refusal as a
  `gate_verdict` with `hook: person`.
- For `AskUserQuestion`, `answers` maps each question's text to your answer.
  The question and its options are in the `tool_call` item with the same
  `call_id`. An `allow` without `answers` leaves the model to ask again in
  words.
- The first answer on a `call_id` wins. A second one, one for a call nobody
  asked about, or one for a question already closed becomes `command_rejected`.
  You may answer as soon as you read `person_asked`: a gate records its question
  before claude sends it to the holder, and the holder keeps your answer until
  then.
- The holder has no timeout of its own. If you will not wait, answer `deny`.
- Closing stdin while a question is open answers it `deny` with "the session is
  ending", so the model stops instead of retrying. That includes a gate's
  question already in the log that claude has not yet sent: the holder waits a
  moment for it before closing claude's stdin.
- What happened is in the log, not in what the model says: the call's
  `tool_result_received` has `ok: false` when it was refused. A model can reply
  "done" to a call that was denied.

`interrupt` stops the running turn and keeps the session. The turn still ends
with its `turn_ended` (`ok: false`), a response it cut is closed `cancelled`,
and a question it had open is closed, so a late answer to it is refused. A tool
it cut is not recorded as a person's refusal, although its result reads the
same, word for word ("The user doesn't want to proceed with this tool use…"). A
person's refusal is the `gate_verdict` with `hook: person`; the cut tool has
the `interrupted` signal beside it. With no turn running, it does
nothing.

**stderr** carries everything meant for a person: the start banner, the
session's four header lines, startup notices, the end summary.

**The end** is stdout reaching EOF: the session is recorded and finalized as a
terminal session is. That includes the session reviewer, which the project's
`feedback.session_retro` policy starts in the background, so its retro may still
be on the way ([how-to-feedback-loop](how-to-feedback-loop.md)). Stopping reading
stdout does not end the session: it runs on and its log on disk stays complete.
Then read the exit code:

| Exit code | Meaning                                                                                                                                                                                                                                                            |
| --------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 0         | stdin closed and every turn ran. A turn that failed is `turn_ended.ok: false`, not the session's exit code.                                                                                                                                                        |
| 130 / 143 | aborted by Ctrl-C / `kill -TERM <holder_pid>`. The log is still closed and the session finalized; a turn claude had not started does not run.                                                                                                                      |
| N         | claude itself exited with N; 128+N when a signal N killed it.                                                                                                                                                                                                      |
| 137       | the holder itself was killed (`kill -9`). Its log has no `run_ended` and the session is not finalized; see below.                                                                                                                                                  |
| 1         | refused before the start, with no header: claude is not logged in (`claude auth login`). The session directory stays; its log is `run_started`, the `reauthenticate` signal and `run_ended`.                                                                       |
| 3         | refused before the start, with no header: a startup gate refused the session.                                                                                                                                                                                      |
| 2         | refused before the start, with no header. The cause is a flag the holder sets itself (`--input-format`, `--output-format`, `--print`, `--permission-prompt-tool`, `--resume`, `--continue`, `--session-id`, `--no-session-persistence`) or a surface with no wire. |

The same loop by hand, from bash:

```bash
coproc H { ai-hats headless -p claude -r maintainer 2>holder.err; }
pid=$H_PID
read -r header <&"${H[0]}"
id=$(uuidgen | tr '[:upper:]' '[:lower:]')
echo "{\"v\":\"commands/v1\",\"cmd\":\"prompt\",\"id\":\"$id\",\"text\":\"read docs/INDEX.md\"}" >&"${H[1]}"
while read -r ev <&"${H[0]}"; do
  case "$(jq -r .event <<<"$ev")" in
    person_asked)
      cid=$(jq -r .call_id <<<"$ev")
      echo "{\"v\":\"commands/v1\",\"cmd\":\"answer\",\"call_id\":\"$cid\",\"decision\":\"allow\"}" >&"${H[1]}" ;;
    turn_ended)
      jq -e --arg id "$id" '.prompt_ids | index($id)' <<<"$ev" >/dev/null && break ;;
  esac
done
exec {H[1]}>&-; cat <&"${H[0]}" >/dev/null; wait "$pid"; echo "exit=$?"
```

From Python, the `ai-hats-client` package is the client. It uses the stdlib only
and does not import `ai_hats`. It offers `HeadlessSession.start`,
`prompt(text)` (returns the id), `turn_for(id)`, `turn(text)`, `next_turn()`,
`answer(call_id, decision)`, `interrupt()`, `close()` and `terminate()`, and
every wait is bounded. A turn wait hands each `person_asked` to its
`on_question` handler. Without a handler it raises `QuestionPending` instead of
waiting out its bound on a question nobody will answer. `ai_hats_client.testing`
is a stand-in `claude` for tests that run with no model.

```python
with HeadlessSession.start(["ai-hats", "headless", "-r", "maintainer"]) as s:
    s.turn("push the branch", on_question=lambda q: s.answer(q["call_id"], "allow"))
    s.close()
```

From the side, `ai-hats session list` shows each session's state, and `--json`
has it as `state`: `live`, `ended`, `dead` (its owner is gone and its finalize
never ran) or `unknown`. `ai-hats session backfill <id>` collects a dead
session's audit and counters from claude's record. It leaves `events.jsonl` as
the run left it, and it does not touch a live session. A claude killed by
SIGKILL also makes the holder exit 137, but that log does end with `run_ended`.
