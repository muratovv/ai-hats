"""CLI integration tests for `ai-hats agent --json` + exit-code propagation (HATS-166).

Mirrors test_cli_run_tags.py patterns: stub SubAgentRunner, drive CLI via
CliRunner, assert stdout is parseable JSON and process exit code matches
``metrics.json["exit_code"]``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from ai_hats.cli import main
from ai_hats.paths import METRICS_JSON, PROJECT_CONFIG, TRACE_LOG


class _StubSession:
    """Emulates a finalized Session with configurable metrics."""

    def __init__(self, session_dir: Path, metrics: dict) -> None:
        self.session_id = session_dir.name.removeprefix("session_")
        self.session_dir = session_dir
        session_dir.mkdir(parents=True, exist_ok=True)
        self.trace_path = session_dir / TRACE_LOG
        self.trace_path.write_text("(stub)")
        self.metrics_path = session_dir / METRICS_JSON
        self.metrics_path.write_text(json.dumps(metrics))


def _install_stub_runner(monkeypatch, project_dir: Path, metrics: dict):
    """Replace SubAgentRunner with a stub that returns a session carrying
    the provided metrics dict."""

    class _Runner:
        def __init__(self, _project_dir, _payload, *, session_mgr=None):
            pass

        def run(self, **_kwargs):
            return _StubSession(
                project_dir / ".gitlog" / "session_stub-session", metrics,
            )

    import ai_hats.runtime as runtime_mod
    monkeypatch.setattr(runtime_mod, "SubAgentRunner", _Runner)


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    (tmp_path / ".gitlog").mkdir()
    (tmp_path / PROJECT_CONFIG).write_text(
        "schema_version: 2\nprovider: claude\nactive_role: test-agent\n"
    )
    return tmp_path


@pytest.fixture
def cli(monkeypatch, project_dir):
    monkeypatch.chdir(project_dir)
    return CliRunner()


# ---------------------------------------------------------------------------
# --json output shape
# ---------------------------------------------------------------------------


def test_json_output_is_single_parseable_object(cli, monkeypatch, project_dir):
    _install_stub_runner(monkeypatch, project_dir, {
        "exit_code": 0,
        "role": "test-agent",
        "model": "sonnet",
        "isolation_mode": "discard",
        "duration_s": 12.345,
    })

    result = cli.invoke(main, ["agent", "test-agent", "--task", "t", "--json"])
    assert result.exit_code == 0, result.output

    payload = json.loads(result.stdout)
    assert payload["session_id"] == "stub-session"
    assert payload["session_dir"].endswith("/.gitlog/session_stub-session")
    # metrics pulled through
    assert payload["exit_code"] == 0
    assert payload["role"] == "test-agent"
    assert payload["duration_s"] == 12.345


def test_json_includes_tags_if_present(cli, monkeypatch, project_dir):
    _install_stub_runner(monkeypatch, project_dir, {
        "exit_code": 0,
        "role": "test-agent",
        "tags": {"alert_fp": "abc", "client": "home"},
    })

    result = cli.invoke(main, [
        "agent", "test-agent", "--task", "t", "--json",
        "--tag", "alert_fp=abc", "--tag", "client=home",
    ])
    assert result.exit_code == 0, result.output

    payload = json.loads(result.stdout)
    assert payload["tags"] == {"alert_fp": "abc", "client": "home"}


def test_json_mode_suppresses_human_output(cli, monkeypatch, project_dir):
    """Stdout must be valid JSON only — no rich decorations."""
    _install_stub_runner(monkeypatch, project_dir, {"exit_code": 0})

    result = cli.invoke(main, ["agent", "test-agent", "--task", "t", "--json"])
    # Parseable as-is, without pre-processing.
    json.loads(result.stdout)
    # The human summary line must not leak into stdout.
    assert "Sub-agent completed" not in result.stdout


# ---------------------------------------------------------------------------
# Exit-code propagation (both JSON and human modes)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("metrics_exit_code", [0, 1, 42, 124])
def test_exit_code_propagates_in_json_mode(
    cli, monkeypatch, project_dir, metrics_exit_code,
):
    _install_stub_runner(monkeypatch, project_dir, {
        "exit_code": metrics_exit_code, "role": "test-agent",
    })

    result = cli.invoke(main, ["agent", "test-agent", "--task", "t", "--json"])
    assert result.exit_code == metrics_exit_code


@pytest.mark.parametrize("metrics_exit_code", [0, 1, 124])
def test_exit_code_propagates_in_human_mode(
    cli, monkeypatch, project_dir, metrics_exit_code,
):
    _install_stub_runner(monkeypatch, project_dir, {
        "exit_code": metrics_exit_code, "role": "test-agent",
    })

    result = cli.invoke(main, ["agent", "test-agent", "--task", "t"])
    assert result.exit_code == metrics_exit_code


def test_missing_metrics_defaults_to_exit_1(cli, monkeypatch, project_dir):
    """Session finished without writing metrics.json → treated as runtime
    failure; exit 1 (never silently 0). JSON output still emitted with
    computed fields so orchestrator sees the crash, not a hang."""

    class _BareSession:
        def __init__(self):
            self.session_id = "bare"
            self.session_dir = project_dir / ".gitlog" / "session_bare"
            self.session_dir.mkdir(parents=True, exist_ok=True)
            self.trace_path = self.session_dir / TRACE_LOG
            self.trace_path.write_text("(stub)")
            self.metrics_path = self.session_dir / METRICS_JSON  # does not exist

    class _Runner:
        def __init__(self, _project_dir, _payload, *, session_mgr=None):
            pass

        def run(self, **_kwargs):
            return _BareSession()

    import ai_hats.runtime as runtime_mod
    monkeypatch.setattr(runtime_mod, "SubAgentRunner", _Runner)

    result = cli.invoke(main, ["agent", "test-agent", "--task", "t", "--json"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["session_id"] == "bare"
    assert "exit_code" not in payload  # metrics absent


# ---------------------------------------------------------------------------
# Human mode regression — existing summary still prints
# ---------------------------------------------------------------------------


def test_human_mode_prints_summary(cli, monkeypatch, project_dir):
    _install_stub_runner(monkeypatch, project_dir, {
        "exit_code": 0, "role": "test-agent",
    })

    result = cli.invoke(main, ["agent", "test-agent", "--task", "t"])
    assert result.exit_code == 0
    assert "Sub-agent completed" in result.stdout
    assert "stub-session" in result.stdout
