# How-To: feedback loop (reflect-session + reflect-all)

Guide to setting up and using the retrospective pipeline. Covers three flows:

- **0. Policy setup** — what to put in `ai-hats.yaml`, when each policy fires.
- **1. Session → reflect-session agent** — auto-retro after a specific session.
- **2. `ai-hats reflect all`** — manual triage of accumulated hypothesis and proposal backlog.

> The commands are `reflect-session` and `reflect-all` (not `review-*`). Full architectural reference — see [`docs/reflect.md`](reflect.md). Full CLI reference with flags — `ai-hats --tree` (subtree: `ai-hats --tree reflect`). Here — practical recipes.

---

## Concept minimum

| Entity                  | Where it lives                                      | Who writes                       |
| ----------------------- | --------------------------------------------------- | -------------------------------- |
| **Session**             | `.gitlog/session_<id>/` (audit, metrics, retro)     | runtime                          |
| **HYP** (hypothesis)    | `.agent/hypotheses/HYP-NNN.yaml`                    | human or agent                   |
| **PROP** (proposal)     | `.agent/backlog/proposals/PROP-NNN.yaml`            | reflect-session on self-problem  |
| **SessionRetro**        | `.agent/retrospectives/sessions/<id>.md`            | builder (LLM)                    |
| **ReflectSession**      | `.agent/retrospectives/reflect-session/<id>.md`     | the `reflect-session` role       |
| **Reflect-all handoff** | `.agent/retrospectives/reflect-all/<ts>-handoff.md` | `ai-hats reflect all`            |

**Hypothesis** — a YAML with `success_criterion`, `observation_window`, `exit_criteria`, `freshness_rule`. It stays in status `active` until it accumulates enough verdicts in `validation_log` to transition to `confirmed` / `refuted` / `stalled`.

**Verdict** — one entry in a hypothesis's `validation_log`:

| verdict        | meaning                                          |
| -------------- | ------------------------------------------------ |
| `confirmed`    | session produced evidence that the HYP holds     |
| `refuted`      | evidence against the hypothesis                  |
| `inconclusive` | data exists but is mixed / insufficient          |
| `n/a`          | the session physically cannot test the HYP      |

The verdict is written into the HYP file atomically via `ai-hats task hyp append-verdict` (filelock-protected). `n/a` is mirrored only into the retro frontmatter and is not written into the HYP file (to keep the observation window clean).

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

The builder always works in LLM mode: immediately after SessionRetroV1, the `reflect-session` role is spawned to vote on active hypotheses.

### Minimal config for a new project

```yaml
feedback:
  session_retro:
    policy: smart
    background: true
```

After editing — `ai-hats self bump`.

### Model for the feedback loop (HATS-232 → HATS-252)

By default ai-hats does not pass `--model` to the provider CLI — the feedback loop inherits the model globally selected in Claude Code / Gemini CLI. If your interactive session runs on Opus, the review runs on Opus too, and cheap telemetry turns expensive.

After HATS-252, post-session reflection makes **a single LLM call** via the `session-reviewer` role. Accordingly, only one field remains:

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

## Flow 1: session → reflect-session

Auto cycle that fires on session end when `policy ∈ {smart, always}`.

### What happens

```
session_end
  └─ runtime → auto_retro.make_decision(policy, metrics)
       │
       ├─ action=skip   → nothing
       ├─ action=hint   → banner for the user
       └─ action=run:
             1) builder LLM writes SessionRetroV1
                → .agent/retrospectives/sessions/<id>.md
             2) reflect-session role is spawned (detached background, claude)
                ├─ reads .agent/hypotheses/*.yaml (status=active)
                ├─ reads .agent/backlog/proposals/*.yaml (status=open)
                ├─ reads .gitlog/session_<id>/ (audit, metrics, retro)
                ├─ for EACH active HYP it issues a verdict:
                │     "$AH" task hyp append-verdict --hyp HYP-NNN --session $SID \
                │            --verdict <kind> --evidence "<...>" \
                │            --recommendation <kind>
                ├─ on self-problem files a PROP:
                │     "$AH" task proposal create --category process --target reflect-session ...
                └─ writes ReflectSessionV1
                   → .agent/retrospectives/reflect-session/<id>.md
             3) runtime safety net: post-validation of the artifact
                if ReflectSessionV1 is broken / missing →
                a meta-PROP is created automatically with failed_session_id=<id>
```

### Reflect-session contract (what the agent must return)

