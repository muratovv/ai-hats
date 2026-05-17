# How-To: feedback loop (`reflect session` + `reflect all`)

Guide to setting up and using the retrospective pipeline. Two flows:

- **`ai-hats reflect session`** — per-session retrospective (auto by default after every session, also runnable by hand for debugging).
- **`ai-hats reflect all`** — manual triage of accumulated hypothesis and proposal backlog.

The session-end retrospective is **a single LLM call** under the `session-reviewer` role. Architectural reference — see [`docs/reflect.md`](reflect.md). Full CLI reference with flags — `ai-hats --tree reflect`. This doc — practical recipes.

> Visual map of the cycle: [Reflection loop in ARCHITECTURE.md](ARCHITECTURE.md#reflection-loop).

---

## Concept minimum

| Entity                  | Where it lives                                          | Schema / role                                      | Who writes                          |
| ----------------------- | ------------------------------------------------------- | -------------------------------------------------- | ----------------------------------- |
| **Session**             | `.gitlog/session_<id>/`                                 | `audit.md` + `metrics.json` + `transcript.txt`     | runtime                             |
| **HYP** (hypothesis)    | `.agent/hypotheses/HYP-NNN.yaml`                        | human-readable YAML                                | human or agent (via CLI)            |
| **PROP** (proposal)     | `.agent/backlog/proposals/PROP-NNN.yaml`                | human-readable YAML                                | `session-reviewer` on self-problem, or human |
| **SessionReview**       | `.agent/retrospectives/sessions/<id>.md`                | `hats-session-review/v1` (one artifact, one call)  | `session-reviewer` role             |
| **Reflect-all handoff** | `.agent/retrospectives/reflect-all/<ts>-handoff.md`     | markdown pointer doc                               | `ai-hats reflect all` pre-flight    |

> Sample artifacts (synthetic but realistic shape):
> [`session-review.md`](../tests/fixtures/real_session/session-review.md) ·
> [`HYP-001-sample.yaml`](../tests/fixtures/real_backlog/HYP-001-sample.yaml) ·
> [`PROP-001-sample.yaml`](../tests/fixtures/real_backlog/PROP-001-sample.yaml).
> The [Backlog state machines](ARCHITECTURE.md#backlog-state-machines) diagram shows the lifecycle of all three.

**Hypothesis** — a YAML with `success_criterion`, `observation_window`, `exit_criteria`. It stays in status `active` until it accumulates enough verdicts in `validation_log` to transition to `confirmed` / `refuted` / `stalled`.

**Verdict** — one entry in a hypothesis's `validation_log`:

| verdict        | meaning                                          |
| -------------- | ------------------------------------------------ |
| `confirmed`    | session produced evidence that the HYP holds     |
| `refuted`      | evidence against the hypothesis                  |
| `inconclusive` | data exists but is mixed / insufficient          |
| `n/a`          | the session physically cannot test the HYP       |

The verdict is written into the HYP file atomically via `ai-hats task hyp append-verdict` (filelock-protected). `n/a` is mirrored only into the SessionReview frontmatter and is not written into the HYP file (to keep the observation window clean).

---

## Quick start

Three minimal recipes for the most common interactions.

### a) Turn the loop on in a fresh project

```yaml
# ai-hats.yaml
feedback:
  session_retro:
    policy: smart
    background: true
```

```bash
ai-hats self bump
```

That's it. After every session that crosses the `smart` threshold (5 turns OR 10 tool calls by default), the `session-reviewer` role runs in the background and writes `.agent/retrospectives/sessions/<id>.md`.

### b) Read what came out of your last session

```bash
ai-hats session list | head -3            # most recent sessions on top
ls .agent/retrospectives/sessions/        # latest review markdowns
```

Open the latest `<id>.md` — `summary`, `observations`, `hypothesis_verdicts[]`, `proposal_actions[]`, `self_problems[]`. Each verdict is one line of evidence the agent gathered. See [`tests/fixtures/real_session/session-review.md`](../tests/fixtures/real_session/session-review.md) for the exact shape.

### c) Clear the backlog after a busy week

```bash
ai-hats reflect all                       # interactive triage chat
# …chat with claude using the printed handoff…
ai-hats reflect commit \                  # bulk-flip statuses
    --accept PROP-3 --reject PROP-12 --defer PROP-15
```

The first command builds a handoff doc and hands you off to a triage chat. The second flips PROP statuses in bulk after you've decided.

---

## Flow 0: policy setup in `ai-hats.yaml`

The `feedback` section controls the whole pipeline:

```yaml
feedback:
  session_retro:
    policy: smart           # off | always | smart | hint
    background: true        # true → run detached in background
    smart_threshold:
      min_turns: 5          # threshold by turn count
      min_tool_calls: 10    # OR by tool-call count
```

### Policies for `session_retro.policy`

| Value    | Behavior on `session_end`                                                                |
| -------- | ---------------------------------------------------------------------------------------- |
| `off`    | nothing happens                                                                          |
| `always` | retro always runs                                                                        |
| `smart`  | retro runs **only if** `turns ≥ min_turns` OR `tool_calls ≥ min_tool_calls`              |
| `hint`   | checks the threshold but instead of running shows a banner "consider running retro manually" |

The smart-threshold condition is **OR**, not AND: crossing either limit is enough.

### Model for the feedback loop

By default ai-hats does not pass `--model` to the provider CLI — the loop inherits the model selected globally in Claude Code / Gemini CLI. If your interactive session runs on Opus, the review runs on Opus too, and cheap telemetry turns expensive.

```yaml
feedback:
  session_retro:
    policy: smart
    review_model: claude-sonnet-4-6   # for the session-reviewer role
```

| Field          | What it affects                                                                   | Where it's plumbed                              |
| -------------- | --------------------------------------------------------------------------------- | ----------------------------------------------- |
| `review_model` | sub-agent for the `session-reviewer` role (summary + observations + verdicts + proposals) | `claude --model <m> --print -p <meta-prompt>` |

Behavior:

- If the field is **unset (`null`)** — the `--model` flag is not passed and the default CLI model is used.
- The old `reflect_model` field is accepted as a deprecated alias (with `DeprecationWarning`); the `model` field (the former LLM-builder) is no longer used and is ignored.
- Supported for both `provider: claude` and `provider: gemini` (the `--model` flag is standard for both CLIs).

After editing — `ai-hats self bump`.

---

## Flow 1: session → auto retrospective

Auto cycle that fires on session end when `policy ∈ {smart, always}`.

> [Reflection loop diagram in ARCHITECTURE.md](ARCHITECTURE.md#reflection-loop) is the visual companion to this section.

### What happens

```
session_end
  └─ runtime → auto_retro.make_decision(policy, metrics)
       │
       ├─ action=skip   → nothing
       ├─ action=hint   → banner for the user
       └─ action=run:
             1) pure-Python compute_facts(project_dir, session_id):
                metrics, files_changed, commits, tasks_closed, links, …
             2) one LLM call as role=session-reviewer (detached background):
                ├─ reads .agent/hypotheses/*.yaml (status=active)
                ├─ reads .agent/backlog/proposals/*.yaml (status=open)
                ├─ reads .gitlog/session_<id>/ (audit + metrics)
                ├─ for EACH active HYP — issues a verdict:
                │     ai-hats task hyp append-verdict --hyp HYP-NNN \
                │         --session <id> --verdict <kind> \
                │         --evidence "<one-line cite>" \
                │         --recommendation <kind>
                ├─ on self-problem — files a meta-proposal:
                │     ai-hats task proposal create \
                │         --category process --target session-reviewer ...
                └─ emits SessionReviewV1 between BEGIN_REFLECT_SESSION_RETRO /
                   END_REFLECT_SESSION_RETRO markers; runner merges with facts
                   and writes .agent/retrospectives/sessions/<id>.md
                   (schema: hats-session-review/v1)
             3) runtime harness check (pure-Python):
                missing / empty / incomplete artifact →
                files ONE meta-proposal (category=process,
                target=session-reviewer, failed_session_id=<id>); deduped per
                session
```

> The narrative output of this flow lives in [`tests/fixtures/real_session/session-review.md`](../tests/fixtures/real_session/session-review.md) — open it side-by-side with the pseudo-code above to see the mapping.

### Session-reviewer contract (what the agent must return)

- `hypothesis_verdicts[]` contains **exactly one entry per active HYP** — no skipping.
- If a hypothesis physically cannot be tested from this session — `verdict: n/a`, and **do not call** `append-verdict` (only mirror into frontmatter).
- Self-problem (the agent didn't understand the HYP, didn't find data) → `ai-hats task proposal create` + `inconclusive` + a reference in `self_problems[]`.
- On `confirmed/refuted/inconclusive` — the agent must call `ai-hats task hyp append-verdict`.

The role is composed of:

- **`review-session`** skill — the orchestrator. Defines the four-step procedure (read evidence → sweep HYPs → triage PROPs → self-meta).
- **`review-hypothesis`** skill — pick verdict + recommendation + persist via `ai-hats task hyp append-verdict`.
- **`review-proposal`** skill — read the open inbox first, vote on similar PROP, or create a novel one.

Sources: `src/ai_hats/libraries/roles/session-reviewer/config.yaml`, `src/ai_hats/libraries/skills/review-session/SKILL.md`, `review-hypothesis/SKILL.md`, `review-proposal/SKILL.md`.

### How the harness validates

Two layers of "no silent failure":

1. **In-skill (LLM-driven):** the `review-session` orchestrator explicitly requires one verdict per active HYP, describes the verdict enum, and forbids silent `n/a`.
2. **Runtime (programmatic):** after the detached process finishes, it reads `.agent/retrospectives/sessions/<id>.md` and parses it as `hats-session-review/v1`. On any issue (missing file, schema fails to parse, not all active HYPs covered) — it writes a meta-PROP with `category=process`, `target=session-reviewer`, `failed_session_id=<id>`. These PROPs surface in `reflect all`.

### Running manually (foreground, for debugging)

```bash
ai-hats reflect session --session <id>                # foreground
ai-hats reflect session --session <id> --background   # same as auto
```

Useful when:

- auto-retro crashed and you want an interactive stack trace;
- you need a retro for a session that didn't cross the `smart` threshold;
- you just enabled LLM mode and are doing a "cold" run on an older session.

---

## Flow 2: `ai-hats reflect all` — manual backlog triage

Once HYPs and PROPs pile up — time to walk the backlog by hand and close / accept / reject in a batch.

### Command lifecycle

```
1. ai-hats reflect all
   ├─ Pre-flight (Python):
   │   collects active HYP + open PROP
   │   writes .agent/retrospectives/reflect-all/<ts>-handoff.md
   │   the handoff contains pointers to:
   │     - HYP-NNN (with a brief validation_log digest)
   │     - PROP-NNN (with rationale)
   │     - hints for ai-hats task hyp/proposal commands
   └─ os.execvp claude <pointer-prompt>
        ↓ you switch into the interactive chat
        in the chat you use:
          ai-hats task hyp show HYP-NNN
          ai-hats task hyp append-verdict ...
          ai-hats task proposal show PROP-NNN
          ai-hats task proposal status PROP-NNN <accepted|rejected|deferred|duplicate>
          ai-hats task create ...   # if you need to spawn a task
2. Once the chat is done — bulk-flip:
   ai-hats reflect commit \
     --accept PROP-3 --accept PROP-7 \
     --reject PROP-12 \
     --defer PROP-15 \
     --duplicate PROP-9
```

### `--dry-run`

```bash
ai-hats reflect all --dry-run
```

Only builds the handoff, does not invoke claude. Useful to:

- see what has accumulated;
- copy the handoff into any editor / another tool;
- check that pre-flight works in CI.

### When to run `reflect all`

- 5+ open PROPs in `.agent/backlog/proposals/` → the per-session loop has started producing noise, time to clear it.
- Retro reminder at session start: "X days without reflect-all, Y skips" — run it.
- Before merging a large change to a role/skill — walk the active HYPs and snapshot their state.
- Routine "once a week" hygiene pass.

### What `reflect all` does NOT do

- **Does not vote on hypotheses automatically** — that's the job of the per-session `session-reviewer`. `reflect all` only displays what has accumulated and helps you make decisions on PROPs / close HYPs.
- **Does not create new HYPs** — for that, see the `hypothesis-workflow` skill (separate flow via `ai-hats task create --tag hypothesis`).

---

## How a hypothesis reaches closure

The full path from creation to `confirmed`/`refuted`:

```
1. Creation (manually or after a session-reviewer self-problem):
   .agent/hypotheses/HYP-042.yaml  status: active
       success_criterion: "..."
       observation_window: "10 sessions"
       exit_criteria.confirm / refute / stalled

2. Verdict accumulation:
   each session → session-reviewer →
     ai-hats task hyp append-verdict --hyp HYP-042 --verdict ... --recommendation ...
   validation_log grows.

3. Triage in reflect all:
   pre-flight handoff shows counters
   (e.g. "8 confirmed, 1 inconclusive, 0 refuted").
   You compare with exit_criteria.confirm.
   In the chat — you close it:
     ai-hats task hyp ... # flip status=confirmed/refuted (see ai-hats task hyp --help)
   OR extend it (recommendation=extend_window).

4. Closure:
   HYP-NNN.yaml: status: confirmed | refuted | stalled
                 closed: 2026-05-05
   drops out of the active list; session-reviewer stops voting on it.
```

Worked example: [`HYP-001-sample.yaml`](../tests/fixtures/real_backlog/HYP-001-sample.yaml) shows a hypothesis with two appended verdicts and the corresponding `exit_criteria` thresholds.

---

## Troubleshooting checklist

| Symptom                                       | Where to look                                                                                                                                                              |
| --------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| auto retro does not start                     | `feedback.session_retro.policy` ≠ `off` and the `smart_threshold` is met                                                                                                   |
| validation_log empty after a session          | run `ai-hats reflect session --session <id>` in foreground — you'll see the stack trace, and the meta-PROP surfaces in `reflect all`                                       |
| meta-PROP with `failed_session_id=...`        | runtime harness caught a broken SessionReview artifact. Open `.agent/retrospectives/sessions/<id>.md`, rerun `ai-hats reflect session --session <id>` in foreground to retry |
| `reflect all` fails with "claude not in PATH" | install Claude Code or use `--dry-run` and work with the handoff in an editor                                                                                              |
| `Overlay: cannot remove ...`                  | unrelated to the feedback loop — see [how-to.md](how-to.md)                                                                                                                |

---

## Related docs

**Architecture & specs**
- [`docs/ARCHITECTURE.md#reflection-loop`](ARCHITECTURE.md#reflection-loop) — visual map of the cycle (auto + manual diagrams).
- [`docs/ARCHITECTURE.md#backlog-state-machines`](ARCHITECTURE.md#backlog-state-machines) — task / HYP / PROP lifecycles.
- [`docs/reflect.md`](reflect.md) — pipeline architecture, schema dispatch, storage layout.

**Sample artifacts**
- [`tests/fixtures/real_session/`](../tests/fixtures/real_session/) — `audit.md`, `metrics.json`, `transcript.txt`, `session-review.md` for one synthetic session.
- [`tests/fixtures/real_backlog/`](../tests/fixtures/real_backlog/) — `HYP-001-sample.yaml`, `PROP-001-sample.yaml` in production shape.

**CLI**
- `ai-hats --tree reflect` — `reflect session` / `reflect all` / `reflect commit` flags.
- `ai-hats --tree task hyp` — hypothesis create / show / append-verdict / set-status.
- `ai-hats --tree task proposal` — proposal create / vote / show / status.

**Configuration**
- [`docs/how-to.md`](how-to.md) — general `ai-hats.yaml` recipes (the `feedback:` section lives alongside the rest of the config).

**Skill internals**
- `src/ai_hats/libraries/skills/review-session/SKILL.md` — orchestrator: four-step session-review procedure.
- `src/ai_hats/libraries/skills/review-hypothesis/SKILL.md` — verdict contract on the per-HYP pass.
- `src/ai_hats/libraries/skills/review-proposal/SKILL.md` — inbox vote / novel-PROP contract.
