"""Test that bare ``ai-hats`` routes through ``bare.yaml`` pipeline.

HATS-267: ``_launch_session`` was migrated from direct ``_do_execute``
to ``loader.load_pipeline("bare.yaml").run(...)``. This test patches
the loader/run so we can assert the wiring without spawning a real
provider.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def test_launch_session_invokes_bare_pipeline(tmp_path: Path):
    from ai_hats.cli import _launch_session

    fake_pipeline = MagicMock(name="bare_pipeline")
    captured: dict[str, object] = {}

    def fake_run(pipeline, initial):
        captured["pipeline"] = pipeline
        captured["initial"] = dict(initial)
        return {"exit_code": 0, "session_id": "x", "review_pid": 99}

    with patch(
        "ai_hats.pipeline.loader.load_pipeline", return_value=fake_pipeline,
    ) as load_mock, patch(
        "ai_hats.pipeline.pipeline.run", side_effect=fake_run,
    ), patch(
        "ai_hats.cli._helpers._project_dir", return_value=tmp_path,
    ), pytest.raises(SystemExit) as exc_info:
        _launch_session(
            provider="claude",
            role="judge",
            extra_args=["--continue"],
            tags={"k": "v"},
        )

    assert exc_info.value.code == 0
    # loader was called with the bare.yaml resource path
    load_args = load_mock.call_args[0]
    assert "bare.yaml" in str(load_args[0])
    # initial state has the right shape
    assert captured["pipeline"] is fake_pipeline
    initial = captured["initial"]
    assert initial["role"] == "judge"
    assert initial["interactive"] is True
    assert initial["project_dir"] == tmp_path
    assert initial["provider"] == "claude"
    assert initial["extra_args"] == ["--continue"]
    assert initial["tags"] == {"k": "v"}


def test_launch_session_propagates_nonzero_exit(tmp_path: Path):
    from ai_hats.cli import _launch_session

    with patch(
        "ai_hats.pipeline.loader.load_pipeline",
        return_value=MagicMock(),
    ), patch(
        "ai_hats.pipeline.pipeline.run", return_value={"exit_code": 42},
    ), patch(
        "ai_hats.cli._helpers._project_dir", return_value=tmp_path,
    ), pytest.raises(SystemExit) as exc_info:
        _launch_session()

    assert exc_info.value.code == 42
