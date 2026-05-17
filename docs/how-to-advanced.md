# How-To: advanced flows (pipeline steps · worktrees · orchestration · CLI integrations)

Umbrella for advanced ai-hats workflows beyond first-time setup. Each section is self-contained — jump to the one you need.

| § | Topic                                                                                                          | Status         |
| - | -------------------------------------------------------------------------------------------------------------- | -------------- |
| 1 | **Custom pipeline steps** — drop a Python step into a project and reference it from a YAML pipeline            | live           |
| 2 | **Worktree workflow** — isolate task work in a linked worktree, parallel sub-agents, merge / discard / recover | live           |
| 3 | **Orchestration** — fan out sessions in parallel / CI, tag metadata, parse JSON, lean on exit codes            | TODO — see [4] |
| 4 | **CLI integrations** — wire external services (Google, GitHub, BQ) as a regular skill                          | TODO — see [5] |

> Full CLI reference — `ai-hats --tree`. First-time setup → [1]. Day-to-day backlog CLI → [2]. Pipeline contract reference (`Step` / `StepIO`) → [3].

---

## 1. Custom pipeline steps

Drop a Python file into `<ai_hats_dir>/pipeline_steps/` and reference it from your YAML pipeline. No `pip install`, no fork of ai-hats — works from a fresh checkout.

This guide walks through a minimal `echo` step end-to-end.

### 1.1 Write the step

Create `<ai_hats_dir>/pipeline_steps/echo.py`:

```python
from ai_hats.pipeline.registry import register
from ai_hats.pipeline.step import Step, StepIO


class EchoStep(Step):
    """Wraps the input ``text`` with a marker into ``echoed``."""

    failure_policy = "halt"  # or "continue" — see §1.4

    def __init__(self, params=None):
        # Pipelines instantiate steps with a ``params`` dict from YAML.
        # Even if your step takes no params, accept the argument to
        # match the loader contract.
        del params

    @property
    def io(self) -> StepIO:
        return StepIO(
            name="echo",                                # YAML id
            requires=frozenset({"text"}),               # inputs (must be in state)
            optional=frozenset(),                       # nice-to-have inputs
            produces=frozenset({"echoed"}),             # outputs (declared exactly)
        )

    def run(self, *, text, **_) -> dict:
        return {"echoed": f"[user-step] {text}"}


register("echo", EchoStep)
```

**StepIO fields** (the contract every step declares):

| Field      | Meaning                                                                                                                  |
| ---------- | ------------------------------------------------------------------------------------------------------------------------ |
| `name`     | YAML id used to reference the step (`- id: echo`).                                                                       |
| `requires` | Keys that **must** be in pipeline state when the step runs. Missing → `BuildError` at validation, before anything fires. |
| `optional` | Keys the step may consume if present; missing ones are silently absent.                                                  |
| `produces` | Keys this step is allowed to emit. Returning anything outside this set raises `StepError` and aborts the pipeline.       |

Full contract — [3].

### 1.2 Reference it from a YAML pipeline

Pipelines live in `<ai_hats_dir>/pipelines/<name>.yaml` (project-level) or are built-in. Either way the syntax is the same:

```yaml
name: echo-pipeline
steps:
  - id: echo
```

That's it — `id: echo` matches the string you passed to `register()`.

### 1.3 Run it

User-facing CLI for `pipeline run` is on the roadmap (HATS-268). Until it lands, drive the harness from Python directly via `run_yaml`:

```python
from pathlib import Path
from ai_hats.pipeline.harness import PipelineHarness

with PipelineHarness("echo-pipeline", Path(".")) as h:
    final = h.run_yaml(
        Path(".agent/ai-hats/pipelines/echo-pipeline.yaml"),
        {"text": "hello"},
    )

print(final["echoed"])
# → [user-step] hello
```

`PipelineHarness.__enter__` triggers user-step loading (calls `load_user_steps` before any YAML is parsed). `run_yaml` then loads the project-local YAML and threads trace wiring through the run.

### 1.4 Failure policy

`failure_policy` controls what happens when `run()` raises:

| Value        | Behaviour                                                                                           |
| ------------ | --------------------------------------------------------------------------------------------------- |
| `"halt"`     | Default. Exception re-raises after trace is emitted; the rest of the pipeline does NOT run.         |
| `"continue"` | Exception is captured into `state["errors"][step_name]`; the pipeline keeps going to the next step. |

Pick `continue` only when downstream steps can cope with the absence of your `produces`. Default to `halt`.

### 1.5 Debugging — trace mode

To see what every step received and produced, enable trace mode:

