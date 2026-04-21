"""Tests for `ai-hats retro-validate` and `ai-hats retro-migrate` CLI commands.

Companion to HATS-122 (exception hygiene): these commands narrowed
their `except Exception` to a concrete tuple, so we lock the
user-visible behaviour for the documented failure modes.
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


def test_retro_validate_ok_on_valid_bundle(tmp_path: Path, runner: CliRunner) -> None:
    bundle = _write(
        tmp_path / "bundle.yaml",
        "schema: hats-bundle/v1\n"
        "bundle_id: BUNDLE-2026-04-17-001\n"
        "project: ai-hats\n"
        "created: 2026-04-17T12:00:00Z\n"
        "session_ids:\n"
        "  - 20260417-120000-abc\n",
    )
    result = runner.invoke(main, ["retro-validate", str(bundle)])
    assert result.exit_code == 0, result.output
    assert "OK" in result.output
    assert "BundleV1" in result.output


def test_retro_validate_fails_on_malformed_yaml(tmp_path: Path, runner: CliRunner) -> None:
    """Malformed YAML must surface as FAIL + exit 1, not be swallowed."""
    bad = _write(tmp_path / "bad.yaml", "schema: hats-bundle/v1\nbundle_id: [unterminated\n")
    result = runner.invoke(main, ["retro-validate", str(bad)])
    assert result.exit_code == 1
    assert "FAIL" in result.output


def test_retro_validate_fails_on_missing_required_field(
    tmp_path: Path, runner: CliRunner
) -> None:
    """Pydantic ValidationError must produce FAIL + exit 1."""
    incomplete = _write(
        tmp_path / "incomplete.yaml",
        "schema: hats-bundle/v1\nbundle_id: BUNDLE-2026-04-17-001\n",  # missing project, created, session_ids
    )
    result = runner.invoke(main, ["retro-validate", str(incomplete)])
    assert result.exit_code == 1
    assert "FAIL" in result.output


