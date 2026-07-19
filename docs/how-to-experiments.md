# Behavior experiments — prove a component edit changes agent behavior

Editing a skill, rule, or trait and eyeballing the next session is not evidence.
A behavior experiment runs N scripted subagent sessions per **arm** (component
variant), scores each run mechanically, and reports per-arm success rates — so a
wording change is either measurably better or it isn't. Terms — see [1].

```
experiment = 1 scenario × 2 arms × N identical runs
                                   ↑ measures frequency of the target behavior
```

Runs are identical on purpose: agent behavior is stochastic, so N repeats of the
same seeded scenario estimate the *rate* of the behavior under each variant.
Different trials belong to different experiments.

## Anatomy of an experiment

```
experiments/
  _lib/                  # shared runner scripts (this guide)
  <name>/
    arms/<arm>/          # component variant as a library dir (skills/, roles/, ...)
    scenario/
      lib/roles/exp-agent/config.yaml   # the role under test (name is a contract)
      seed.sh            # seeds the sandbox backlog; receives the sandbox path
      task.txt           # the task prompt given to the agent
    score/               # this experiment's mechanical checks (executables)
    runs/                # collected material — gitignored, never commit
```

- **Arms** are plain library directories wired into the sandbox via
  `library_paths`. Resolution is last-wins, so an arm that ships
  `skills/hatrack/SKILL.md` overrides the built-in skill of the same name — see [2].
- **`exp-agent`** is the required role name: `prepare` composes the sandbox with
  `-r exp-agent`, and `run` launches `ai-hats agent exp-agent`.
- **`seed.sh`** gets the sandbox path as `$1` and builds the backlog state the
  scenario needs (e.g. `rack create` + transitions). It runs with the ambient
  session env scrubbed.
- **`score/`** holds executables, each invoked as `<script> <run-dir>`; exit 0
  is a pass, and a run is a success only when every script passes. What to score
  is the experiment author's decision — the infra only collects the material.

## Run an experiment

```bash
# N runs of each arm on a pinned model (sequential; fresh sandbox per run)
experiments/_lib/run.sh experiments/smoke a 5 claude-haiku-4-5
experiments/_lib/run.sh experiments/smoke b 5 claude-haiku-4-5

# per-arm success/fail/timeout/crash counters + rate, markdown on stdout
experiments/_lib/report.sh experiments/smoke

# tear down the tmp sandboxes (idempotent)
experiments/_lib/clean.sh experiments/smoke
```

`experiments/smoke/` is the trivial experiment used to verify the infra itself —
copy it as a starting point. The first real consumer is
`experiments/hatrack-advance-to-review/` (HATS-1053/HATS-1051).

## What gets collected per run

`runs/<arm>/run-<i>/` after a run:

| Artifact | What it is |
| --- | --- |
| `envelope.json` | The `ai-hats agent --json` envelope: exit code, cost, tags, composition snapshot |
| `status.json` | Runner verdict: exit code, duration, `timed_out` |
| `sessions/` | Session dirs (`metrics.json`, `transcript.txt`, `audit.md`) from the sandbox |
| `backlog/` | Final sandbox backlog state — the primary scoring signal (fs-as-truth) |
| `provider-jsonl/` | Raw provider JSONL (tool-call source of truth), located by `claude_session_id` |
| `ai-hats.yaml` + `arm-manifest.txt` | Arm identity proof: the config that wired the arm in + sha256 of the arm dir |

Timeouts and crashes stay in the report as their own statuses — a failed run is
never silently dropped from the sample.

## Environment knobs

| Variable | Default | Purpose |
| --- | --- | --- |
| `AI_HATS_EXP_TMP` | `$TMPDIR/ai-hats-exp` | Base dir for sandboxes |
| `AI_HATS_EXP_VENV` | ambient `AI_HATS_VENV` | Venv reused by sandboxes (skips per-sandbox pip install) |
| `AI_HATS_EXP_RUN_TIMEOUT` | `600` | Per-run cap in seconds; exceeding it records a `timeout` status |

## Caveats

- **Privacy.** `runs/` carries raw session recordings (JSONL, transcripts) —
  personal data. It is gitignored; never commit it. Commit only the report.
- **Ambient env is scrubbed.** `prepare`/`run` strip `AI_HATS_*` session pins and
  `GIT_*` plumbing before touching the sandbox — without this, sessions land in
  the parent project and transitions hit the wrong tracker.
- **Headless permissions.** Sandboxes pre-approve `rack` / `ai-hats` /
  `ai-hats-rack` in `.claude/settings.json`; an agent hitting a permission
  prompt on any other CLI will stall — extend the allowlist in
  `_lib/prepare.sh` if a scenario needs more.
- **Compare within one window.** Models drift; arms are comparable inside one
  experiment run, not across weeks.
- **Residue.** The provider keeps its own per-sandbox project dirs under
  `~/.claude/projects/`; the infra does not clean those.

## References

**[1]** — [`docs/glossary.md#behavior-experiment-ab`](glossary.md#behavior-experiment-ab) — term definitions (experiment, arm, scenario, score, runs).

**[2]** — [`docs/how-to-extend.md`](how-to-extend.md) — library layout and last-wins override precedence.

**[3]** — [`docs/how-to-orchestration.md`](how-to-orchestration.md) — the `ai-hats agent --json` / `--tag` surface the runner is built on.
