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


def test_launch_session_invokes_human_pipeline(tmp_path: Path, monkeypatch):
    from ai_hats.cli import _launch_session

    # A real onboarded fixture instead of a patched resolver (HATS-1606).
    (tmp_path / ".agent").mkdir()
    monkeypatch.chdir(tmp_path)

    captured: dict[str, object] = {}

    def fake_run(self, initial):
        captured["name"] = self.name
        captured["initial"] = dict(initial)
        return {"exit_code": 0, "session_id": "x", "review_pid": 99}

    with (
        patch(
            "ai_hats.pipeline.harness.PipelineHarness.run",
            autospec=True,
            side_effect=fake_run,
        ),
        pytest.raises(SystemExit) as exc_info,
    ):
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
    # HATS-1218: the provider rides the composition payload, not a second
    # funnel key beside it — no step ever read the old ``provider`` seed.
    assert initial["composition"].provider.name == "claude"
    assert "provider" not in initial
    assert initial["extra_args"] == ["--continue"]
    assert initial["tags"] == {"k": "v"}


def test_launch_session_propagates_nonzero_exit(tmp_path: Path, monkeypatch):
    from ai_hats.cli import _launch_session

    # _launch_session reads ProjectConfig when role is unset.
    (tmp_path / PROJECT_CONFIG).write_text(
        "schema_version: 4\nai_hats_dir: .agent/ai-hats\nprovider: claude\n"
        "active_role: assistant\ndefault_role: assistant\n"
    )
    monkeypatch.chdir(tmp_path)

    with (
        patch(
            "ai_hats.pipeline.harness.PipelineHarness.run",
            return_value={"exit_code": 42},
        ),
        pytest.raises(SystemExit) as exc_info,
    ):
        _launch_session()

    assert exc_info.value.code == 42
