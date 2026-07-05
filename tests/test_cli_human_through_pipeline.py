"""Test that bare ``ai-hats`` routes through ``human.yaml`` via PipelineHarness.

HATS-267 migrated ``_launch_session`` to the pipeline subsystem.
HATS-269 refactored it onto ``PipelineHarness``. Tests patch the
harness layer (``PipelineHarness.run``) to assert wiring without
spawning a real provider.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from ai_hats.paths import PROJECT_CONFIG


def test_launch_session_invokes_human_pipeline(tmp_path: Path):
    from ai_hats.cli import _launch_session

    captured: dict[str, object] = {}

    def fake_run(self, initial):
        captured["name"] = self.name
        captured["initial"] = dict(initial)
        return {"exit_code": 0, "session_id": "x", "review_pid": 99}

    with patch(
        "ai_hats.pipeline.harness.PipelineHarness.run",
        autospec=True, side_effect=fake_run,
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
    assert captured["name"] == "human"
    initial = captured["initial"]
    assert initial["role"] == "judge"
    assert initial["interactive"] is True
    assert initial["project_dir"] == tmp_path
    assert initial["provider"] == "claude"
    assert initial["extra_args"] == ["--continue"]
    assert initial["tags"] == {"k": "v"}


def test_launch_session_propagates_nonzero_exit(tmp_path: Path):
    from ai_hats.cli import _launch_session

    # _launch_session reads ProjectConfig when role is unset.
    (tmp_path / PROJECT_CONFIG).write_text(
        "schema_version: 2\nprovider: claude\nactive_role: assistant\n"
    )

    with patch(
        "ai_hats.pipeline.harness.PipelineHarness.run",
        return_value={"exit_code": 42},
    ), patch(
        "ai_hats.cli._helpers._project_dir", return_value=tmp_path,
    ), pytest.raises(SystemExit) as exc_info:
        _launch_session()

    assert exc_info.value.code == 42
