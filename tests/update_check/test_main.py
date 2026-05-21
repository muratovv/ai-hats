"""Unit tests for the ``python -m ai_hats.update_check`` entry-point."""

from __future__ import annotations

from unittest.mock import patch

from ai_hats.update_check import __main__ as entry


def test_main_returns_0_when_disabled(monkeypatch):
    monkeypatch.setenv("AI_HATS_NO_UPDATE_CHECK", "1")
    monkeypatch.setattr("sys.argv", ["update_check"])
    assert entry.main() == 0


def test_main_returns_1_when_no_project_dir(monkeypatch):
    monkeypatch.delenv("AI_HATS_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setattr("sys.argv", ["update_check"])
    assert entry.main() == 1


def test_main_returns_1_when_project_dir_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("AI_HATS_NO_UPDATE_CHECK", raising=False)
    missing = tmp_path / "does-not-exist"
    monkeypatch.setattr("sys.argv", ["update_check", str(missing)])
    assert entry.main() == 1


def test_main_calls_run_check_on_valid_input(tmp_path, monkeypatch):
    monkeypatch.delenv("AI_HATS_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setattr("sys.argv", ["update_check", str(tmp_path)])
    with patch.object(entry, "run_check", return_value=None) as m:
        assert entry.main() == 0
        m.assert_called_once()


def test_main_swallows_exceptions(tmp_path, monkeypatch):
    monkeypatch.delenv("AI_HATS_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setattr("sys.argv", ["update_check", str(tmp_path)])
    with patch.object(entry, "run_check", side_effect=RuntimeError("boom")):
        # main must NOT propagate — background subprocess should die silently.
        assert entry.main() == 1
