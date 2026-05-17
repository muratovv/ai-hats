# How-To: feedback loop (`reflect session` + `reflect all`)

Guide to setting up and using the retrospective pipeline. Two flows:

- **`ai-hats reflect session`** — per-session retrospective (auto by default after every session, or runnable by hand). Votes on every active [hypothesis][3] it can test and, on self-problem, files a [proposal][3] for the maintainer.
- **`ai-hats reflect all`** — manual triage of the accumulated [HYP][3] / [PROP][3] backlog.

Filing a new HYP yourself (a hunch you want the loop to track) is a third, **session-driven** flow — `ai-hats task hyp create` while the symptom is fresh in your audit. See [Quick start (d)](#d-file-a-new-hypothesis-from-a-session) below for a worked example.

The session-end retrospective is **a single LLM call** under the `session-reviewer` role. Architectural reference — see [1]. Full CLI reference with flags — `ai-hats --tree reflect`. This doc — practical recipes.

> Visual map of the cycle: [Reflection loop][2].

---

## Concept minimum

| Entity                  | Where it lives                                      | Schema / role                                     | Who writes                                   |
| ----------------------- | --------------------------------------------------- | ------------------------------------------------- | -------------------------------------------- |
| **Session**             | `.gitlog/session_<id>/`                             | `audit.md` + `metrics.json` + `transcript.txt`    | runtime — see [Session lifecycle][11]        |
| **HYP** (hypothesis)    | `.agent/hypotheses/HYP-NNN.yaml`                    | human-readable YAML                               | human or agent (via CLI)                     |
| **PROP** (proposal)     | `.agent/backlog/proposals/PROP-NNN.yaml`            | human-readable YAML                               | `session-reviewer` on self-problem, or human |
| **SessionReview**       | `.agent/retrospectives/sessions/<id>.md`            | `hats-session-review/v1` (one artifact, one call) | `session-reviewer` role                      |
| **Reflect-all handoff** | `.agent/retrospectives/reflect-all/<ts>-handoff.md` | markdown pointer doc                              | `ai-hats reflect all` pre-flight             |

> Sample artifacts (synthetic but realistic shape): [4], [5], [6]. The [Backlog state machines][3] diagram shows the lifecycle of all three.

**Hypothesis** — a YAML with `success_criterion`, `observation_window`, `exit_criteria`. It stays in status `active` until it accumulates enough verdicts in `validation_log` to transition to `confirmed` / `refuted` / `stalled`.

**Verdict** — one entry in a hypothesis's `validation_log`:

| verdict        | meaning                                      |
| -------------- | -------------------------------------------- |
| `confirmed`    | session produced evidence that the HYP holds |
| `refuted`      | evidence against the hypothesis              |
| `inconclusive` | data exists but is mixed / insufficient      |
| `n/a`          | the session physically cannot test the HYP   |

The verdict is written into the HYP file atomically via `ai-hats task hyp append-verdict` (filelock-protected). `n/a` is mirrored only into the SessionReview frontmatter and is not written into the HYP file (to keep the observation window clean).

---

## Quick start

Four minimal recipes for the most common interactions.

### a) Turn the loop on in a fresh project

```yaml
# ai-hats.yaml — see Flow 0 below for the full field reference
feedback:
  session_retro:
    policy: smart
    background: true
```

```bash
ai-hats self bump
```

That's it. After every session that crosses the smart threshold (5 turns OR 10 tool calls by default — see [Policy setup](#flow-0-policy-setup-in-ai-hatsyaml) for tuning), the `session-reviewer` role runs in the background and writes `.agent/retrospectives/sessions/<id>.md`.

### b) Read what came out of your last session

```bash
ai-hats session list | head -3            # most recent sessions on top
ls .agent/retrospectives/sessions/        # latest review markdowns
```

Open the latest `<id>.md` — `summary`, `observations`, `hypothesis_verdicts[]`, `proposal_actions[]`, `self_problems[]`. Each verdict is one line of evidence the agent gathered. See [4] for the exact shape.

### c) Clear the backlog after a busy week

```bash
ai-hats reflect all                       # interactive triage chat
# …chat with ai-hats using the printed handoff…
ai-hats reflect commit \                  # bulk-flip statuses
    --accept PROP-3 --reject PROP-12 --defer PROP-15
```

The first command builds a handoff doc and hands you off to a triage chat. The second flips PROP statuses in bulk after you've decided.

### d) File a new hypothesis from a session

You noticed during a session that the agent keeps stepping on the same rake. You want the loop to **test the hunch** that fixing X will reduce the rake. File a HYP:

```bash
ai-hats task hyp create \
    --title "Filters break under sub-agent refactors" \
    --hypothesis "Every regression in observe.py filters in the past \
                  month followed a SidecarTracer refactor. A targeted \
                  test gate on observe.py edits should catch the next one." \
    --source-task HATS-029 \
    --observation-window "4 sessions" \
    --success-criterion "zero new filter regressions in the window"
```

The card lands at `.agent/hypotheses/HYP-NNN.yaml` with `status: active`. From that moment, every subsequent `session-reviewer` run votes on it; you close it via `ai-hats reflect all` (Recipe c) once the window closes.

A complete example of what an active HYP looks like — including how its `validation_log` grows — is in [5]. Full CLI flags — `ai-hats --tree task hyp` or `ai-hats task hyp create --help`.

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

| Value    | Behavior on `session_end`                                                                    |
| -------- | -------------------------------------------------------------------------------------------- |
| `off`    | nothing happens                                                                              |
| `always` | retro always runs                                                                            |
| `smart`  | retro runs **only if** `turns ≥ min_turns` OR `tool_calls ≥ min_tool_calls`                  |
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

| Field          | What it affects                                                                           | Where it's plumbed                            |
| -------------- | ----------------------------------------------------------------------------------------- | --------------------------------------------- |
| `review_model` | sub-agent for the `session-reviewer` role (summary + observations + verdicts + proposals) | `claude --model <m> --print -p <meta-prompt>` |

Behavior:

- If the field is **unset (`null`)** — the `--model` flag is not passed and the default CLI model is used.
- The old `reflect_model` field is accepted as a deprecated alias (with `DeprecationWarning`); the `model` field (the former LLM-builder) is no longer used and is ignored.
- Supported for both `provider: claude` and `provider: gemini` (the `--model` flag is standard for both CLIs).

After editing — `ai-hats self bump`.

---

## Flow 1: session → auto retrospective

Auto cycle that fires on session end when `policy ∈ {smart, always}`.

<p align="center">
  <img src="assets/diagrams/auto-reflect-session.svg" alt="Auto reflect-session flow" width="520">
</p>

In words:

1. **`auto_retro.make_decision`** examines the just-finished session's metrics and the configured policy. The outcome is `skip` (do nothing), `hint` (banner to the user), or `run`.
2. On `run`, **pure-Python `compute_facts`** assembles the factual layer — metrics, files changed, commits, tasks closed — without an LLM.
3. The **session-reviewer LLM** is spawned (detached background by default). It reads the facts plus the audit and metrics from the session run dir, then for every active HYP issues a verdict via `ai-hats task hyp append-verdict`. On a self-problem it files a meta-proposal via `ai-hats task proposal create --category process --target session-reviewer`.
4. The runner merges facts with the LLM's output and writes one SessionReviewV1 markdown at `.agent/retrospectives/sessions/<id>.md` (schema `hats-session-review/v1`).
5. A **pure-Python harness check** parses the artifact afterwards. If it is missing, unparseable, or doesn't cover every active HYP, a single meta-PROP (`target=session-reviewer`, `failed_session_id=<id>`) is filed and surfaces in `reflect all`.

> Side-by-side companion: open [4] next to the steps above to see how each frontmatter field maps to a step.

### Session-reviewer contract (what the agent must return)

- `hypothesis_verdicts[]` contains **exactly one entry per active HYP** — no skipping.
- If a hypothesis physically cannot be tested from this session — `verdict: n/a`, and **do not call** `append-verdict` (only mirror into frontmatter).
- Self-problem (the agent didn't understand the HYP, didn't find data) → `ai-hats task proposal create` + `inconclusive` + a reference in `self_problems[]`.
- On `confirmed/refuted/inconclusive` — the agent must call `ai-hats task hyp append-verdict`.

The role is composed of:

- **`review-session`** skill [8] — the orchestrator. Defines the four-step procedure (read evidence → sweep HYPs → triage PROPs → self-meta).
- **`review-hypothesis`** skill [9] — pick verdict + recommendation + persist via `ai-hats task hyp append-verdict`.
- **`review-proposal`** skill [10] — read the open inbox first, vote on similar PROP, or create a novel one.

Role config: [7].

### How the harness validates

Two layers of "no silent failure":

1. **In-skill (LLM-driven):** the `review-session` orchestrator [8] explicitly requires one verdict per active HYP, describes the verdict enum, and forbids silent `n/a`.
2. **Runtime (programmatic):** after the detached process finishes, it reads `.agent/retrospectives/sessions/<id>.md` and parses it as `hats-session-review/v1`. On any issue (missing file, schema fails to parse, not all active HYPs covered) — it writes a meta-PROP with `category=process`, `target=session-reviewer`, `failed_session_id=<id>`. These PROPs surface in `reflect all`.

### Running manually (foreground, for debugging)

A "session" is one invocation of `ai-hats` or `ai-hats agent <role>` — its trace dir lives in `.gitlog/session_<id>/`. See [11] for what runtime writes during one and how `<id>` is generated.

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

```bash
# 1. Pre-flight + handoff to interactive chat
ai-hats reflect all
# - collects active HYPs + open PROPs from .agent/
# - writes .agent/retrospectives/reflect-all/<ts>-handoff.md
# - then os.execvp's into claude with a pointer prompt

# 2. Inside the chat — inspect and decide, with these CLI handles
ai-hats task hyp show HYP-NNN                                    # full validation_log
ai-hats task hyp append-verdict --hyp HYP-NNN ...                # add evidence
ai-hats task proposal show PROP-NNN                              # rationale + votes
ai-hats task proposal status PROP-NNN <accepted|rejected|deferred|duplicate>
ai-hats task create ...                                          # spawn a task from a PROP

# 3. Once the chat is done — bulk-flip PROP statuses in one command
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

Only builds the handoff, does not invoke an interactive chat. Useful to:

- see what has accumulated without committing to a triage session;
- copy the handoff into another tool (editor, search, paste into a colleague's message);
- check that pre-flight works in CI (no Claude in PATH, no auth);
- prepare offline before flying / before a long meeting.

### When to run `reflect all`

- 5+ open PROPs in `.agent/backlog/proposals/` → the per-session loop has started producing noise, time to clear it.
- Retro reminder at session start: "X days without reflect-all, Y skips" — run it.
- Before merging a large change to a role/skill — walk the active HYPs and snapshot their state.
- Routine "once a week" hygiene pass.

### What `reflect all` does NOT do

- **Does not vote on hypotheses automatically** — that's the job of the per-session `session-reviewer`. `reflect all` only displays what has accumulated and helps you make decisions on PROPs / close HYPs.
- **Does not create new HYPs** — for that, see [Quick start (d)](#d-file-a-new-hypothesis-from-a-session) above (use `ai-hats task hyp create` while a symptom is fresh).

---

## How a hypothesis reaches closure

<p align="center">
  <img src="assets/diagrams/hypothesis-closure-flow.svg" alt="Hypothesis closure flow" width="420">
</p>

1. **Create.** Either by hand or as the follow-up to a `session-reviewer` self-problem. The card carries `success_criterion`, `observation_window`, and `exit_criteria`. Status starts at `active`.
2. **Accumulate verdicts.** Every subsequent session triggers `session-reviewer`, which appends one entry to `validation_log` per applicable HYP.
3. **Triage.** During `reflect all`, the pre-flight handoff shows counters per HYP (e.g. "8 confirmed, 1 inconclusive, 0 refuted"). You compare against the HYP's `exit_criteria` and either close it or extend the observation window.
4. **Close.** Status flips to `confirmed` / `refuted` / `stalled`; `closed: YYYY-MM-DD` is set; the HYP drops out of the active list and `session-reviewer` stops voting on it.

Worked example: [5] shows a hypothesis after two appended verdicts and the `exit_criteria` thresholds that govern its closure.

---

## Troubleshooting checklist

| Symptom                                       | Where to look                                                                                                                                                                |
| --------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| auto retro does not start                     | `feedback.session_retro.policy` ≠ `off` and the `smart_threshold` is met                                                                                                     |
| validation_log empty after a session          | run `ai-hats reflect session --session <id>` in foreground — you'll see the stack trace, and the meta-PROP surfaces in `reflect all`                                         |
| meta-PROP with `failed_session_id=...`        | runtime harness caught a broken SessionReview artifact. Open `.agent/retrospectives/sessions/<id>.md`, rerun `ai-hats reflect session --session <id>` in foreground to retry |
| `reflect all` fails with "claude not in PATH" | install Claude Code or use `--dry-run` and work with the handoff in an editor                                                                                                |
| `Overlay: cannot remove ...`                  | unrelated to the feedback loop — see [12]                                                                                                                                    |

---

## References

[1] [`docs/reflect.md`](reflect.md) — pipeline architecture, schema dispatch, storage layout.
[2] [`docs/ARCHITECTURE.md#reflection-loop`](ARCHITECTURE.md#reflection-loop) — visual map of the cycle.
[3] [`docs/ARCHITECTURE.md#backlog-state-machines`](ARCHITECTURE.md#backlog-state-machines) — task / HYP / PROP lifecycles.
[4] [`tests/fixtures/real_session/session-review.md`](../tests/fixtures/real_session/session-review.md) — synthetic `hats-session-review/v1` artifact.
[5] [`tests/fixtures/real_backlog/HYP-001-sample.yaml`](../tests/fixtures/real_backlog/HYP-001-sample.yaml) — synthetic hypothesis with `validation_log`.
[6] [`tests/fixtures/real_backlog/PROP-001-sample.yaml`](../tests/fixtures/real_backlog/PROP-001-sample.yaml) — synthetic proposal with `votes[]`.
[7] `src/ai_hats/libraries/roles/session-reviewer/config.yaml` — role composition.
[8] `src/ai_hats/libraries/skills/review-session/SKILL.md` — orchestrator: four-step session-review procedure.
[9] `src/ai_hats/libraries/skills/review-hypothesis/SKILL.md` — verdict contract on the per-HYP pass.
[10] `src/ai_hats/libraries/skills/review-proposal/SKILL.md` — inbox vote / novel-PROP contract.
[11] [`docs/ARCHITECTURE.md#session-lifecycle`](ARCHITECTURE.md#session-lifecycle) — what runtime writes during a session, where `<id>` comes from.
[12] [`docs/how-to.md`](how-to.md) — general `ai-hats.yaml` recipes.

**CLI (always live, no static reference)** — `ai-hats --tree reflect`, `ai-hats --tree task hyp`, `ai-hats --tree task proposal`.
