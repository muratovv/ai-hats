"""Tests for `ai-hats execute` primitive — _resolve_prompt + dispatch."""

from __future__ import annotations

import json
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from ai_hats.cli import main
from ai_hats.cli.execute import _resolve_prompt
from ai_hats.paths import METRICS_JSON, PROJECT_CONFIG, TRACE_LOG


# ---------- _resolve_prompt ----------


def test_resolve_prompt_none(tmp_path: Path) -> None:
    assert _resolve_prompt(None, tmp_path) is None


def test_resolve_prompt_short_name_resolves_to_builtin(tmp_path: Path) -> None:
    # The shipped reflect-all.md must resolve by short name via the
    # built-in entry of LibraryResolver.library_paths (HATS-445).
    text = _resolve_prompt("reflect-all", tmp_path)
    assert text is not None
    assert "Reflect-all triage session" in text


def test_resolve_prompt_filesystem_path(tmp_path: Path) -> None:
    f = tmp_path / "my-prompt.md"
    f.write_text("hello world")
    text = _resolve_prompt(str(f), tmp_path)
    assert text == "hello world"


def test_resolve_prompt_fail_fast_on_path_shape(tmp_path: Path) -> None:
    """Path-shaped arg with no file → fail fast (likely a typo)."""
    with pytest.raises(click.BadParameter):
        _resolve_prompt("./does-not-exist.md", tmp_path)
    with pytest.raises(click.BadParameter):
        _resolve_prompt("/no/such/file.txt", tmp_path)


def test_resolve_prompt_raw_text_fallback(tmp_path: Path) -> None:
    """Plain text (no path-shape) is returned verbatim — supports
    ``--prompt 'ping'`` use case where user just wants to send a message."""
    assert _resolve_prompt("ping", tmp_path) == "ping"
    assert _resolve_prompt("hello world", tmp_path) == "hello world"


def test_resolve_prompt_project_library_overrides_builtin(
    tmp_path: Path,
) -> None:
    """A project-local ``libraries/initial_injections/<name>.md`` shadows
    the built-in file of the same name — last-wins precedence (HATS-445).
    """
    project = tmp_path / "proj"
    project.mkdir()
    (project / PROJECT_CONFIG).write_text(
        "schema_version: 2\nprovider: claude\nactive_role: maintainer\n"
    )
    inj_dir = project / "libraries" / "initial_injections"
    inj_dir.mkdir(parents=True)
    (inj_dir / "reflect-all.md").write_text("PROJECT_OVERRIDE_MARKER")

    text = _resolve_prompt("reflect-all", project)
    assert text == "PROJECT_OVERRIDE_MARKER"


def test_resolve_prompt_project_library_ships_custom_injection(
    tmp_path: Path,
) -> None:
    """End-to-end PoC: a plugin's own ``initial_injections/<name>.md`` is
    discoverable through ``--prompt <name>`` (HATS-445).
    """
    project = tmp_path / "proj"
    project.mkdir()
    (project / PROJECT_CONFIG).write_text(
        "schema_version: 2\nprovider: claude\nactive_role: maintainer\n"
    )
    inj_dir = project / "libraries" / "initial_injections"
    inj_dir.mkdir(parents=True)
    (inj_dir / "rebalance-long.md").write_text(
        "Rebalance the long strategy: ...\n"
    )

    text = _resolve_prompt("rebalance-long", project)
    assert text is not None
    assert "Rebalance the long strategy" in text


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
    (pd / PROJECT_CONFIG).write_text(
        "schema_version: 2\nprovider: claude\nactive_role: primary\n"
    )
    monkeypatch.chdir(pd)
    return pd


class _StubSession:
    def __init__(self, session_dir: Path, metrics: dict) -> None:
        self.session_id = session_dir.name.removeprefix("session_")
        self.session_dir = session_dir
        session_dir.mkdir(parents=True, exist_ok=True)
        self.trace_path = session_dir / TRACE_LOG
        self.trace_path.write_text("(stub)")
        self.metrics_path = session_dir / METRICS_JSON
        self.metrics_path.write_text(json.dumps(metrics))


def test_execute_batch_routes_to_subagent_runner(
    project_dir: Path, monkeypatch
) -> None:
    captured: dict = {}

    class _Runner:
        def __init__(self, _pd, payload, *, session_mgr=None):
            captured["pd"] = _pd
            self._payload = payload

        def run(self, **kwargs):
            captured["kwargs"] = {
                "role_name": self._payload.effective_role, **kwargs,
            }
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
        def __init__(self, _pd, payload, *, session_mgr=None, tracer_factory=None):
            captured["pd"] = _pd
            captured["provider"] = payload.provider.name
            captured["role"] = payload.effective_role

        def run(self, **kwargs):
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
    assert captured["role"] == "judge"
    assert captured["provider"] == "claude"  # from project ai-hats.yaml
    # prompt prepended as first positional arg
    assert "Reflect-all triage session" in captured["extra_args"][0]


def test_execute_interactive_no_prompt_passes_empty_extra_args(
    project_dir: Path, monkeypatch
) -> None:
    captured: dict = {}

    class _WrapRunner:
        def __init__(self, _pd, _payload, *, session_mgr=None, tracer_factory=None): pass

        def run(self, **kwargs):
            captured["extra_args"] = kwargs.get("extra_args")
            return 0, _StubSession(
                project_dir / ".gitlog" / "session_wrap-1", {"exit_code": 0},
            )

    import ai_hats.runtime as runtime_mod
    monkeypatch.setattr(runtime_mod, "WrapRunner", _WrapRunner)

    res = CliRunner().invoke(main, ["execute", "--role", "judge"])
    assert res.exit_code == 0, res.output
    assert captured["extra_args"] == []


def test_execute_batch_fail_fast_on_path_shape(
    project_dir: Path, monkeypatch
) -> None:
    """Path-shaped --prompt that doesn't resolve to a file → fail fast."""
    res = CliRunner().invoke(
        main,
        ["execute", "--role", "x", "--batch", "--prompt", "./missing.md"],
    )
    assert res.exit_code != 0
    assert "looks like a path" in res.output.lower() or "no such" in res.output.lower()
