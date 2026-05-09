# How to add a custom pipeline step

Drop a Python file into `<project>/.agent/ai-hats/pipeline_steps/` and reference it from your YAML pipeline. No `pip install`, no fork of ai-hats — works from a fresh checkout.

This guide walks you through a minimal `echo` step end-to-end.

## 1. Write the step

Create `.agent/ai-hats/pipeline_steps/echo.py`:

```python
from ai_hats.pipeline.registry import register
from ai_hats.pipeline.step import Step, StepIO


class EchoStep(Step):
    """Wraps the input ``text`` with a marker into ``echoed``."""

    failure_policy = "halt"  # or "continue" — see §"Failure policy"

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

| Field      | Meaning                                                                                                                                |
|---         |---                                                                                                                                     |
| `name`     | YAML id used to reference the step (`- id: echo`).                                                                                     |
| `requires` | Keys that **must** be in pipeline state when the step runs. Missing → `BuildError` at validation, before anything fires.               |
| `optional` | Keys the step may consume if present; missing ones are silently absent.                                                                |
| `produces` | Keys this step is allowed to emit. Returning anything outside this set raises `StepError` and aborts the pipeline.                     |

See `docs/adr/0001-pipelines-as-typed-dataflow.md` for the full contract.

## 2. Reference it from a YAML pipeline

Pipelines live in `.agent/ai-hats/pipelines/<name>.yaml` (project-level) or are built-in. Either way the syntax is the same:

```yaml
name: echo-pipeline
steps:
  - id: echo
```

That's it — `id: echo` matches the string you passed to `register()`.

## 3. Run it

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

## Failure policy

`failure_policy` controls what happens when `run()` raises:

| Value      | Behaviour                                                                                                |
|---         |---                                                                                                       |
| `"halt"`   | Default. Exception re-raises after trace is emitted; the rest of the pipeline does NOT run.              |
| `"continue"` | Exception is captured into `state["errors"][step_name]`; the pipeline keeps going to the next step.    |

Pick `continue` only when downstream steps can cope with the absence of your `produces`. Default to `halt`.

## Debugging — trace mode

To see what every step received and produced, enable trace mode:

```bash
AI_HATS_PIPELINE_TRACE=1 python my_runner.py
```

This writes `<project>/.agent/ai-hats/traces/<pipeline>-<timestamp>.jsonl` with one event per step. Inspect with `jq`:

```bash
cat .agent/ai-hats/traces/echo-pipeline-*.jsonl | jq '{step, requires_seen, produces, duration_ms, error}'
```

For full value reprs (truncated at 120 chars), add `AI_HATS_PIPELINE_TRACE_VALUES=1`. By default values are NOT in the trace, so prompt contents and secrets stay off disk.

## Conflict with a built-in

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

## `AI_HATS_DIR` override — shared step library

By default the loader looks at `<project>/.agent/ai-hats/pipeline_steps/`. To point ai-hats at a shared library elsewhere:

```bash
export AI_HATS_DIR=/team/shared-ai-hats
# loader now reads /team/shared-ai-hats/pipeline_steps/
```

The override applies to **every** ai-hats artefact (traces, future pipelines), so use it when you want to isolate a whole environment, not just step code.

## Conventions

- **Files starting with `_`** (e.g. `_helpers.py`) are skipped by the loader. Use them for shared helpers between user steps.
- **One step per file** is the convention (not enforced); makes it easy to delete or version a single step.
- **Module names are namespaced** under `_ai_hats_user_steps.<stem>` internally — so they don't collide with anything else on `sys.path`.
- **Loaded once per process.** Editing a step file does not hot-reload — restart your invocation to pick up changes.

## Security

`.agent/ai-hats/pipeline_steps/*.py` is **executed by ai-hats** every time a `PipelineHarness` is entered. Same threat model as `.agent/hooks/` shell scripts: do not commit code from sources you do not trust, and review changes from external contributors as carefully as you would any other code.

## See also

- `docs/adr/0001-pipelines-as-typed-dataflow.md` — `Step` / `StepIO` / `Pipeline` contract.
- `docs/adr/0002-pipeline-subsystem-cli.md` — built-in step inventory and CLI design.
- `tests/test_user_steps.py::test_step_runs_in_pipeline_e2e` — exact e2e example this guide is based on (kept in sync with the snippets above).
