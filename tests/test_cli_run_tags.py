"""CLI integration tests for `ai-hats agent --tag` (HATS-163).

Verifies the wiring CLI flag → parse_tags → SubAgentRunner.run(tags=...) without
spinning up a real claude/gemini subprocess. A stub SubAgentRunner captures the
kwargs it receives.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from ai_hats.cli import main


class _StubSession:
    def __init__(self, tmp_path: Path) -> None:
        import json

        self.session_id = "stub-session"
        self.session_dir = tmp_path / "session_stub"
        self.session_dir.mkdir(parents=True, exist_ok=True)
        # Pipeline LaunchProvider step reads trace_path/metrics_path —
        # provide both so post-pipeline flat state is well-formed.
        self.trace_path = self.session_dir / "trace.log"
        self.trace_path.write_text("(stub)")
        self.metrics_path = self.session_dir / "metrics.json"
        self.metrics_path.write_text(
            json.dumps({"exit_code": 0, "role": "test-agent"})
        )


class _StubRunner:
    """Captures SubAgentRunner.run() kwargs for assertion."""

    last_kwargs: dict | None = None

    def __init__(self, project_dir: Path, _payload, *, session_mgr=None) -> None:
        self.project_dir = project_dir

    def run(self, **kwargs):
        _StubRunner.last_kwargs = kwargs
        return _StubSession(self.project_dir)


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    (tmp_path / ".gitlog").mkdir()
    # Minimal ai-hats.yaml so _project_dir() resolves here.
    (tmp_path / "ai-hats.yaml").write_text(
        "schema_version: 2\nprovider: claude\nactive_role: test-agent\n"
    )
    return tmp_path


@pytest.fixture
def stub_runner(monkeypatch, project_dir):
    """Replace SubAgentRunner with _StubRunner and cwd into the fake project."""
    import ai_hats.runtime as runtime_mod
    monkeypatch.setattr(runtime_mod, "SubAgentRunner", _StubRunner)
    monkeypatch.chdir(project_dir)
    _StubRunner.last_kwargs = None
    return _StubRunner


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_run_without_tags_passes_none(stub_runner):
    result = CliRunner().invoke(main, ["agent", "test-agent", "--task", "t"])
    assert result.exit_code == 0, result.output
    assert stub_runner.last_kwargs["tags"] is None


def test_run_single_tag(stub_runner):
    result = CliRunner().invoke(
        main, ["agent", "test-agent", "--task", "t", "--tag", "alert_fp=abc123"],
    )
    assert result.exit_code == 0, result.output
    assert stub_runner.last_kwargs["tags"] == {"alert_fp": "abc123"}


def test_run_multiple_tags(stub_runner):
    result = CliRunner().invoke(main, [
        "agent", "test-agent", "--task", "t",
        "--tag", "alert_fp=abc", "--tag", "client=home-lab",
    ])
    assert result.exit_code == 0, result.output
    assert stub_runner.last_kwargs["tags"] == {
        "alert_fp": "abc", "client": "home-lab",
    }


# ---------------------------------------------------------------------------
# Validation errors — surface as BadParameter (exit 2)
# ---------------------------------------------------------------------------


def test_run_tag_missing_equals_fails(stub_runner):
    result = CliRunner().invoke(main, ["agent", "test-agent", "--tag", "broken"])
    assert result.exit_code == 2
    assert "missing '=' separator" in result.output
    # Runner never called.
    assert stub_runner.last_kwargs is None


def test_run_tag_reserved_key_fails(stub_runner):
    result = CliRunner().invoke(
        main, ["agent", "test-agent", "--tag", "role=hacker"],
    )
    assert result.exit_code == 2
    assert "is reserved" in result.output
    assert stub_runner.last_kwargs is None


def test_run_tag_invalid_key_format_fails(stub_runner):
    result = CliRunner().invoke(
        main, ["agent", "test-agent", "--tag", "1bad=v"],
    )
    assert result.exit_code == 2
    assert "must match" in result.output
