"""Unit tests for the runtime self-location guard (HATS-791).

Two surfaces:

1. :func:`ai_hats.self_location.classify_invocation` — the PURE sanctioned/
   foreign decision. Full truth table: every sanctioned shape (managed default
   ``.venv`` / versioned dir / editable host clone / env-pinned / yaml-pinned /
   unresolved / skip) → ``"sanctioned"``; a real foreign app-venv → ``"foreign"``.

2. A regression assertion that an in-process ``CliRunner`` invocation of the
   bare ``main`` click group does NOT trip the guard — the guard is wired into
   ``main_entry`` (the real ``python -m ai_hats`` path), NOT ``main``, so the
   whole CliRunner-driven suite bypasses it for free. If a future refactor
   moves the guard onto ``main``, this test fails before the suite does.

Fail-under-revert: drop the ``"foreign"`` branch in ``classify_invocation`` and
:func:`test_foreign_app_venv_is_foreign` flips to ``"sanctioned"``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from ai_hats.self_location import classify_invocation


# --------------------------------------------------------------------------
# classify_invocation — truth table
# --------------------------------------------------------------------------


def test_running_prefix_equals_resolved_is_sanctioned(tmp_path: Path):
    venv = tmp_path / "proj" / ".agent" / "ai-hats" / ".venv"
    venv.mkdir(parents=True)
    assert (
        classify_invocation(venv, venv, is_editable_install=False, skip=False)
        == "sanctioned"
    )


def test_managed_default_venv_namespace_is_sanctioned(tmp_path: Path):
    """Under <project>/.agent/ai-hats/.venv, even if resolved differs."""
    running = tmp_path / "proj" / ".agent" / "ai-hats" / ".venv"
    running.mkdir(parents=True)
    other = tmp_path / "elsewhere" / "venv"
    other.mkdir(parents=True)
    assert (
        classify_invocation(running, other, is_editable_install=False, skip=False)
        == "sanctioned"
    )


def test_managed_versioned_dir_is_sanctioned(tmp_path: Path):
    """Blue-green versions/<sha>/ (HATS-647) is a managed namespace."""
    running = tmp_path / "proj" / ".agent" / "ai-hats" / "versions" / "abc123"
    running.mkdir(parents=True)
    other = tmp_path / "proj" / ".agent" / "ai-hats" / ".venv"
    other.mkdir(parents=True)
    assert (
        classify_invocation(running, other, is_editable_install=False, skip=False)
        == "sanctioned"
    )


def test_editable_host_clone_is_sanctioned(tmp_path: Path):
    """PEP 610 editable host clone (channel:local dev checkout)."""
    running = tmp_path / "dev" / ".venv"  # NOT under .agent/ai-hats
    running.mkdir(parents=True)
    resolved = tmp_path / "proj" / ".agent" / "ai-hats" / ".venv"
    resolved.mkdir(parents=True)
    assert (
        classify_invocation(running, resolved, is_editable_install=True, skip=False)
        == "sanctioned"
    )


def test_env_pinned_resolved_matches_running_is_sanctioned(tmp_path: Path):
    """AI_HATS_VENV pin: caller passes the pin as resolved_venv; matches."""
    pinned = tmp_path / "ci-cache" / "shared-venv"
    pinned.mkdir(parents=True)
    assert (
        classify_invocation(pinned, pinned, is_editable_install=False, skip=False)
        == "sanctioned"
    )


def test_yaml_pinned_resolved_matches_running_is_sanctioned(tmp_path: Path):
    """yaml venv_path override resolving to the running venv → sanctioned."""
    venv = tmp_path / "custom" / "venv-path"
    venv.mkdir(parents=True)
    assert (
        classify_invocation(venv, venv, is_editable_install=False, skip=False)
        == "sanctioned"
    )


def test_unresolved_venv_is_sanctioned_fail_open(tmp_path: Path):
    """No project / resolution error → resolved_venv None → fail open."""
    running = tmp_path / "some" / "venv"
    running.mkdir(parents=True)
    assert (
        classify_invocation(running, None, is_editable_install=False, skip=False)
        == "sanctioned"
    )


def test_unknown_running_prefix_is_sanctioned(tmp_path: Path):
    """Unknown running interpreter → cannot judge → sanctioned."""
    resolved = tmp_path / "proj" / ".agent" / "ai-hats" / ".venv"
    resolved.mkdir(parents=True)
    assert (
        classify_invocation(None, resolved, is_editable_install=False, skip=False)
        == "sanctioned"
    )
    assert (
        classify_invocation("", resolved, is_editable_install=False, skip=False)
        == "sanctioned"
    )


def test_skip_env_overrides_foreign(tmp_path: Path):
    """skip=True (AI_HATS_SKIP_SELF_LOCATION_GUARD=1) sanctions even a shadow."""
    running = tmp_path / "appvenv"
    running.mkdir(parents=True)
    resolved = tmp_path / "proj" / ".agent" / "ai-hats" / ".venv"
    resolved.mkdir(parents=True)
    # Without skip this is foreign (see below); skip flips it.
    assert (
        classify_invocation(running, resolved, is_editable_install=False, skip=False)
        == "foreign"
    )
    assert (
        classify_invocation(running, resolved, is_editable_install=False, skip=True)
        == "sanctioned"
    )


def test_foreign_app_venv_is_foreign(tmp_path: Path):
    """The shadow case: a real venv that is neither managed, editable, nor the
    resolved venv → foreign. Fail-under-revert anchor."""
    running = tmp_path / "myapp" / ".venv"  # foreign app-venv
    running.mkdir(parents=True)
    resolved = tmp_path / "proj" / ".agent" / "ai-hats" / ".venv"
    resolved.mkdir(parents=True)
    assert (
        classify_invocation(running, resolved, is_editable_install=False, skip=False)
        == "foreign"
    )


# --------------------------------------------------------------------------
# CliRunner regression — the bare `main` group bypasses the guard.
# --------------------------------------------------------------------------


def test_cli_runner_invocation_of_main_does_not_trip_guard(monkeypatch, tmp_path):
    """In-process ``CliRunner`` drives ``main`` (not ``main_entry``), so the
    HATS-791 guard must NOT fire — even when sys.prefix would look foreign.

    Guards against a future refactor moving ``_guard_self_location`` onto the
    bare ``main`` group, which would break every CliRunner-driven test.
    """
    from ai_hats.cli import main

    # Make the would-be guard verdict 'foreign' if it were ever consulted on
    # this path: a foreign-looking sys.prefix and a resolved managed venv.
    foreign = tmp_path / "appvenv"
    foreign.mkdir()
    monkeypatch.setattr("sys.prefix", str(foreign))
    monkeypatch.delenv("AI_HATS_SKIP_SELF_LOCATION_GUARD", raising=False)
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    # `--help` exits cleanly through the group without launching anything; if
    # the guard fired it would sys.exit(3) and print the refusal text.
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0, result.output
    assert "refusing to run from a foreign" not in result.output


@pytest.mark.parametrize("skip_val", ["0", "true", "yes", ""])
def test_skip_env_only_honours_exact_1(skip_val: str, tmp_path: Path):
    """Wiring contract: only the literal '1' disables the guard. (Mirrors the
    main_entry wiring which compares os.environ.get(...) == '1'.)"""
    # classify_invocation takes a bool; this documents that anything other than
    # the exact '1' should map to skip=False at the wiring layer. Here we just
    # assert the bool semantics directly.
    running = tmp_path / "appvenv"
    running.mkdir()
    resolved = tmp_path / "proj" / ".agent" / "ai-hats" / ".venv"
    resolved.mkdir(parents=True)
    skip = skip_val == "1"  # the wiring's exact comparison
    expected = "sanctioned" if skip else "foreign"
    assert (
        classify_invocation(running, resolved, is_editable_install=False, skip=skip)
        == expected
    )
