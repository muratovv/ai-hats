"""Tests for `ai-hats retro-validate` CLI command.

Locks user-visible behaviour for the documented failure modes
(exception hygiene from HATS-122).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from ai_hats.cli import main


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def _write(path: Path, content: str) -> Path:
    path.write_text(content)
    return path


# --- retro-validate ---


def test_retro_validate_ok_on_valid_session_retro(tmp_path: Path, runner: CliRunner) -> None:
    retro = _write(
        tmp_path / "session.md",
        "---\n"
        "schema: hats-session-retro/v1\n"
        "session_id: 20260417-120000-abc\n"
        "project: ai-hats\n"
        "role: assistant\n"
        "date: 2026-04-17\n"
        "metrics:\n"
        "  exit_code: 0\n"
        "  turns: 5\n"
        "  tool_calls: 12\n"
        "summary: Test\n"
        "links:\n"
        "  audit: a.md\n"
        "---\n\n"
        "# body\n",
    )
    result = runner.invoke(main, ["session", "retro-validate", str(retro)])
    assert result.exit_code == 0, result.output
    assert "OK" in result.output
    assert "SessionRetroV1" in result.output


def test_retro_validate_fails_on_malformed_yaml(tmp_path: Path, runner: CliRunner) -> None:
    """Malformed YAML must surface as FAIL + exit 1, not be swallowed."""
    bad = _write(
        tmp_path / "bad.md",
        "---\nschema: hats-session-retro/v1\nsession_id: [unterminated\n---\n\n",
    )
    result = runner.invoke(main, ["session", "retro-validate", str(bad)])
    assert result.exit_code == 1
    assert "FAIL" in result.output


def test_retro_validate_fails_on_missing_required_field(
    tmp_path: Path, runner: CliRunner
) -> None:
    """Pydantic ValidationError must produce FAIL + exit 1."""
    incomplete = _write(
        tmp_path / "incomplete.md",
        "---\nschema: hats-session-retro/v1\nsession_id: x\n---\n\n",
    )
    result = runner.invoke(main, ["session", "retro-validate", str(incomplete)])
    assert result.exit_code == 1
    assert "FAIL" in result.output
