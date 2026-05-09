"""Tests for `ai-hats execute` primitive — _resolve_prompt + dispatch."""

from __future__ import annotations

import json
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from ai_hats.cli import main
from ai_hats.cli.execute import _initial_injections_dir, _resolve_prompt


# ---------- _resolve_prompt ----------


def test_resolve_prompt_none() -> None:
    assert _resolve_prompt(None) is None


def test_resolve_prompt_short_name_resolves_to_builtin(tmp_path: Path) -> None:
    # The shipped reflect-all.md must resolve by short name.
    text = _resolve_prompt("reflect-all")
    assert text is not None
    assert "Reflect-all triage session" in text


def test_resolve_prompt_filesystem_path(tmp_path: Path) -> None:
    f = tmp_path / "my-prompt.md"
    f.write_text("hello world")
    text = _resolve_prompt(str(f))
    assert text == "hello world"


def test_resolve_prompt_fail_fast(tmp_path: Path) -> None:
    with pytest.raises(click.BadParameter):
        _resolve_prompt("does-not-exist-anywhere")


def test_initial_injections_dir_points_to_package_libraries() -> None:
    d = _initial_injections_dir()
    assert d.name == "initial_injections"
    assert d.parent.name == "libraries"
    assert (d / "reflect-all.md").is_file()


# ---------- execute --help (smoke for click wiring) ----------


def test_execute_help_lists_flags() -> None:
    res = CliRunner().invoke(main, ["execute", "--help"])
    assert res.exit_code == 0
    for flag in ("--role", "--provider", "--interactive", "--batch",
                 "--prompt", "--model", "--isolation", "--ticket", "--tag"):
        assert flag in res.output


# ---------- execute --batch dispatch (mocks SubAgentRunner) ----------


@pytest.fixture
def project_dir(tmp_path: Path, monkeypatch) -> Path:
    pd = tmp_path / "proj"
    pd.mkdir()
    (pd / ".gitlog").mkdir()
    (pd / "ai-hats.yaml").write_text(
        "schema_version: 2\nprovider: claude\nactive_role: primary\n"
    )
    monkeypatch.chdir(pd)
    return pd


class _StubSession:
    def __init__(self, session_dir: Path, metrics: dict) -> None:
        self.session_id = session_dir.name.removeprefix("session_")
        self.session_dir = session_dir
        session_dir.mkdir(parents=True, exist_ok=True)
        self.trace_path = session_dir / "trace.log"
        self.trace_path.write_text("(stub)")
        self.metrics_path = session_dir / "metrics.json"
        self.metrics_path.write_text(json.dumps(metrics))


def test_execute_batch_routes_to_subagent_runner(
    project_dir: Path, monkeypatch
) -> None:
    captured: dict = {}

    class _Runner:
        def __init__(self, _pd):
            captured["pd"] = _pd

        def run(self, **kwargs):
            captured["kwargs"] = kwargs
            return _StubSession(
                project_dir / ".gitlog" / "session_stub-1", {"exit_code": 0},
            )

    import ai_hats.runtime as runtime_mod
    monkeypatch.setattr(runtime_mod, "SubAgentRunner", _Runner)

    res = CliRunner().invoke(
        main,
        ["execute", "--role", "session-reviewer", "--batch",
         "--prompt", "reflect-all", "--ticket", "HATS-001"],
    )
    assert res.exit_code == 0, res.output
    kw = captured["kwargs"]
    assert kw["role_name"] == "session-reviewer"
    assert "Reflect-all triage session" in kw["task"]  # resolved by short name
    assert kw["ticket_id"] == "HATS-001"


def test_execute_interactive_routes_to_wraprunner(
    project_dir: Path, monkeypatch
) -> None:
    captured: dict = {}

    class _WrapRunner:
        def __init__(self, _pd):
            captured["pd"] = _pd

        def run(self, provider, **kwargs):
            captured["provider"] = provider
            captured.update(kwargs)
            return 0, _StubSession(
                project_dir / ".gitlog" / "session_wrap-1", {"exit_code": 0},
            )

    import ai_hats.runtime as runtime_mod
    monkeypatch.setattr(runtime_mod, "WrapRunner", _WrapRunner)

    res = CliRunner().invoke(
        main,
        ["execute", "--role", "judge", "--interactive", "--prompt",
         "reflect-all"],
    )
    assert res.exit_code == 0, res.output
    assert captured["role_override"] == "judge"
    assert captured["provider"] == "claude"  # from project ai-hats.yaml
    # prompt prepended as first positional arg
    assert "Reflect-all triage session" in captured["extra_args"][0]


def test_execute_interactive_no_prompt_passes_empty_extra_args(
    project_dir: Path, monkeypatch
) -> None:
    captured: dict = {}

    class _WrapRunner:
        def __init__(self, _pd): pass

        def run(self, provider, **kwargs):
            captured["extra_args"] = kwargs.get("extra_args")
            return 0, _StubSession(
                project_dir / ".gitlog" / "session_wrap-1", {"exit_code": 0},
            )

    import ai_hats.runtime as runtime_mod
    monkeypatch.setattr(runtime_mod, "WrapRunner", _WrapRunner)

    res = CliRunner().invoke(main, ["execute", "--role", "judge"])
    assert res.exit_code == 0, res.output
    assert captured["extra_args"] == []


def test_execute_batch_fail_fast_on_unknown_prompt(
    project_dir: Path, monkeypatch
) -> None:
    res = CliRunner().invoke(
        main, ["execute", "--role", "x", "--batch", "--prompt", "no-such-name"],
    )
    assert res.exit_code != 0
    assert "no such" in res.output.lower() or "not a known" in res.output.lower()
