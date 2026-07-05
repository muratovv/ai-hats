"""Unit tests for the ``ai-hats session show`` Usage section (HATS-734).

The HATS-664 producer (``compute_usage`` → ``usage.json``) had zero in-src
readers, so the resume-mode discovery bug HATS-734 fixes stayed invisible for
months. ``_render_usage`` is the human-facing consumer that makes the channel
falsifiable. These cover: rich rendering of present fields, fail-soft on a
missing / malformed usage.json, and that ``usage.json`` shows in Artifacts.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from ai_hats.cli import main
from ai_hats.paths import runs_dir
from ai_hats.paths import METRICS_JSON, PROJECT_CONFIG, USAGE_JSON, session_dirname

SID = "20260605-100000-1"

_USAGE = {
    "schema_version": "usage/v1",
    "source": "real-claude-uuid.jsonl",
    "session_id": "claude-uuid",
    "role": "maintainer",
    "provider": "claude",
    "exit_code": 0,
    "always_on": {
        "first_input_tokens": 5,
        "first_cache_creation_input_tokens": 18204,
        "first_cache_read_input_tokens": 0,
        "model": "claude",
        "note": "measured proxy",
        "static": {"role": "maintainer", "total_tokens": 17000, "exact": False,
                   "components": []},
    },
    "aggregates": {
        "skill_loads": {"backlog-manager": 1},
        "reference_reads": {},
        "tool_calls": 16, "tool_results": 16, "tool_errors": 4,
        "tool_success_rate": 0.75,
        "hook_firings": 0, "hook_total_ms": 0,
    },
    "sidechain": {"is_sidechain": False, "agent_name": None,
                  "parent_session_id": None},
    "flags": [],
}


def _make_session(project_dir: Path, *, usage: dict | str | None) -> None:
    sdir = runs_dir(project_dir) / session_dirname(SID)
    sdir.mkdir(parents=True)
    (sdir / METRICS_JSON).write_text(json.dumps({
        "role": "maintainer", "provider": "claude", "exit_code": 0,
        "turns": 4, "tool_calls": 16,
    }))
    if usage is None:
        return
    (sdir / USAGE_JSON).write_text(
        usage if isinstance(usage, str) else json.dumps(usage)
    )


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    (tmp_path / PROJECT_CONFIG).write_text(
        "schema_version: 2\nprovider: claude\nactive_role: maintainer\n"
    )
    return tmp_path


@pytest.fixture
def cli(monkeypatch, project_dir):
    monkeypatch.chdir(project_dir)
    return CliRunner()


def test_usage_section_rendered_from_usage_json(cli, project_dir):
    _make_session(project_dir, usage=_USAGE)
    result = cli.invoke(main, ["session", "show", SID])
    assert result.exit_code == 0, result.output
    out = result.output
    assert "Usage" in out
    assert "always_on (measured)" in out
    assert "18,204" in out
    assert "always_on (static)" in out
    assert "17,000" in out
    assert "skill_loads" in out
    assert "backlog-manager x1" in out
    assert "success_rate 0.75" in out


def test_usage_json_listed_in_artifacts(cli, project_dir):
    _make_session(project_dir, usage=_USAGE)
    result = cli.invoke(main, ["session", "show", SID])
    assert result.exit_code == 0, result.output
    assert "Artifacts:" in result.output
    assert USAGE_JSON in result.output


def test_no_usage_section_when_file_absent(cli, project_dir):
    _make_session(project_dir, usage=None)
    result = cli.invoke(main, ["session", "show", SID])
    assert result.exit_code == 0, result.output
    assert "Usage" not in result.output


def test_malformed_usage_json_is_fail_soft(cli, project_dir):
    _make_session(project_dir, usage="{not valid json")
    result = cli.invoke(main, ["session", "show", SID])
    # No crash, no Usage section — the rest of `show` still renders.
    assert result.exit_code == 0, result.output
    assert "Usage" not in result.output
    assert "Session:" in result.output


def test_sidechain_and_flags_surface(cli, project_dir):
    usage = json.loads(json.dumps(_USAGE))
    usage["sidechain"] = {"is_sidechain": True, "agent_name": "Explore",
                          "parent_session_id": "p1"}
    usage["flags"] = ["malformed-lines: 2"]
    _make_session(project_dir, usage=usage)
    result = cli.invoke(main, ["session", "show", SID])
    assert result.exit_code == 0, result.output
    assert "sidechain: Explore" in result.output
    assert "malformed-lines: 2" in result.output
