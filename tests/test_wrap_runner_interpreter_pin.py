"""HATS-1521: seam tests for ``WrapRunner._check_interpreter_pin``.

A venv built on the wrong interpreter used to reach the user in silence and
surface as an unrelated failure at the worst moment (HATS-1519 spent it on a
blocked `git commit`). It must be said out loud at session start, and it must
never block: the venv still runs, it just runs somewhere untested.
"""

import sys

import pytest

from ai_hats import wrap_runner
from ai_hats.constants import PINNED_PYTHON
from ai_hats.paths import runs_dir
from ai_hats.wrap_runner import WrapRunner


def _runner(project):
    from ai_hats.composition_payload import CompositionPayload
    from ai_hats.hooks_manager import HooksManager
    from ai_hats.models import ProjectConfig
    from ai_hats_core import CompositionResult
    from ai_hats_observe import SessionManager, SidecarTracer

    hooks = HooksManager(
        project,
        ProjectConfig(),
        compose=lambda role: None,
        resolve_provider=lambda name: None,
    )
    payload = CompositionPayload(
        result=CompositionResult(name="t", priorities=[], rules=[], skills=[], injections=[]),
        provider=None,
        effective_role="t",
        hooks=hooks,
    )
    return WrapRunner(
        project,
        payload,
        session_mgr=SessionManager(project, runs_dir=runs_dir(project)),
        tracer_factory=SidecarTracer,
    )


@pytest.fixture
def runner(tmp_path):
    return _runner(tmp_path)


def test_the_pinned_interpreter_says_nothing(runner, monkeypatch):
    major, minor = (int(p) for p in PINNED_PYTHON.split("."))
    monkeypatch.setattr(sys, "version_info", (major, minor, 0, "final", 0))

    assert runner._check_interpreter_pin() == []


def test_an_older_interpreter_warns(runner, monkeypatch):
    monkeypatch.setattr(sys, "version_info", (3, 11, 15, "final", 0))

    notices = runner._check_interpreter_pin()

    assert [n.level for n in notices] == ["warn"]
    assert "3.11" in notices[0].text
    assert PINNED_PYTHON in notices[0].text


def test_a_newer_interpreter_warns_too(runner, monkeypatch):
    """Ahead of the pin is still outside the matrix — the dev venv drifted to
    3.14 while the pin sat on 3.11, which is how nobody noticed."""
    monkeypatch.setattr(sys, "version_info", (3, 99, 0, "final", 0))

    assert [n.level for n in runner._check_interpreter_pin()] == ["warn"]


def _managed_venv(monkeypatch, project, *, ours: bool) -> None:
    """Put the running venv under the framework dir, with ai-hats installed into
    it (``ours``) or pointing back at a checkout outside it (editable)."""
    prefix = project / ".agent" / "ai-hats" / ".venv"
    (prefix / "lib").mkdir(parents=True)
    monkeypatch.setattr(sys, "prefix", str(prefix))
    pkg = prefix / "lib" / "ai_hats" if ours else project / "src" / "ai_hats"
    pkg.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(wrap_runner, "__file__", str(pkg / "wrap_runner.py"))


def test_the_managed_venv_gets_the_command_that_rebuilds_it(runner, tmp_path, monkeypatch):
    """House format (HATS-1522): exactly one `Fix:` line, self-contained enough to
    copy. `self update` is NOT it — on a current sha it rebuilds nothing."""
    monkeypatch.setattr(sys, "version_info", (3, 11, 15, "final", 0))
    _managed_venv(monkeypatch, tmp_path, ours=True)

    text = runner._check_interpreter_pin()[0].text

    fix = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("Fix:")]
    assert fix == [f"Fix: {wrap_runner._REPAIR_CMD}"]


def test_a_venv_ai_hats_did_not_build_is_not_offered_a_rebuild(runner, tmp_path, monkeypatch):
    """`--repair` deletes the managed venv and leaves a user-owned one alone, so
    offering it to an override / editable install would be a command that lies."""
    monkeypatch.setattr(sys, "version_info", (3, 11, 15, "final", 0))
    _managed_venv(monkeypatch, tmp_path, ours=False)

    text = runner._check_interpreter_pin()[0].text

    assert "Fix:" not in text
    assert "will not touch it" in text


def test_the_warning_names_no_ai_hats_subcommand(runner, monkeypatch):
    """The first cut said `ai-hats self update --reinstall`: a flag click rejects
    with exit 2, and `self update` cannot be relied on to move a venv onto the pin
    (a current sha reuses the venv it runs from). The remedy is the rebuild — name
    a CLI command here again only after checking it parses AND that it rebuilds."""
    monkeypatch.setattr(sys, "version_info", (3, 11, 15, "final", 0))

    assert "ai-hats self" not in runner._check_interpreter_pin()[0].text


def test_this_very_session_is_on_the_pin_or_says_so(runner):
    """No monkeypatch: whatever interpreter runs the suite, the check agrees
    with it. Guards against a message that only renders under a fake tuple."""
    running = f"{sys.version_info.major}.{sys.version_info.minor}"
    notices = runner._check_interpreter_pin()

    assert notices == [] if running == PINNED_PYTHON else running in notices[0].text