```bash
AI_HATS_PIPELINE_TRACE=1 python my_runner.py
```

This writes `<ai_hats_dir>/traces/<pipeline>-<timestamp>.jsonl` with one event per step. Inspect with `jq`:

```bash
cat .agent/ai-hats/traces/echo-pipeline-*.jsonl | jq '{step, requires_seen, produces, duration_ms, error}'
```

For full value reprs (truncated at 120 chars), add `AI_HATS_PIPELINE_TRACE_VALUES=1`. By default values are NOT in the trace, so prompt contents and secrets stay off disk.

### 1.6 Conflict with a built-in

If your `register("compose_role", ...)` collides with a built-in name, harness entry raises `StepRegistryError`:

```
ai_hats.pipeline.registry.StepRegistryError: 'step already registered: compose_role'
```

**Pick a different name** — overriding built-ins is intentionally not supported (silent overrides are bad debugging surface).

To see all registered step ids:

```python
from ai_hats.pipeline.registry import names
print(names())
```

### 1.7 `AI_HATS_DIR` override — shared step library

By default the loader looks at `<ai_hats_dir>/pipeline_steps/`. To point ai-hats at a shared library elsewhere:

```bash
export AI_HATS_DIR=/team/shared-ai-hats
# loader now reads /team/shared-ai-hats/pipeline_steps/
```

The override applies to **every** ai-hats artefact (traces, future pipelines), so use it when you want to isolate a whole environment, not just step code.

### 1.8 Conventions

- **Files starting with `_`** (e.g. `_helpers.py`) are skipped by the loader. Use them for shared helpers between user steps.
- **One step per file** is the convention (not enforced); makes it easy to delete or version a single step.
- **Module names are namespaced** under `_ai_hats_user_steps.<stem>` internally — so they don't collide with anything else on `sys.path`.
- **Loaded once per process.** Editing a step file does not hot-reload — restart your invocation to pick up changes.

### 1.9 Security

`<ai_hats_dir>/pipeline_steps/*.py` is **executed by ai-hats** every time a `PipelineHarness` is entered. Same threat model as `<ai_hats_dir>/hooks/` shell scripts: do not commit code from sources you do not trust, and review changes from external contributors as carefully as you would any other code.

### 1.10 See also

- [3] — `Step` / `StepIO` / `Pipeline` contract (ADR-0001).
- [6] — pipeline subsystem CLI design (ADR-0002).
- [7] — e2e test fixture this guide is based on (kept in sync with the snippets above).

---

## 2. Worktree workflow

Git worktrees give each task its own working copy: separate branch, separate filesystem path, master stays clean. ai-hats wraps `git worktree` with five commands that fit the task lifecycle.

### 2.1 When to use a worktree

| Situation                                        | Use a worktree                                                                            |
| ------------------------------------------------ | ----------------------------------------------------------------------------------------- |
| Non-trivial change you want isolated from master | yes — `ai-hats wt create` (or just `task transition <id> execute`, which does it for you) |
| Parallel sub-agents on independent tasks         | yes — each agent gets its own worktree via `ai-hats agent --isolation`                    |
| Risky refactor you might abandon                 | yes — `wt discard` cleans the branch + dir in one shot                                    |
| One-line typo / README fix                       | no — commit on master                                                                     |
| Hotfix that must ship from master                | no — direct commit; the worktree adds bookkeeping that hurts here                         |

### 2.2 Lifecycle

```bash
# Create (or — typically — let `task transition execute` create it for you)
ai-hats wt create feat/HATS-NNN
cd $(git worktree list --porcelain | awk '/^worktree/ {print $2; exit}' | tail -n1)
# (real path is printed by `wt create`; copy it from there)

# Work — commit freely. Master tree is untouched.
git commit -am "..."

# Finish — merge back. Default is --no-ff (preserves history). Use --squash for single-commit history.
cd <project-dir>
ai-hats wt merge feat/HATS-NNN
ai-hats wt merge feat/HATS-NNN --squash      # alternative

# Abandon — drop the branch and the dir.
ai-hats wt discard feat/HATS-NNN
```

`wt merge` and `wt discard` auto-detect the branch when invoked from inside the worktree (no arg). From the main repo, pass the branch explicitly.

### 2.3 Running commands inside the worktree

Always use `ai-hats wt exec` instead of hand-rolled `WT=...; PYTHONPATH=$WT/src` boilerplate — it sets cwd and `PYTHONPATH=src` for you and skips a permission round-trip on every new worktree:

