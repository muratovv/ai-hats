"""Tests for HATS-1164: self update isolation and dev environment integrity tripwire."""

from __future__ import annotations

from unittest.mock import patch

from ai_hats.cli.assembly import _run_self_update
from ai_hats.models import Channel


def test_self_update_guards_against_installing_into_running_dev_env(monkeypatch, capsys):
    """Under test runner environment without explicit target_python, _run_self_update skips install."""
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "test")

    with patch("subprocess.run") as mock_run:
        _run_self_update(target_python=None)
        mock_run.assert_not_called()

    captured = capsys.readouterr()
    assert "Update skipped" in captured.out or "running inside test runner environment" in captured.out


def test_self_update_accepts_sandbox_target_python(tmp_path, monkeypatch):
    """When target_python seam is provided, _run_self_update uses that sandbox interpreter."""
    monkeypatch.setenv("AI_HATS_ALLOW_SELF_UPDATE_IN_TEST", "1")
    sandbox_py = tmp_path / "bin" / "python"

    with (
        patch("ai_hats.cli.maintenance._read_harness", return_value=(Channel.LOCAL, None, None)),
        patch("ai_hats.cli.maintenance._run_post_install_verify", return_value=(True, "")),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value.returncode = 0
        _run_self_update(target_python=sandbox_py)

        uv_calls = [c for c in mock_run.call_args_list if c[0] and c[0][0][0] == "uv"]
        assert len(uv_calls) == 1
        cmd = uv_calls[0][0][0]
        assert "--python" in cmd
        python_idx = cmd.index("--python")
        assert cmd[python_idx + 1] == str(sandbox_py)
