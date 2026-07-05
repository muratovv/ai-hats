"""CLI integration tests for `ai-hats session list` filters + --json (HATS-163).

Covers the end-to-end path: CLI flag → parse_tag_filters → SessionManager
filters → table output or JSON list. Tests work on real session_dirs in
tmp_path (no mocking of the filter logic — that's already unit-tested in
test_session_list_filters_unit.py).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from ai_hats.cli import main
from ai_hats.paths import METRICS_JSON, PROJECT_CONFIG, runs_dir, session_dirname


def _make_session(
    project_dir: Path,
    session_id: str,
    *,
    metrics: dict,
) -> None:
    sdir = runs_dir(project_dir) / session_dirname(session_id)
    sdir.mkdir(parents=True)
    (sdir / METRICS_JSON).write_text(json.dumps(metrics))


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    runs_dir(tmp_path).mkdir(parents=True, exist_ok=True)
    (tmp_path / PROJECT_CONFIG).write_text(
        "schema_version: 2\nprovider: claude\nactive_role: primary\n"
    )
    # Set of sessions covering filter axes.
    _make_session(project_dir=tmp_path, session_id="20260401T100000Z_a1", metrics={
        "role": "diagnoser", "provider": "claude",
        "exit_code": 0, "turns": 5, "tool_calls": 10,
        "tokens": {"input": 100, "output": 200, "cache_read": 0, "cache_creation": 0},
        "tags": {"alert_fp": "abc", "client": "home"},
    })
    _make_session(project_dir=tmp_path, session_id="20260410T120000Z_b2", metrics={
        "role": "diagnoser", "provider": "claude",
        "exit_code": 0, "turns": 3, "tool_calls": 7,
        "tokens": {"input": 50, "output": 100, "cache_read": 0, "cache_creation": 0},
        "tags": {"alert_fp": "xyz", "client": "home"},
    })
    _make_session(project_dir=tmp_path, session_id="20260420T090000Z_c3", metrics={
        "role": "primary", "provider": "gemini",
        "exit_code": 1, "turns": 2, "tool_calls": 3,
        "tokens": {"input": 30, "output": 60, "cache_read": 0, "cache_creation": 0},
        "tags": {"alert_fp": "abc", "client": "work"},
    })
    return tmp_path


@pytest.fixture
def cli(monkeypatch, project_dir):
    monkeypatch.chdir(project_dir)
    return CliRunner()


# ---------------------------------------------------------------------------
# JSON output shape
# ---------------------------------------------------------------------------


def test_json_output_is_list(cli):
    result = cli.invoke(main, ["session", "list", "--json", "--all"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert len(data) == 3


def test_json_item_has_computed_fields(cli, project_dir):
    result = cli.invoke(main, ["session", "list", "--json", "--all"])
    data = json.loads(result.output)
    first = data[0]
    assert "session_id" in first
    assert "session_dir" in first
    assert first["session_id"] == "20260401T100000Z_a1"
    # Absolute path — forward compatible for orchestrators.
    assert first["session_dir"].startswith(str(project_dir))
    assert first["started_at"] == "2026-04-01T10:00:00Z"
    # Metrics fields pulled through.
    assert first["role"] == "diagnoser"
    assert first["tags"] == {"alert_fp": "abc", "client": "home"}


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


def test_tag_filter_single(cli):
    result = cli.invoke(main, [
        "session", "list", "--json", "--all",
        "--tag", "alert_fp=abc",
    ])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    ids = [d["session_id"] for d in data]
    assert ids == ["20260401T100000Z_a1", "20260420T090000Z_c3"]


def test_tag_filter_and(cli):
    result = cli.invoke(main, [
        "session", "list", "--json", "--all",
        "--tag", "alert_fp=abc", "--tag", "client=home",
    ])
    data = json.loads(result.output)
    ids = [d["session_id"] for d in data]
    assert ids == ["20260401T100000Z_a1"]


def test_role_filter(cli):
    result = cli.invoke(main, [
        "session", "list", "--json", "--all", "--role", "diagnoser",
    ])
    data = json.loads(result.output)
    ids = [d["session_id"] for d in data]
    assert ids == ["20260401T100000Z_a1", "20260410T120000Z_b2"]


def test_since_filter(cli):
    result = cli.invoke(main, [
        "session", "list", "--json", "--all", "--since", "2026-04-10",
    ])
    data = json.loads(result.output)
    ids = [d["session_id"] for d in data]
    assert ids == ["20260410T120000Z_b2", "20260420T090000Z_c3"]


def test_combined_filters(cli):
    """role + tag + since — all ANDed."""
    result = cli.invoke(main, [
        "session", "list", "--json", "--all",
        "--role", "diagnoser",
        "--tag", "client=home",
        "--since", "2026-04-05",
    ])
    data = json.loads(result.output)
    ids = [d["session_id"] for d in data]
    assert ids == ["20260410T120000Z_b2"]


# ---------------------------------------------------------------------------
# Validation errors propagate
# ---------------------------------------------------------------------------


def test_tag_filter_invalid_format_errors(cli):
    result = cli.invoke(main, [
        "session", "list", "--tag", "broken",
    ])
    assert result.exit_code == 2
    assert "missing '=' separator" in result.output


def test_tag_filter_reserved_key_errors(cli):
    result = cli.invoke(main, [
        "session", "list", "--tag", "role=anything",
    ])
    assert result.exit_code == 2
    assert "is reserved" in result.output


# ---------------------------------------------------------------------------
# Human table still works
# ---------------------------------------------------------------------------


def test_table_output_unchanged_without_json(cli):
    """Default table output still shows all sessions (regression guard).

    Rich truncates long ids; assert on count line + distinct metric.
    """
    result = cli.invoke(main, ["session", "list", "--all"])
    assert result.exit_code == 0
    assert "3 sessions shown" in result.output
    # Distinct row values — turns 5 / 3 / 2 survive column truncation.
    assert "│     5 │" in result.output
    assert "│     2 │" in result.output


def test_table_with_filters(cli):
    """Table output respects new filters (same path as --json)."""
    result = cli.invoke(main, [
        "session", "list", "--all",
        "--tag", "alert_fp=xyz",
    ])
    assert result.exit_code == 0
    assert "1 sessions shown" in result.output
    # b2 has turns=3, tool_calls=7 — unique across fixture.
    assert "     3 │     7 │" in result.output
