"""End-to-end test: ``execute.yaml`` runs through the loader on the batch path.

The interactive path is covered by ``test_pipeline_human_yaml.py``; what only this
file covers is how ``provider`` resolves an exit code when the runner is
``SubAgentRunner`` — from the session's ``metrics.json``, and 1 when there is none.

HATS-1783: these two cases arrived here from ``test_pipeline_execute_preset.py``,
which ran them against ``pipeline.presets.execute_pipeline`` — a Python-assembled
pipeline that no longer exists, and whose step list was never ``execute.yaml``'s.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from ai_hats.pipeline.loader import load_pipeline
from ai_hats.pipeline.pipeline import run as run_pipeline
from ai_hats_observe.artifacts import METRICS_JSON, TRACE_LOG


_BUILTIN = (
    Path(__file__).parent.parent
    / "packages/ai-hats-library/src/ai_hats_library/core/pipelines/execute.yaml"
)


def _fake_session(tmp_path: Path) -> MagicMock:
    sess = MagicMock()
    sess.session_id = "sid-test"
    sess.session_dir = tmp_path / "sd"
    sess.session_dir.mkdir(parents=True, exist_ok=True)
    sess.trace_path = sess.session_dir / TRACE_LOG
    sess.trace_path.write_text("(empty)")
    sess.metrics_path = sess.session_dir / METRICS_JSON
    return sess


def _run_batch(tmp_path: Path, sess: MagicMock) -> dict:
    runner = MagicMock()
    runner.run.return_value = sess
    with (
        patch("ai_hats.runtime.SubAgentRunner", return_value=runner),
        patch("subprocess.Popen", return_value=MagicMock(pid=2)),
    ):
        return run_pipeline(
            load_pipeline(_BUILTIN),
            {
                "interactive": False,
                "role": None,
                "project_dir": tmp_path,
                "composition": MagicMock(name="composition_payload"),
                "session_mgr": MagicMock(name="session_mgr"),
                "tracer_factory": MagicMock(name="tracer_factory"),
            },
        )


def test_execute_yaml_batch_reads_the_exit_code_from_metrics(tmp_path: Path):
    sess = _fake_session(tmp_path)
    sess.metrics_path.write_text(json.dumps({"exit_code": 7}))

    state = _run_batch(tmp_path, sess)

    assert state["exit_code"] == 7
    assert state["session_id"] == "sid-test"


def test_execute_yaml_batch_without_metrics_defaults_the_exit_code_to_one(tmp_path: Path):
    """No metrics file means the run cannot be called a success."""
    state = _run_batch(tmp_path, _fake_session(tmp_path))

    assert state["exit_code"] == 1


def test_execute_yaml_io_shape():
    io = load_pipeline(_BUILTIN).io

    assert io.name == "execute"
    # `role` is optional — compose_role projects a seeded payload (HATS-865).
    assert {"interactive", "project_dir", "composition"} <= io.requires
    for key in ("session_id", "session_dir", "transcript_path", "exit_code"):
        assert key in io.produces
