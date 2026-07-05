"""Tests for user-authored pipeline-step loader (HATS-275).

The flagship is ``test_step_runs_in_pipeline_e2e`` — it writes a real
user-step file, a real YAML pipeline that references it, and runs the
whole thing through ``PipelineHarness`` without mocks. That's the
"as the user does it" path; the rest cover edge cases of the loader
itself.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from ai_hats.pipeline import registry
from ai_hats.pipeline.harness import PipelineHarness
from ai_hats.pipeline.user_steps import _reset_loader_cache, load_user_steps
from ai_hats.paths import ENV_AI_HATS_DIR


@pytest.fixture(autouse=True)
def _reset_state():
    """Each test gets a clean loader-cache + a snapshot/restore of registry."""
    _reset_loader_cache()
    snapshot = dict(registry._REGISTRY)
    yield
    registry._REGISTRY.clear()
    registry._REGISTRY.update(snapshot)


def _write_step(steps_dir: Path, name: str, body: str) -> Path:
    steps_dir.mkdir(parents=True, exist_ok=True)
    path = steps_dir / f"{name}.py"
    path.write_text(textwrap.dedent(body).lstrip())
    return path


# ---- loader behaviour ------------------------------------------------


def test_no_steps_dir_silent_noop(tmp_path):
    """Empty/absent dir → loader returns [], no error."""
    # paths.pipeline_steps_dir creates the dir on first call, so the
    # "absent" case is really "empty".
    assert load_user_steps(tmp_path) == []


def test_step_registers_via_module_top_level(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_AI_HATS_DIR, raising=False)
    steps_dir = tmp_path / ".agent" / "ai-hats" / "pipeline_steps"
    _write_step(steps_dir, "echo", """
        from ai_hats.pipeline.registry import register
        from ai_hats.pipeline.step import Step, StepIO


        class EchoStep(Step):
            failure_policy = "halt"

            def __init__(self, params=None):
                del params

            @property
            def io(self) -> StepIO:
                return StepIO(
                    name="echo",
                    requires=frozenset({"text"}),
                    produces=frozenset({"echoed"}),
                )

            def run(self, *, text, **_):
                return {"echoed": text}


        register("echo", EchoStep)
    """)

    loaded = load_user_steps(tmp_path)
    assert len(loaded) == 1 and loaded[0].name == "echo.py"

    factory = registry.get("echo")
    assert factory({}).io.name == "echo"


def test_underscore_prefix_modules_skipped(tmp_path, monkeypatch):
    """Files starting with _ are private helpers, not auto-loaded."""
    monkeypatch.delenv(ENV_AI_HATS_DIR, raising=False)
    steps_dir = tmp_path / ".agent" / "ai-hats" / "pipeline_steps"
    # If this module ran, it would raise — proves it was NOT executed.
    _write_step(steps_dir, "_helpers", """
        raise RuntimeError("this should not have been imported")
    """)
    assert load_user_steps(tmp_path) == []


def test_loader_idempotent_within_process(tmp_path, monkeypatch):
    """Re-loading does not fire register() twice → no StepRegistryError."""
    monkeypatch.delenv(ENV_AI_HATS_DIR, raising=False)
    steps_dir = tmp_path / ".agent" / "ai-hats" / "pipeline_steps"
    _write_step(steps_dir, "noop", """
        from ai_hats.pipeline.registry import register
        from ai_hats.pipeline.step import Step, StepIO


        class NoopStep(Step):
            failure_policy = "halt"

            def __init__(self, params=None):
                del params

            @property
            def io(self) -> StepIO:
                return StepIO(name="noop", produces=frozenset({"ok"}))

            def run(self, **_):
                return {"ok": True}


        register("noop", NoopStep)
    """)

    first = load_user_steps(tmp_path)
    assert len(first) == 1
    second = load_user_steps(tmp_path)  # must not raise
    assert second == []  # no NEW imports on second call


def test_conflict_with_builtin_raises(tmp_path, monkeypatch):
    """User module trying to override a built-in → StepRegistryError."""
    monkeypatch.delenv(ENV_AI_HATS_DIR, raising=False)
    steps_dir = tmp_path / ".agent" / "ai-hats" / "pipeline_steps"
    _write_step(steps_dir, "shadow", """
        from ai_hats.pipeline.registry import register
        from ai_hats.pipeline.step import Step, StepIO


        class Hijack(Step):
            failure_policy = "halt"

            def __init__(self, params=None):
                del params

            @property
            def io(self) -> StepIO:
                return StepIO(name="compose_role")

            def run(self, **_):
                return {}


        register("compose_role", Hijack)
    """)

    with pytest.raises(registry.StepRegistryError, match="already registered"):
        load_user_steps(tmp_path)


def test_invalid_step_class_surfaces_error(tmp_path, monkeypatch):
    """A broken module raises at import — surfaces, not swallowed."""
    monkeypatch.delenv(ENV_AI_HATS_DIR, raising=False)
    steps_dir = tmp_path / ".agent" / "ai-hats" / "pipeline_steps"
    _write_step(steps_dir, "broken", """
        raise ValueError("intentional import-time failure")
    """)
    with pytest.raises(ValueError, match="intentional"):
        load_user_steps(tmp_path)


def test_ai_hats_dir_override_for_user_steps(tmp_path, monkeypatch):
    """AI_HATS_DIR=<custom> → loader looks under <custom>/pipeline_steps/,
    NOT under <project>/.agent/ai-hats/pipeline_steps/."""
    custom = tmp_path / "custom-runtime"
    monkeypatch.setenv(ENV_AI_HATS_DIR, str(custom))
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    steps_dir = custom / "pipeline_steps"
    _write_step(steps_dir, "external", """
        from ai_hats.pipeline.registry import register
        from ai_hats.pipeline.step import Step, StepIO


        class External(Step):
            failure_policy = "halt"

            def __init__(self, params=None):
                del params

            @property
            def io(self) -> StepIO:
                return StepIO(name="external", produces=frozenset({"src"}))

            def run(self, **_):
                return {"src": "shared-library"}


        register("external", External)
    """)

    loaded = load_user_steps(project_dir)
    assert len(loaded) == 1
    assert registry.get("external")({}).io.name == "external"


# ---- e2e: real user-step + real YAML + harness ----------------------


def test_step_runs_in_pipeline_e2e(tmp_path, monkeypatch):
    """End-to-end: user drops a step file + a YAML pipeline; harness
    runs them. No mocks inside pipeline-core or registry — this is the
    exact path a real user takes."""
    monkeypatch.delenv(ENV_AI_HATS_DIR, raising=False)

    # 1. The user-authored step file (echoes ``text`` into ``echoed``,
    #    plus prepends a banner so we can verify it actually ran).
    steps_dir = tmp_path / ".agent" / "ai-hats" / "pipeline_steps"
    _write_step(steps_dir, "echo", """
        from ai_hats.pipeline.registry import register
        from ai_hats.pipeline.step import Step, StepIO


        class EchoStep(Step):
            \"\"\"Returns the input text wrapped with a marker.\"\"\"

            failure_policy = "halt"

            def __init__(self, params=None):
                del params

            @property
            def io(self) -> StepIO:
                return StepIO(
                    name="echo",
                    requires=frozenset({"text"}),
                    produces=frozenset({"echoed"}),
                )

            def run(self, *, text, **_):
                return {"echoed": f"[user-step] {text}"}


        register("echo", EchoStep)
    """)

    # 2. The user-authored YAML pipeline that references the new step.
    #    Harness loads built-in pipelines via importlib.resources, so
    #    we patch the loader path for this one. (HATS-268 will surface
    #    project-local pipelines through the same lookup.)
    yaml_path = tmp_path / "echo-pipeline.yaml"
    yaml_path.write_text(textwrap.dedent("""
        name: echo-pipeline
        steps:
          - id: echo
    """).lstrip())

    # 3. Harness loads user steps on entry, then runs the project-local
    #    YAML via run_yaml (minimum-friction proxy for HATS-268's
    #    `ai-hats pipeline run`, which arrives later).
    with PipelineHarness("echo-pipeline", tmp_path) as h:
        final = h.run_yaml(yaml_path, {"text": "hello user"})

    assert final["echoed"] == "[user-step] hello user"
    # Sanity: the step was registered exactly once during harness entry.
    assert "echo" in registry.names()
