"""Tests for pipeline.loader — YAML schema + registry resolution."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from ai_hats.pipeline.loader import (
    PipelineYamlError,
    clear_core_pipeline_cache,
    load_core_pipeline,
    load_pipeline,
)


_BUILTIN_DIR = (
    Path(__file__).parent.parent / "packages/ai-hats-library/src/ai_hats_library/core/pipelines"
)


@pytest.mark.parametrize(
    "name", ["human", "execute", "reflect-all", "reflect-session"]
)
def test_load_each_builtin(name: str):
    p = load_pipeline(_BUILTIN_DIR / f"{name}.yaml")
    assert p.io.name == name
    assert len(p.steps) >= 1


def test_load_invalid_yaml(tmp_path: Path):
    f = tmp_path / "bad.yaml"
    f.write_text("name: x\nsteps: [: : :")
    with pytest.raises(PipelineYamlError, match="invalid YAML"):
        load_pipeline(f)


def test_load_unknown_step(tmp_path: Path):
    f = tmp_path / "p.yaml"
    f.write_text("name: x\nsteps:\n  - id: nonexistent_step\n")
    with pytest.raises(PipelineYamlError, match="unknown step"):
        load_pipeline(f)


def test_load_missing_top_level_name(tmp_path: Path):
    f = tmp_path / "p.yaml"
    f.write_text("steps:\n  - id: pre_log\n")
    with pytest.raises(PipelineYamlError, match="'name' must be"):
        load_pipeline(f)


def test_load_empty_steps(tmp_path: Path):
    f = tmp_path / "p.yaml"
    f.write_text("name: x\nsteps: []\n")
    with pytest.raises(PipelineYamlError, match="non-empty list"):
        load_pipeline(f)


def test_load_step_missing_id(tmp_path: Path):
    f = tmp_path / "p.yaml"
    f.write_text("name: x\nsteps:\n  - params: {}\n")
    with pytest.raises(PipelineYamlError, match="id must be"):
        load_pipeline(f)


def test_load_invalid_step_params(tmp_path: Path):
    f = tmp_path / "p.yaml"
    # extract_marker requires start/end/out_key
    f.write_text(
        "name: x\nsteps:\n"
        "  - id: extract_marker\n"
        "    params: {start: A}\n"
    )
    with pytest.raises(PipelineYamlError, match="missing param"):
        load_pipeline(f)


def test_load_top_level_not_mapping(tmp_path: Path):
    f = tmp_path / "p.yaml"
    f.write_text("- just\n- a\n- list\n")
    with pytest.raises(PipelineYamlError, match="top-level"):
        load_pipeline(f)


# ---- HATS-378 Phase 0: harness policy on pipeline steps ----


def test_load_step_without_harness_block_has_none_policy(tmp_path: Path):
    f = tmp_path / "p.yaml"
    f.write_text(
        "name: x\nsteps:\n"
        "  - id: pre_log\n"
    )
    p = load_pipeline(f)
    assert p.steps[0].harness_policy is None


def test_load_step_with_harness_block_attaches_policy(tmp_path: Path):
    f = tmp_path / "p.yaml"
    f.write_text(
        "name: x\nsteps:\n"
        "  - id: pre_log\n"
        "    harness:\n"
        "      reporting: true\n"
        "      on_zero_output: harness_incident\n"
        "      on_timeout:\n"
        "        retry: 1\n"
        "        budget_multiplier: 2\n"
        "        then: harness_incident\n"
    )
    p = load_pipeline(f)
    policy = p.steps[0].harness_policy
    assert policy is not None
    assert policy.reporting is True
    assert policy.on_zero_output == "harness_incident"
    assert policy.on_timeout is not None
    assert policy.on_timeout.retry == 1
    assert policy.on_timeout.budget_multiplier == 2.0


def test_load_step_with_invalid_harness_block_raises(tmp_path: Path):
    f = tmp_path / "p.yaml"
    f.write_text(
        "name: x\nsteps:\n"
        "  - id: pre_log\n"
        "    harness:\n"
        "      reporting: maybe\n"
    )
    with pytest.raises(PipelineYamlError, match="harness:.*reporting must be bool"):
        load_pipeline(f)


# ---- load_core_pipeline cache (HATS-566) ----


def test_load_core_pipeline_memoizes_first_call():
    """HATS-566: ``load_core_pipeline`` returns the same Pipeline object
    on repeated calls so its YAML is parsed against the step registry
    in memory at first access, not at each call.

    Regression guard for editable-install drift: a long-running session
    that straddles a working-tree update would otherwise read updated
    YAML at session-end against the stale module-loaded registry, hit
    ``StepRegistryError``, and lose ``run_session_end`` + auto-retro
    spawn from the ``finalize-hitl`` pipeline.
    """
    clear_core_pipeline_cache()
    try:
        first = load_core_pipeline("finalize-hitl")
        second = load_core_pipeline("finalize-hitl")
        assert first is second
    finally:
        clear_core_pipeline_cache()


def test_load_core_pipeline_use_cache_false_bypasses_memo():
    """``use_cache=False`` skips the memo for tests that swap the
    registry between cases.
    """
    clear_core_pipeline_cache()
    try:
        cached = load_core_pipeline("finalize-hitl")
        fresh = load_core_pipeline("finalize-hitl", use_cache=False)
        # Same pipeline definition, but distinct Pipeline objects when
        # cache is bypassed — proves the path is hot.
        assert fresh is not cached
        assert fresh.io.name == cached.io.name
    finally:
        clear_core_pipeline_cache()


def test_core_pipeline_cache_absorbs_on_disk_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Faithful reproduction of HATS-566: an editable-install YAML rewrite
    mid-session must NOT crash the finalize pipeline.

    Scenario: a long-running ``WrapRunner`` session preloads
    ``finalize-hitl`` (eager preload in ``runtime.py`` before ``_pty_spawn``),
    then a ``git pull`` / merge lands a new step into the on-disk YAML
    against the registry already loaded at process start. The memoized
    cache must serve the preloaded Pipeline so ``_run_finalize_hitl``
    (``run_session_end`` + auto-retro spawn) survives the drift instead of
    dying on ``StepRegistryError``.

    Unlike the identity tests above, this one actually rewrites the YAML on
    disk between calls — proving the cache *absorbs* drift, not merely that
    repeated calls return the same object.
    """
    import ai_hats.paths.library as _paths_library

    # Tmp library layout that ``load_core_pipeline`` resolves through. Patch the
    # builtin-library resolver seam (HATS-831) rather than ``importlib.files`` —
    # the resolver is cwd-aware and short-circuits to the live source checkout
    # before reaching importlib, so patching importlib no longer intercepts.
    # Patch in ``paths.library`` (where ``core_pipeline_path`` resolves the name),
    # not the ``ai_hats.paths`` re-export.
    pipelines_dir = tmp_path / "core" / "pipelines"
    pipelines_dir.mkdir(parents=True)
    yaml_file = pipelines_dir / "finalize-hitl.yaml"
    yaml_file.write_text("name: finalize-hitl\nsteps:\n  - id: pre_log\n")

    monkeypatch.setattr(_paths_library, "builtin_library_root", lambda: tmp_path)

    clear_core_pipeline_cache()
    try:
        # 1. Eager preload — mirrors WrapRunner.run() before _pty_spawn.
        preloaded = load_core_pipeline("finalize-hitl")

        # 2. Mid-session working-tree update introduces a step the
        #    in-memory registry has never heard of.
        yaml_file.write_text(
            "name: finalize-hitl\nsteps:\n  - id: nonexistent_step\n"
        )

        # 3. Cache absorbs the drift — same object, NO exception.
        assert load_core_pipeline("finalize-hitl") is preloaded

        # 4. Sanity: the drift is real. Bypassing the cache reads the
        #    rewritten YAML and would have crashed _run_finalize_hitl.
        with pytest.raises(PipelineYamlError, match="unknown step"):
            load_core_pipeline("finalize-hitl", use_cache=False)
    finally:
        clear_core_pipeline_cache()


# ---- __main__ dry-run inspector ----


@pytest.mark.integration
@pytest.mark.parametrize(
    "name", ["human", "execute", "reflect-all", "reflect-session"]
)
def test_loader_main_inspects_each_builtin(name: str):
    proc = subprocess.run(
        [sys.executable, "-m", "ai_hats.pipeline.loader",
         str(_BUILTIN_DIR / f"{name}.yaml")],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert f"Pipeline: {name}" in proc.stdout
    assert "Steps:" in proc.stdout
    assert "external requires" in proc.stdout
