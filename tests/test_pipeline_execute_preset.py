"""End-to-end tests for ``execute_pipeline`` preset (HATS-267 shape).

The preset is now [ComposeRole, ResolvePrompt, PreLog, LaunchProvider,
SpawnSessionReview, PostLog] — same shape as ``execute.yaml``. Tests
mock at the runner level (WrapRunner/SubAgentRunner) so we exercise
the real LaunchProvider step.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ai_hats.pipeline import run
from ai_hats.pipeline.presets import execute_pipeline


def _fake_session(tmp_path: Path) -> MagicMock:
    sess = MagicMock()
    sess.session_id = "sid-test"
    sess.session_dir = tmp_path / "sd"
    sess.session_dir.mkdir(parents=True, exist_ok=True)
    sess.trace_path = sess.session_dir / "trace.log"
    sess.trace_path.write_text("(empty)")
    sess.metrics_path = sess.session_dir / "metrics.json"
    return sess


def test_execute_preset_interactive_threads_flat_keys(tmp_path: Path):
    sess = _fake_session(tmp_path)
    runner = MagicMock()
    runner.run.return_value = (0, sess)

    with patch(
        "ai_hats.runtime.WrapRunner", return_value=runner,
    ), patch(
        "ai_hats.models.ProjectConfig.from_yaml",
        return_value=SimpleNamespace(provider="claude"),
    ), patch("subprocess.Popen", return_value=MagicMock(pid=1)):
        state = run(execute_pipeline, {
            "interactive": True,
            "role": None,
            "project_dir": tmp_path,
            "extra_args": [],
        })

    assert state["exit_code"] == 0
    assert state["session_id"] == "sid-test"
    assert state["session_dir"] == sess.session_dir
    assert state["transcript_path"] == sess.trace_path
    assert state["review_pid"] == 1
    # HATS-452 (П3): compose_role with role=None omits the key entirely;
    # downstream consumers treat missing == None == "no override".
    assert "system_prompt" not in state
    assert state["prompt_text"] == ""


def test_execute_preset_batch_reads_metrics(tmp_path: Path):
    sess = _fake_session(tmp_path)
    sess.metrics_path.write_text(json.dumps({"exit_code": 7}))
    runner = MagicMock()
    runner.run.return_value = sess

    with patch(
        "ai_hats.runtime.SubAgentRunner", return_value=runner,
    ), patch("subprocess.Popen", return_value=MagicMock(pid=2)):
        state = run(execute_pipeline, {
            "interactive": False,
            "role": None,
            "project_dir": tmp_path,
            "ticket": "HATS-267",
        })

    assert state["exit_code"] == 7
    assert state["session_id"] == "sid-test"


def test_execute_preset_batch_missing_metrics_defaults_to_one(tmp_path: Path):
    sess = _fake_session(tmp_path)
    runner = MagicMock()
    runner.run.return_value = sess

    with patch(
        "ai_hats.runtime.SubAgentRunner", return_value=runner,
    ), patch("subprocess.Popen", return_value=MagicMock(pid=3)):
        state = run(execute_pipeline, {
            "interactive": False,
            "role": None,
            "project_dir": tmp_path,
        })

    assert state["exit_code"] == 1


def test_execute_pipeline_io_shape():
    io = execute_pipeline.io
    assert io.name == "execute"
    # external requires after compose_role made `role` optional
    assert "interactive" in io.requires
    assert "project_dir" in io.requires
    # produces flat keys per ADR-0002 §Step inventory
    for k in ("session_id", "session_dir", "transcript_path", "exit_code"):
        assert k in io.produces


def test_execute_pipeline_skeleton():
    assert execute_pipeline.pipeline_name == "execute"
    names = [s.io.name for s in execute_pipeline.steps]
    assert names == [
        "check_update_async",
        "compose_role",
        "resolve_prompt",
        "pre_log",
        "launch_provider",
        "spawn_session_review",
        "post_log",
        "render_update_banner",
    ]


def test_execute_preset_log_steps_print(tmp_path: Path, capsys):
    sess = _fake_session(tmp_path)
    runner = MagicMock()
    runner.run.return_value = (0, sess)

    with patch(
        "ai_hats.runtime.WrapRunner", return_value=runner,
    ), patch(
        "ai_hats.models.ProjectConfig.from_yaml",
        return_value=SimpleNamespace(provider="claude"),
    ), patch("subprocess.Popen", return_value=MagicMock(pid=1)):
        run(execute_pipeline, {
            "interactive": True,
            "role": None,
            "project_dir": tmp_path,
        })

    err = capsys.readouterr().err
    assert "pre_log fires" in err
    assert "post_log fires" in err
    assert "exit_code" in err
