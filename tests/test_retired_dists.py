"""Tests for src/ai_hats/retired_dists.py — retired-distribution prune (HATS-1280).

The module runs from ``_bump_internal`` in the middle of ``self update``, so the
property every test below defends is **fail-open**: never raise, never hang,
never remove something still needed.

T1  kill switch: ``ENV_SKIP_PRUNE`` set → ``[]`` and NOT ONE subprocess call.
T2  declared-dependency guard: a name ai-hats still requires is never uninstalled.
T2c control: the same setup WITHOUT the guard does uninstall (T2/T3/T4 not vacuous).
T3  guard failure is conservative: raising, an empty answer, or an ai-hats that
    cannot resolve its own metadata all → prune nothing.
T4  not installed → no ``uv``, no subprocess (the "no measurable cost" property).
T5  no ``uv`` on PATH → clean False, no raise.
T6  ``uv`` non-zero exit → not-removed, no raise.
T7  ``subprocess.TimeoutExpired`` → swallowed (a hang is what ``except`` cannot cover).
T8  ``KeyboardInterrupt`` → swallowed; this is why ``_uninstall`` catches BaseException.
T9  ``strip_retired_scripts``: removes the retired script, spares the rest and
    anything still declared, idempotent, survives a missing dir and a bad path.
T10 ``prune_retired`` never raises even when its internals throw BaseException.
T11 the uninstall timeout (and ``start_new_session``) really reach ``subprocess.run``.

Every test that lets the prune run replaces ``subprocess.run`` and/or
``shutil.which`` first: an escaped call would uninstall from the developer's own
venv (which is exactly what the session-autouse ``_no_retired_prune`` fixture in
tests/conftest.py exists to prevent — tests that need the prune ACTIVE delete
that var explicitly via :func:`_activate_prune`).

``prune_retired`` has TWO early returns, and a test that drives it must neutralise
BOTH or it passes for the wrong reason: the kill switch (:func:`_activate_prune`)
AND the editable-install exemption (:func:`_not_editable`) — this checkout and CI
are both editable installs, so the second one fires in every unit-test run.
"""  # comment-length: allow

from __future__ import annotations

import importlib.metadata
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from ai_hats import retired_dists
from ai_hats.paths import ai_hats_dir

RETIRED_NAME = "ai-hats-tracker"
RETIRED_SCRIPT = "ai-hats-tracker"


# ---------- the default-deny guard for the whole module ----------