```bash
ai-hats wt exec -- pytest tests/test_foo.py -xvs
ai-hats wt exec -- python -c 'import ai_hats; print(ai_hats.__file__)'
ai-hats wt exec -- ruff check src/
```

For an interactive shell session inside the worktree:

```bash
eval "$(ai-hats wt env)"   # exports $WT and PYTHONPATH
cd $WT
```

### 2.4 Parallel sub-agents

`ai-hats task transition <id> execute` opens a worktree for the task and the agent works there. To delegate a sub-task to a separate agent in its own isolated worktree:

```bash
ai-hats agent sre --task "investigate alert XYZ" --isolation
```

Each agent has its own worktree, branch, and trace dir. Use this when the parent agent needs to keep working on the main task while a side investigation runs in parallel.

Branch naming convention: `<type>/<TICKET-ID>` (e.g. `feat/HATS-200`, `fix/HATS-380`). The `task transition execute` flow picks the branch name automatically from the task ID.

### 2.5 Pitfalls

- **Uncommitted work in a worktree is NOT protected.** A worktree is a filesystem directory; parallel sessions, cleanup hooks, or `git worktree remove --force` can destroy it without warning, and there is **no recovery** for uncommitted changes. Commit at every meaningful checkpoint (every passing test run, every completed sub-task).
- **Don't `cp` skill files manually.** After editing `library/{core,usage}/skills/*/SKILL.md`, run `ai-hats self bump` — it re-copies all skills into `.claude/skills/` and `.agent/skills/`. Hand-copying generates noisy permission entries on every new worktree.
- **Don't create a worktree from inside a worktree.** `ai-hats wt create` from a linked worktree is blocked. Always `cd` back to the main repo first.
- **Don't mix manual `wt create` with `task transition execute` from the main repo.** If you created a worktree by hand and want the task to use it, `cd` into the worktree first, then transition. Otherwise the transition errors out with a clear remediation message.
- **Don't forget to `cd` back to the project dir before merge / discard** — the auto-detect of the active worktree depends on cwd; from the main repo, pass the branch explicitly.

### 2.6 Recovery from a stray worktree

```bash
git worktree list                         # audit what's tracked
git worktree remove <path>                # remove a stray linked worktree
git worktree prune                        # clean stale metadata
rm <ai_hats_dir>/sessions/worktree.json   # if ai-hats state is stale
```

Rule of thumb: one task, one worktree, one `<ai_hats_dir>/sessions/worktree.json` (in the main repo).

### 2.7 See also

- [8] — `worktree-isolation` skill — in-session checklist composed into every role that owns a lifecycle.
- [2] — `task transition execute` and `task close` lifecycle (the most common worktree entry/exit points).

---

## 3. Orchestration

> **TODO** — section not authored yet. Until folded in, the existing standalone doc applies: [4]. Topics it already covers: session tags (`--tag`), JSON output (`--json`), stable exit codes, fanning out via parallel / xargs / CI / webhook orchestrators.

---

## 4. CLI integrations

> **TODO** — section not authored yet. Until folded in, the existing standalone doc applies: [5]. Topics it already covers: wiring external services (Google Workspace, GitHub, BigQuery) as a regular skill that documents a CLI; ai-hats stays secret-agnostic — auth, tokens, and keys are owned by the CLI and the user.

---

## References

**[1]** — [`docs/how-to-configure.md`](how-to-configure.md) — first-time setup, role pick, provider, feedback policy.

**[2]** — [`docs/how-to-backlog.md`](how-to-backlog.md) — day-to-day `ai-hats task` / `task hyp` / `task proposal` recipes.

**[3]** — [`docs/adr/0001-pipelines-as-typed-dataflow.md`](adr/0001-pipelines-as-typed-dataflow.md) — `Step` / `StepIO` / `Pipeline` contract (ADR-0001).

**[4]** — [`docs/how-to-orchestration.md`](how-to-orchestration.md) — orchestration: session tags, JSON, exit codes (will fold into §3).

**[5]** — [`docs/how-to-cli-integrations.md`](how-to-cli-integrations.md) — wiring external services as CLI skills (will fold into §4).

**[6]** — [`docs/adr/0002-pipeline-subsystem-cli.md`](adr/0002-pipeline-subsystem-cli.md) — built-in step inventory and CLI design (ADR-0002).

**[7]** — [`tests/test_user_steps.py`](../tests/test_user_steps.py) — `test_step_runs_in_pipeline_e2e` is the e2e fixture this guide tracks.

**[8]** — [`library/core/skills/worktree-isolation/SKILL.md`](../library/core/skills/worktree-isolation/SKILL.md) — in-session skill for isolated work.