- `hypothesis_verdicts[]` contains **exactly one entry per active HYP** — no skipping.
- If a hypothesis physically cannot be tested from this session — `verdict: n/a`, and **do not call** `append-verdict` (only mirror into frontmatter).
- Self-problem (the agent didn't understand the HYP, didn't find data) → `task proposal create` + `inconclusive` + a reference in `self_problems[]`.
- On `confirmed/refuted/inconclusive` — the agent must call `ai-hats task hyp append-verdict`.

All of this logic lives in the `hypothesis-validation` skill (`libraries/skills/hypothesis-validation/SKILL.md`), which is automatically attached to the `reflect-session` role.

### How the harness validates

Two layers of "no silent failure":

1. **In-skill (LLM-driven):** the skill explicitly requires one verdict per active HYP, describes the enums, and forbids silent `n/a`.
2. **Runtime (programmatic):** after the detached process finishes, it reads `.agent/retrospectives/reflect-session/<id>.md` and parses it as `hats-reflect-session/v1`. On any issue (missing file, schema fails to parse, not all active HYPs covered) — it writes a meta-PROP with `category=process`, `target=reflect-session`, `failed_session_id=<id>`. These PROPs surface in reflect-all.

### Running manually (foreground, for debugging)

```bash
ai-hats reflect session --session <id>            # foreground
ai-hats reflect session --session <id> --background   # same as auto
```

Useful when:

- auto-retro crashed and you want an interactive stack trace;
- you need a retro for a session that didn't cross the `smart` threshold;
- LLM mode was just enabled and you're doing a "cold" run on an older session.

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

### When to run reflect-all

- 5+ open PROPs in `.agent/backlog/proposals/` → reflect-session has started producing noise, time to clear it.
- Retro reminder at session start: "X days without reflect-all, Y skips" — run it.
- Before merging a large change to a role/skill — walk the active HYPs and snapshot their state.
- Routine "once a week" hygiene pass.

### What reflect-all does NOT do

- **Does not vote on hypotheses automatically** — that's the job of reflect-session on a specific session. Reflect-all only displays what has accumulated and helps you make decisions on PROPs / close HYPs.
- **Does not create new HYPs** — for that, see the `hypothesis-workflow` skill (separate flow via `ai-hats task create --tag hypothesis`).

---

## How a hypothesis reaches closure

The full path from creation to `confirmed`/`refuted`:

```
1. Creation (manually or after reflect-session):
   .agent/hypotheses/HYP-042.yaml  status: active
       success_criterion: "..."
       observation_window: "10 sessions"
       exit_criteria.confirm / refute / stalled

2. Verdict accumulation:
   each session → reflect-session →
     ai-hats task hyp append-verdict --hyp HYP-042 --verdict ... --recommendation ...
   validation_log grows.

3. Triage in reflect-all:
   pre-flight handoff shows counters
   (e.g. "8 confirmed, 1 inconclusive, 0 refuted").
   You compare with exit_criteria.confirm.
   In the chat — you close it:
     ai-hats task hyp ... # flip status=confirmed/refuted (see ai-hats task hyp --help)
   OR extend it (recommendation=extend_window).

4. Closure:
   HYP-NNN.yaml: status: confirmed | refuted | stalled
                 closed: 2026-05-05
   drops out of the active list; reflect-session stops voting on it.
```

---

## Troubleshooting checklist

| Symptom                                       | Where to look                                                                                                                                                              |
| --------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| reflect-session does not start                | `feedback.session_retro.policy` ≠ `off` and the `smart_threshold` is met                                                                                                   |
| validation_log empty after a session          | check `ai-hats reflect session --session <id>` in foreground — you'll see the stack trace, and the meta-PROP surfaces in reflect-all                                       |
| meta-PROP with `failed_session_id=...`        | runtime safety net caught a broken artifact. Open `.agent/retrospectives/reflect-session/<id>.md`, rerun `ai-hats reflect session --session <id>` in foreground to retry   |
| reflect-all fails with "claude not in PATH"   | install Claude Code or use `--dry-run` and work with the handoff in an editor                                                                                              |
| `Overlay: cannot remove ...`                  | unrelated to the feedback loop — see [how-to.md](how-to.md)                                                                                                                |

---

## Related docs

- [`docs/reflect.md`](reflect.md) — pipeline architecture, schema table, follow-up tasks.
- [`docs/how-to.md`](how-to.md) — general how-to for `ai-hats.yaml` (roles, overlays, libraries).
- `libraries/skills/hypothesis-workflow/SKILL.md` — how to file new HYPs from reflect-session findings.
- `libraries/skills/hypothesis-validation/SKILL.md` — the reflect-session contract during voting.