@pytest.fixture(autouse=True)
def _no_real_uv(monkeypatch):
    """Default-deny the uv lookup for EVERY test here: an unstubbed
    ``shutil.which`` resolves the developer's real uv and uninstalls out of this
    very venv. Tests that want uv re-patch it (their setattr runs after and wins).

    Records as well as raises — ``prune_retired`` swallows ``BaseException``, so a
    breach raised inside it would vanish; the teardown assert is what shows it.
    """
    real_which = shutil.which
    reached: list[str] = []

    def guarded(cmd, *args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        if cmd == "uv":
            reached.append(cmd)
            raise AssertionError("test reached the REAL uv lookup without stubbing it")
        return real_which(cmd, *args, **kwargs)

    monkeypatch.setattr(retired_dists.shutil, "which", guarded)
    yield
    assert reached == [], "a test in this module resolved uv through the real PATH"


# ---------- helpers ----------


class _Spy:
    """Record the call, then raise — so a leaked call is both blocked and provable.

    Raising alone is not enough: ``prune_retired`` swallows ``BaseException`` by
    design, so an assertion raised from inside it would vanish. The recorded
    ``calls`` list survives the swallow and is what the tests assert on.
    """

    def __init__(self, label: str) -> None:
        self.label = label
        self.calls: list[tuple[tuple, dict]] = []

    def __call__(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN204
        self.calls.append((args, kwargs))
        raise AssertionError(f"{self.label} must not be called")


def _activate_prune(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop the session-wide kill switch so the prune actually runs.

    tests/conftest.py sets ``ENV_SKIP_PRUNE=1`` session-autouse. Without this
    call every "the prune did nothing" assertion below would pass for the wrong
    reason.
    """
    monkeypatch.delenv(retired_dists.ENV_SKIP_PRUNE, raising=False)


def _not_editable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend the install is not editable, so the prune proceeds.

    THIS checkout is an editable install, and :func:`prune_retired` stands down on
    those — without this, every prune-active test would pass for the wrong reason.
    """
    monkeypatch.setattr("ai_hats.paths.editable_install_root", lambda _dist="ai-hats": None)


def _block_subprocess(monkeypatch: pytest.MonkeyPatch) -> _Spy:
    spy = _Spy("subprocess.run")
    monkeypatch.setattr(retired_dists.subprocess, "run", spy)
    return spy


def _block_which(monkeypatch: pytest.MonkeyPatch) -> _Spy:
    spy = _Spy("shutil.which")
    monkeypatch.setattr(retired_dists.shutil, "which", spy)
    return spy


def _fake_uv(monkeypatch: pytest.MonkeyPatch, path: str = "/nonexistent/bin/uv") -> None:
    """Pretend ``uv`` is on PATH without ever letting a real one be found."""
    monkeypatch.setattr(retired_dists.shutil, "which", lambda name: path if name == "uv" else None)


def _declares(monkeypatch: pytest.MonkeyPatch, *dists: str) -> None:
    monkeypatch.setattr(
        retired_dists,
        "expected_runtime_deps",
        lambda: [(d, d.replace("-", "_")) for d in dists],
    )


def _installed(monkeypatch: pytest.MonkeyPatch, value: bool, *names: str) -> None:
    """Answer ``value`` for ``names`` only — by default the one name this suite drives.

    Scoped rather than blanket: the retired set holds more than one entry
    (HATS-1826 added the folded surface dists), and a blanket "everything is
    installed" would add uninstalls no assertion here is about.
    """
    targets = set(names) or {RETIRED_NAME}
    monkeypatch.setattr(retired_dists, "_is_installed", lambda name: value and name in targets)


def _must_not_raise(label: str, fn, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
    """Call ``fn``, turning ANY escaping ``BaseException`` into a named failure.

    Needed because the escapees under test are ``BaseException``: a leaked
    ``KeyboardInterrupt`` would otherwise abort the whole pytest session instead
    of failing the single test that pins the guarantee.
    """
    try:
        return fn(*args, **kwargs)
    except BaseException as exc:  # noqa: BLE001 - catching it IS the assertion
        pytest.fail(f"{label} let {type(exc).__name__} escape: {exc!r}", pytrace=False)


def _fake_run(returncode: int = 0) -> tuple:
    """A ``subprocess.run`` stand-in plus the list it records its kwargs into."""
    calls: list[tuple[tuple, dict]] = []

    def run(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(
            args=args[0], returncode=returncode, stdout="", stderr=""
        )

    return run, calls


def _fake_venv(root: Path, *scripts: str) -> Path:
    venv = root / ".venv"
    (venv / "bin").mkdir(parents=True)
    for script in scripts:
        (venv / "bin" / script).write_text("#!/bin/sh\nexit 0\n")
    return venv


# ---------- the pinned inventory ----------


def test_retired_set_pins_the_name_this_suite_drives():
    """Every assertion below names ``ai-hats-tracker``; fail loudly if it moved."""
    assert RETIRED_NAME in retired_dists.RETIRED_DISTRIBUTIONS
    assert RETIRED_SCRIPT in retired_dists.RETIRED_DISTRIBUTIONS[RETIRED_NAME]


def test_the_folded_surface_dists_are_retired():
    """HATS-1826 folded the surfaces into ai-hats; an upgrade must drop the leftovers.

    ``ai-hats-agy`` shipped the ``ai-hats-hook-dispatcher`` console script, so the
    legacy venv needs that name stripped too; ``ai-hats-cline`` shipped none.
    ``ai-hats-codex`` and ``ai-hats-opencode`` never published (the index answers
    404), so no venv can be carrying them and listing them would be noise.
    """
    assert retired_dists.RETIRED_DISTRIBUTIONS["ai-hats-agy"] == ("ai-hats-hook-dispatcher",)
    assert retired_dists.RETIRED_DISTRIBUTIONS["ai-hats-cline"] == ()
    assert "ai-hats-codex" not in retired_dists.RETIRED_DISTRIBUTIONS
    assert "ai-hats-opencode" not in retired_dists.RETIRED_DISTRIBUTIONS


# ---------- T1: kill switch ----------


def test_t1_kill_switch_returns_empty_and_runs_no_subprocess(monkeypatch, tmp_path):
    """``ENV_SKIP_PRUNE`` short-circuits before any process is spawned.

    ``_not_editable`` is what gives the assertions teeth: the editable exemption
    is the very next early return, so without it deleting the kill switch would
    still produce ``[]`` and an untouched spy.
    """
    monkeypatch.setenv(retired_dists.ENV_SKIP_PRUNE, "1")
    _not_editable(monkeypatch)
    run_spy = _block_subprocess(monkeypatch)
    which_spy = _block_which(monkeypatch)
    # would otherwise be a prime uninstall candidate
    _declares(monkeypatch, "click")
    _installed(monkeypatch, True)

    assert retired_dists.prune_retired(tmp_path) == []
    assert run_spy.calls == [], "kill switch did not stop subprocess.run"
    assert which_spy.calls == [], "kill switch did not stop the uv lookup"


def test_t1b_kill_switch_honours_any_truthy_value(monkeypatch, tmp_path):
    monkeypatch.setenv(retired_dists.ENV_SKIP_PRUNE, "0")  # non-empty string is truthy
    _not_editable(monkeypatch)
    run_spy = _block_subprocess(monkeypatch)
    which_spy = _block_which(monkeypatch)
    _declares(monkeypatch, "click")
    _installed(monkeypatch, True)

    assert retired_dists.prune_retired(tmp_path) == []
    assert run_spy.calls == []
    assert which_spy.calls == []


# ---------- T2: declared-dependency guard ----------


def test_t2_declared_dependency_is_never_uninstalled(monkeypatch):
    """A retired name that ai-hats STILL declares must survive untouched.

    The cherry-pick / ``--revision`` case: an install onto a ref whose metadata
    still requires the dist. Installed + in RETIRED_DISTRIBUTIONS + guard says
    "declared" → no uninstall.
    """
    _declares(monkeypatch, "click", RETIRED_NAME)
    _installed(monkeypatch, True)
    _fake_uv(monkeypatch)
    run_spy = _block_subprocess(monkeypatch)

    assert retired_dists._still_declared() >= {RETIRED_NAME}
    assert retired_dists.prune_running_interpreter() == []
    assert run_spy.calls == [], "a still-declared dependency reached uv pip uninstall"


def test_t2b_declared_guard_is_pep503_normalised(monkeypatch):
    """``AI_Hats_Tracker`` declared must shield ``ai-hats-tracker`` retired."""
    _declares(monkeypatch, "AI_Hats.Tracker")
    _installed(monkeypatch, True)
    _fake_uv(monkeypatch)
    run_spy = _block_subprocess(monkeypatch)

    assert retired_dists.prune_running_interpreter() == []
    assert run_spy.calls == []


def test_t2c_control_without_the_guard_the_uninstall_does_happen(monkeypatch):
    """Anti-vacuity control for T2/T3/T4: this setup DOES prune.

    Without it, "no uninstall happened" would be unfalsifiable — every one of
    those tests could pass because the code path is simply never reached.
    """
    _declares(monkeypatch, "click")  # tracker NOT declared
    _installed(monkeypatch, True)
    _fake_uv(monkeypatch)
    run, calls = _fake_run(returncode=0)
    monkeypatch.setattr(retired_dists.subprocess, "run", run)

    assert retired_dists.prune_running_interpreter() == [RETIRED_NAME]
    assert len(calls) == 1


# ---------- T3: a guard that crashes must prune nothing ----------


def test_t3_guard_failure_treats_everything_as_declared(monkeypatch):
    """``expected_runtime_deps`` raising → prune NOTHING, not prune everything."""

    def boom():
        raise RuntimeError("metadata unreadable")

    monkeypatch.setattr(retired_dists, "expected_runtime_deps", boom)
    _installed(monkeypatch, True)
    _fake_uv(monkeypatch)
    run_spy = _block_subprocess(monkeypatch)

    declared = retired_dists._still_declared()
    assert declared == {retired_dists._normalise(n) for n in retired_dists.RETIRED_DISTRIBUTIONS}
    assert retired_dists.prune_running_interpreter() == []
    assert run_spy.calls == [], "a crashed guard let the prune through"


def test_t3b_an_empty_declared_set_is_read_as_unreadable_not_as_nothing_declared(monkeypatch):
    """``expected_runtime_deps`` swallows a missing ai-hats and answers ``[]``.

    Indistinguishable from "declares nothing", so an empty answer must NOT mean
    "prune everything" — a PYTHONPATH source run would otherwise sweep the venv.
    """
    _declares(monkeypatch)  # → []
    _installed(monkeypatch, True)
    _fake_uv(monkeypatch)
    run_spy = _block_subprocess(monkeypatch)

    assert retired_dists._still_declared() == {
        retired_dists._normalise(n) for n in retired_dists.RETIRED_DISTRIBUTIONS
    }
    assert retired_dists.prune_running_interpreter() == []
    assert run_spy.calls == [], "an empty declared set let the prune through"


def test_t3c_own_dist_unreadable_is_read_as_unreadable(monkeypatch):
    """The probe is the point: a plausible dep list is not trusted if ai-hats
    itself cannot be resolved (clobbered dist-info, source run)."""

    def boom(name):
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(retired_dists.importlib.metadata, "distribution", boom)
    _declares(monkeypatch, "click")  # non-empty, tracker absent → would prune
    _installed(monkeypatch, True)
    _fake_uv(monkeypatch)
    run_spy = _block_subprocess(monkeypatch)

    assert retired_dists._still_declared() == {
        retired_dists._normalise(n) for n in retired_dists.RETIRED_DISTRIBUTIONS
    }
    assert retired_dists.prune_running_interpreter() == []
    assert run_spy.calls == [], "an unresolvable ai-hats let the prune through"


# ---------- T4: not installed → no cost ----------


def test_t4_not_installed_spawns_nothing(monkeypatch):
    """The steady state after the first upgrade: zero subprocesses, zero uv lookups."""
    _declares(monkeypatch, "click")
    _installed(monkeypatch, False)
    run_spy = _block_subprocess(monkeypatch)
    which_spy = _block_which(monkeypatch)

    assert retired_dists.prune_running_interpreter() == []
    assert run_spy.calls == []
    assert which_spy.calls == [], "uv was looked up for a dist that is not installed"


def test_t4b_is_installed_branches(monkeypatch):
    """``_is_installed``: present → True; not found → False; unreadable → False."""
    assert retired_dists._is_installed("ai-hats") is True
    assert retired_dists._is_installed("this-distribution-does-not-exist-1280") is False

    def boom(name):
        raise ValueError("corrupt dist-info")

    monkeypatch.setattr(retired_dists.importlib.metadata, "distribution", boom)
    assert retired_dists._is_installed("ai-hats") is False


# ---------- T5: no uv ----------


def test_t5_no_uv_on_path_returns_cleanly(monkeypatch):
    """``shutil.which`` → None must yield False, never a raise, never a call."""
    monkeypatch.setattr(retired_dists.shutil, "which", lambda name: None)
    run_spy = _block_subprocess(monkeypatch)

    assert retired_dists._uninstall(RETIRED_NAME, sys.executable) is False
    assert run_spy.calls == []

    _declares(monkeypatch, "click")
    _installed(monkeypatch, True)
    assert retired_dists.prune_running_interpreter() == []
    assert run_spy.calls == []


# ---------- T6: uv failed ----------


def test_t6_uv_non_zero_exit_is_not_removed(monkeypatch):
    _fake_uv(monkeypatch)
    run, calls = _fake_run(returncode=2)
    monkeypatch.setattr(retired_dists.subprocess, "run", run)

    assert retired_dists._uninstall(RETIRED_NAME, sys.executable) is False

    _declares(monkeypatch, "click")
    _installed(monkeypatch, True)
    assert retired_dists.prune_running_interpreter() == []
    assert len(calls) == 2, "uv was expected to be attempted and to fail"


# ---------- T7: the hang ----------


def test_t7_timeout_expired_is_swallowed(monkeypatch, tmp_path):
    """A wedged uv (it locks the target env) must not become an upgrade failure.

    ``TimeoutExpired`` only ever surfaces because ``subprocess.run`` is given a
    ``timeout`` (see T11) — a hang is the one failure mode ``except`` cannot
    cover on its own, so both halves are pinned separately.
    """
    _activate_prune(monkeypatch)
    _not_editable(monkeypatch)  # else the prune_retired leg below returns [] unexercised
    _fake_uv(monkeypatch)

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0] if args else ["uv"], timeout=1)

    monkeypatch.setattr(retired_dists.subprocess, "run", timeout)
    _declares(monkeypatch, "click")
    _installed(monkeypatch, True)

    assert (
        _must_not_raise("_uninstall", retired_dists._uninstall, RETIRED_NAME, sys.executable)
        is False
    )
    assert (
        _must_not_raise("prune_running_interpreter", retired_dists.prune_running_interpreter) == []
    )
    assert _must_not_raise("prune_retired", retired_dists.prune_retired, tmp_path) == []


# ---------- T8: BaseException ----------


def test_t8_keyboard_interrupt_from_uv_is_swallowed(monkeypatch):
    """A Ctrl-C landing in uv must not propagate out of the prune.

    ``KeyboardInterrupt`` is a ``BaseException``: this test is the reason
    ``_uninstall`` catches ``BaseException`` rather than ``Exception``, and a
    refactor to the narrower clause must fail HERE.
    """
    _fake_uv(monkeypatch)

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(retired_dists.subprocess, "run", interrupt)

    assert (
        _must_not_raise("_uninstall", retired_dists._uninstall, RETIRED_NAME, sys.executable)
        is False
    )

    _declares(monkeypatch, "click")
    _installed(monkeypatch, True)
    assert (
        _must_not_raise("prune_running_interpreter", retired_dists.prune_running_interpreter) == []
    )


def test_t8b_system_exit_from_uv_is_swallowed(monkeypatch):
    """The other common ``BaseException`` on the same path."""
    _fake_uv(monkeypatch)

    def bail(*args, **kwargs):
        raise SystemExit(1)

    monkeypatch.setattr(retired_dists.subprocess, "run", bail)
    assert (
        _must_not_raise("_uninstall", retired_dists._uninstall, RETIRED_NAME, sys.executable)
        is False
    )


# ---------- T9: console scripts in the legacy venv ----------


def test_t9_strips_retired_script_and_spares_the_rest(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_HATS_TRASH_DIR", str(tmp_path / "trash"))
    _declares(monkeypatch, "click")  # strip consults the same guard (T9h)
    venv = _fake_venv(tmp_path, RETIRED_SCRIPT, "ai-hats", "python")

    removed = retired_dists.strip_retired_scripts(venv, tmp_path)

    assert removed == [str(venv / "bin" / RETIRED_SCRIPT)]
    assert not (venv / "bin" / RETIRED_SCRIPT).exists()
    assert (venv / "bin" / "ai-hats").is_file(), "an unrelated console script was removed"
    assert (venv / "bin" / "python").is_file()


def test_t9b_strip_is_idempotent(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_HATS_TRASH_DIR", str(tmp_path / "trash"))
    _declares(monkeypatch, "click")
    venv = _fake_venv(tmp_path, RETIRED_SCRIPT)

    assert retired_dists.strip_retired_scripts(venv, tmp_path)
    assert retired_dists.strip_retired_scripts(venv, tmp_path) == []
    assert retired_dists.strip_retired_scripts(venv, tmp_path) == []


def test_t9c_strip_handles_a_broken_symlink(monkeypatch, tmp_path):
    """A dangling launcher symlink still counts — ``is_file()`` alone would miss it."""
    monkeypatch.setenv("AI_HATS_TRASH_DIR", str(tmp_path / "trash"))
    _declares(monkeypatch, "click")
    venv = _fake_venv(tmp_path)
    link = venv / "bin" / RETIRED_SCRIPT
    link.symlink_to(tmp_path / "gone" / "nowhere")

    removed = retired_dists.strip_retired_scripts(venv, tmp_path)

    assert removed == [str(link)]
    assert not link.is_symlink()


def test_t9d_strip_covers_the_windows_scripts_layout(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_HATS_TRASH_DIR", str(tmp_path / "trash"))
    _declares(monkeypatch, "click")
    venv = tmp_path / ".venv"
    (venv / "Scripts").mkdir(parents=True)
    exe = venv / "Scripts" / f"{RETIRED_SCRIPT}.exe"
    exe.write_bytes(b"MZ")

    assert retired_dists.strip_retired_scripts(venv, tmp_path) == [str(exe)]
    assert not exe.exists()


def test_t9e_missing_venv_dir_returns_empty_without_raising(monkeypatch, tmp_path):
    _declares(monkeypatch, "click")  # so [] means "no such path", not "shielded"
    assert retired_dists.strip_retired_scripts(tmp_path / "no-such-venv") == []
    assert retired_dists.strip_retired_scripts(tmp_path / "no-such-venv", tmp_path) == []


def test_t9f_unreadable_path_is_skipped_not_raised(monkeypatch, tmp_path):
    """``discard`` hitting EACCES must degrade to "nothing removed"."""
    import ai_hats_core.safe_delete as safe_delete

    _declares(monkeypatch, "click")
    venv = _fake_venv(tmp_path, RETIRED_SCRIPT)

    def denied(path, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(safe_delete, "discard", denied)

    assert retired_dists.strip_retired_scripts(venv, tmp_path) == []
    assert (venv / "bin" / RETIRED_SCRIPT).is_file()


def test_t9g_stat_failure_is_skipped_not_raised(monkeypatch, tmp_path):
    """An ``OSError`` from the existence probe itself is equally non-fatal."""
    _declares(monkeypatch, "click")
    venv = _fake_venv(tmp_path, RETIRED_SCRIPT)

    def denied(self):
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(Path, "is_file", denied)
    monkeypatch.setattr(Path, "is_symlink", denied)

    assert retired_dists.strip_retired_scripts(venv, tmp_path) == []


def test_t9h_a_still_declared_dist_keeps_its_script(monkeypatch, tmp_path):
    """The legacy-venv half honours the same declared-dependency guard.

    Anti-vacuity partner of T9: same venv, same script, only the guard differs.
    """
    monkeypatch.setenv("AI_HATS_TRASH_DIR", str(tmp_path / "trash"))
    _declares(monkeypatch, "click", RETIRED_NAME)
    venv = _fake_venv(tmp_path, RETIRED_SCRIPT)

    assert retired_dists.strip_retired_scripts(venv, tmp_path) == []
    assert (venv / "bin" / RETIRED_SCRIPT).is_file(), "a still-declared script was stripped"


# ---------- T10: prune_retired absorbs everything ----------


def test_t10_prune_retired_never_raises_on_internal_baseexception(monkeypatch, tmp_path):
    """Its caller is a bump whose exit code must not depend on this module.

    Both early returns must be off or the stub below is never reached and the
    ``except BaseException`` clause is never the reason this passes.
    """
    _activate_prune(monkeypatch)
    _not_editable(monkeypatch)

    def boom():
        raise KeyboardInterrupt

    monkeypatch.setattr(retired_dists, "prune_running_interpreter", boom)
    run_spy = _block_subprocess(monkeypatch)

    result = _must_not_raise("prune_retired", retired_dists.prune_retired, tmp_path)
    assert isinstance(result, list)
    assert result == []
    assert run_spy.calls == []


def test_t10b_prune_retired_keeps_partial_results_when_the_second_half_dies(monkeypatch, tmp_path):
    """What the interpreter half already removed is still reported."""
    _activate_prune(monkeypatch)
    _not_editable(monkeypatch)
    monkeypatch.setattr(retired_dists, "prune_running_interpreter", lambda: [RETIRED_NAME])

    def boom(*args, **kwargs):
        raise RuntimeError("legacy venv exploded")

    monkeypatch.setattr(retired_dists, "strip_retired_scripts", boom)
    legacy = ai_hats_dir(tmp_path) / ".venv"
    (legacy / "bin").mkdir(parents=True)
    _block_subprocess(monkeypatch)

    assert retired_dists.prune_retired(tmp_path) == [RETIRED_NAME]


def test_t10c_prune_retired_strips_the_legacy_venv_script(monkeypatch, tmp_path):
    """The happy path across both targets, with the running-interpreter half stubbed."""
    _activate_prune(monkeypatch)
    _not_editable(monkeypatch)
    monkeypatch.setenv("AI_HATS_TRASH_DIR", str(tmp_path / "trash"))
    monkeypatch.setattr(retired_dists, "prune_running_interpreter", lambda: [])
    run_spy = _block_subprocess(monkeypatch)

    legacy = ai_hats_dir(tmp_path) / ".venv"
    (legacy / "bin").mkdir(parents=True)
    script = legacy / "bin" / RETIRED_SCRIPT
    script.write_text("#!/bin/sh\n")
    keep = legacy / "bin" / "ai-hats"
    keep.write_text("#!/bin/sh\n")

    assert retired_dists.prune_retired(tmp_path) == [str(script)]
    assert not script.exists()
    assert keep.is_file()
    assert run_spy.calls == []


def test_t10d_no_legacy_venv_is_a_no_op(monkeypatch, tmp_path):
    """The second half is not even ATTEMPTED without a legacy venv.

    Asserting only the ``[]`` would be unfalsifiable: stripping a directory that
    does not exist also answers ``[]``, so the spy is what pins ``is_dir()``.
    """
    _activate_prune(monkeypatch)
    _not_editable(monkeypatch)
    monkeypatch.setattr(retired_dists, "prune_running_interpreter", lambda: [])
    strip_spy = _Spy("strip_retired_scripts")
    monkeypatch.setattr(retired_dists, "strip_retired_scripts", strip_spy)
    _block_subprocess(monkeypatch)

    assert retired_dists.prune_retired(tmp_path) == []
    assert strip_spy.calls == [], "the legacy-venv half ran without a legacy venv"


def test_t10e_legacy_venv_that_is_the_running_prefix_is_left_alone(monkeypatch, tmp_path):
    """Never strip the scripts of the interpreter currently executing.

    The ONLY guard on that property — so it must actually reach the comparison:
    without ``_not_editable`` the editable early return answers ``[]`` for it.
    """
    _activate_prune(monkeypatch)
    _not_editable(monkeypatch)
    # a working trash target too: a discard that merely FAILS would leave the
    # script in place and let a deleted prefix check pass unnoticed
    monkeypatch.setenv("AI_HATS_TRASH_DIR", str(tmp_path / "trash"))
    monkeypatch.setattr(retired_dists, "prune_running_interpreter", lambda: [])
    _block_subprocess(monkeypatch)

    legacy = ai_hats_dir(tmp_path) / ".venv"
    (legacy / "bin").mkdir(parents=True)
    script = legacy / "bin" / RETIRED_SCRIPT
    script.write_text("#!/bin/sh\n")
    monkeypatch.setattr(retired_dists.sys, "prefix", str(legacy))

    assert retired_dists.prune_retired(tmp_path) == []
    assert script.is_file(), "the running interpreter's own script was stripped"


# ---------- T11: the arguments that are invisible until production ----------


def test_t11_timeout_and_new_session_reach_subprocess_run(monkeypatch):
    """A missing ``timeout`` is undetectable until uv wedges an upgrade in the field.

    ``start_new_session`` rides along for the same reason: it keeps a terminal
    SIGINT off uv mid-write, and nothing else would ever notice its absence.
    """
    _fake_uv(monkeypatch)
    run, calls = _fake_run(returncode=0)
    monkeypatch.setattr(retired_dists.subprocess, "run", run)

    assert retired_dists._uninstall(RETIRED_NAME, sys.executable) is True

    assert len(calls) == 1
    _, kwargs = calls[0]
    assert "timeout" in kwargs, "subprocess.run was called WITHOUT a timeout — uv can hang forever"
    assert kwargs["timeout"] == retired_dists._UNINSTALL_TIMEOUT_S
    assert isinstance(kwargs["timeout"], (int, float)) and kwargs["timeout"] > 0
    assert kwargs.get("start_new_session") is True
    assert kwargs.get("capture_output") is True


def test_t11b_uninstall_targets_the_interpreter_it_was_given(monkeypatch):
    """``--python <exe>`` must be explicit — uv otherwise picks its own env."""
    _fake_uv(monkeypatch, "/nonexistent/bin/uv")
    run, calls = _fake_run(returncode=0)
    monkeypatch.setattr(retired_dists.subprocess, "run", run)

    retired_dists._uninstall(RETIRED_NAME, "/some/other/python")

    argv = calls[0][0][0]
    assert argv == [
        "/nonexistent/bin/uv",
        "pip",
        "uninstall",
        "--python",
        "/some/other/python",
        RETIRED_NAME,
    ]


def test_t11c_running_interpreter_prune_targets_sys_executable(monkeypatch):
    """The interpreter half must never uninstall out of a foreign env."""
    _declares(monkeypatch, "click")
    _installed(monkeypatch, True)
    _fake_uv(monkeypatch)
    run, calls = _fake_run(returncode=0)
    monkeypatch.setattr(retired_dists.subprocess, "run", run)

    assert retired_dists.prune_running_interpreter() == [RETIRED_NAME]
    assert calls[0][0][0][4] == sys.executable


# ---------- guard against the suite itself regressing ----------


def test_uv_is_spawned_only_through_the_patched_seam(monkeypatch):
    """The module resolves uv through ``shutil.which`` only — the seam the
    ``_no_real_uv`` fixture closes. If a refactor hard-codes a path or shells out
    another way, that default-deny stops covering the suite; catch it here rather
    than by watching a developer's venv lose a distribution.

    Named for what it checks: it greps the SOURCE. The guard over the TESTS is the
    ``_no_real_uv`` autouse fixture at the top of this file.
    """
    source = Path(retired_dists.__file__).read_text()
    assert 'shutil.which("uv")' in source, "uv is no longer resolved through the patched seam"
    assert "subprocess.run(" in source
    for escape_hatch in ("Popen", "check_call", "check_output", "os.system", "os.exec", "shell="):
        assert escape_hatch not in source, f"a second spawn path appeared: {escape_hatch}"


def test_importlib_metadata_is_reachable_for_patching():
    """``_is_installed`` must keep going through the module attribute (T4b patches it)."""
    assert retired_dists.importlib.metadata is importlib.metadata


# ---- editable installs are exempt (HATS-1280) ----


def test_editable_install_is_never_pruned(monkeypatch, tmp_path):
    """A dev checkout resolves packages/* as workspace members, so on a ref
    predating the retirement `uv sync` would reinstall what we removed — the two
    would fight on every update. The editable symptom is a broken script, not a
    working legacy CLI, so the prune stands down entirely.

    Everything else is staged so the prune WOULD uninstall: installed, not
    declared, uv resolvable. Only the editable verdict stops it — delete that
    guard and the spy records the call."""
    _activate_prune(monkeypatch)
    _declares(monkeypatch, "click")  # tracker NOT declared → a prime candidate
    _installed(monkeypatch, True)
    _fake_uv(monkeypatch)
    spy = _block_subprocess(monkeypatch)
    monkeypatch.setattr("ai_hats.paths.editable_install_root", lambda _d="ai-hats": tmp_path)

    assert retired_dists.prune_retired(tmp_path) == []
    assert spy.calls == [], "an editable install must not reach uv"


def test_non_editable_install_is_pruned(monkeypatch, tmp_path):
    """Anti-vacuity control for the test above: same setup, not editable → it runs."""
    _activate_prune(monkeypatch)
    _not_editable(monkeypatch)
    # a NON-EMPTY declared set is required: `_still_declared` reads an empty one
    # as "metadata unreadable" and conservatively shields everything (see T3b)
    _declares(monkeypatch, "click")
    _installed(monkeypatch, True)
    _fake_uv(monkeypatch)
    run, calls = _fake_run(0)
    monkeypatch.setattr(retired_dists.subprocess, "run", run)

    assert retired_dists.prune_retired(tmp_path) == ["ai-hats-tracker"]
    assert calls, "a non-editable install must reach uv — otherwise the guard test is vacuous"
