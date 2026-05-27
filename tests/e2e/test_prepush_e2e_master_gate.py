"""HATS-550 — end-to-end behaviour of the master-push e2e+smoke gate.

Per ``dev_rule_e2e_gate``: this script is pure bash that no in-process
unit test can meaningfully exercise. Each case spawns ``bash <hook>`` as
a real subprocess and feeds it the standard pre-push stdin protocol.

We never invoke the real ``pytest -m "integration or smoke"`` inside
these tests — that would turn each case into an exponential-blowup
re-run of the whole gated suite. Instead we stub ``pytest`` on PATH
with a tiny shell script whose exit code we control, and assert the
hook's branching on stdin shape + child exit code.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HOOK = (
    REPO_ROOT
    / "library/usage/skills/maintainer-quality-gate"
    / "git_hooks/pre-push-e2e-master.sh"
)
ZERO = "0" * 40
NEW_SHA = "1" * 40
OLD_SHA = "2" * 40


def _make_pytest_stub(bindir: Path, exit_code: int) -> Path:
    """Write a fake ``pytest`` on PATH that records argv and exits with ``exit_code``.

    The stub also writes its argv to ``bindir/last_argv`` so cases can
    assert whether pytest was invoked at all.
    """
    bindir.mkdir(parents=True, exist_ok=True)
    stub = bindir / "pytest"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$@" > "{bindir}/last_argv"\n'
        f"exit {exit_code}\n"
    )
    stub.chmod(0o755)
    return stub


def _run_hook(
    stdin: str,
    bindir: Path | None,
    *,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the hook with a controlled PATH (only ``bindir`` + git).

    ``bindir=None`` simulates "pytest missing from PATH". We still need
    ``git`` available — symlink it in from the real PATH.
    """
    env = {
        "PATH": "/usr/bin:/bin",  # baseline so `git rev-parse` works
        "HOME": os.environ.get("HOME", "/tmp"),
    }
    if bindir is not None:
        env["PATH"] = f"{bindir}:{env['PATH']}"
    return subprocess.run(
        ["bash", str(HOOK)],
        input=stdin,
        cwd=str(cwd) if cwd else str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=15,
        env=env,
    )


# --- Fast-path: non-master targets, deletions, empty stdin -----------------


@pytest.mark.integration
def test_non_master_target_is_noop(tmp_path: Path):
    """Push to a feature branch must not invoke pytest at all."""
    bindir = tmp_path / "bin"
    _make_pytest_stub(bindir, exit_code=99)  # would fail loudly if called
    stdin = f"refs/heads/feature/foo {NEW_SHA} refs/heads/feature/foo {OLD_SHA}\n"

    res = _run_hook(stdin, bindir)

    assert res.returncode == 0, res.stderr
    assert not (bindir / "last_argv").exists(), "pytest must not be invoked"


@pytest.mark.integration
def test_master_deletion_is_noop(tmp_path: Path):
    """Deleting master (local_sha = 0*40) must not invoke pytest."""
    bindir = tmp_path / "bin"
    _make_pytest_stub(bindir, exit_code=99)
    stdin = f"refs/heads/master {ZERO} refs/heads/master {OLD_SHA}\n"

    res = _run_hook(stdin, bindir)

    assert res.returncode == 0, res.stderr
    assert not (bindir / "last_argv").exists(), "pytest must not be invoked"


@pytest.mark.integration
def test_empty_stdin_is_noop(tmp_path: Path):
    """Empty pre-push payload (rare but possible) must exit 0."""
    bindir = tmp_path / "bin"
    _make_pytest_stub(bindir, exit_code=99)

    res = _run_hook("", bindir)

    assert res.returncode == 0, res.stderr
    assert not (bindir / "last_argv").exists(), "pytest must not be invoked"


# --- Trigger path: pytest exit codes --------------------------------------


@pytest.mark.integration
def test_master_push_invokes_pytest_and_passes(tmp_path: Path):
    """Master push + pytest exit 0 → allow push, pytest WAS invoked."""
    bindir = tmp_path / "bin"
    _make_pytest_stub(bindir, exit_code=0)
    stdin = f"refs/heads/master {NEW_SHA} refs/heads/master {OLD_SHA}\n"

    res = _run_hook(stdin, bindir)

    assert res.returncode == 0, res.stderr
    argv_path = bindir / "last_argv"
    assert argv_path.exists(), "pytest was not invoked on master push"
    argv = argv_path.read_text()
    # Both markers and both folders must be in argv.
    assert "integration or smoke" in argv
    assert "tests/e2e/" in argv
    assert "tests/smoke/" in argv


@pytest.mark.integration
def test_master_push_blocks_on_pytest_failure(tmp_path: Path):
    """Master push + pytest exit 1 → block with stderr tail."""
    bindir = tmp_path / "bin"
    _make_pytest_stub(bindir, exit_code=1)
    stdin = f"refs/heads/master {NEW_SHA} refs/heads/master {OLD_SHA}\n"

    res = _run_hook(stdin, bindir)

    assert res.returncode == 1
    assert "BLOCKED" in res.stderr
    assert "rc=1" in res.stderr


@pytest.mark.integration
def test_master_push_passes_when_no_tests_collected(tmp_path: Path):
    """pytest rc=5 ("no tests collected") → defensive allow, not block.

    Justification: a renamed marker or empty folder must not permanently
    brick ``git push origin master``.
    """
    bindir = tmp_path / "bin"
    _make_pytest_stub(bindir, exit_code=5)
    stdin = f"refs/heads/master {NEW_SHA} refs/heads/master {OLD_SHA}\n"

    res = _run_hook(stdin, bindir)

    assert res.returncode == 0, res.stderr
    assert "no tests collected" in res.stderr


@pytest.mark.integration
def test_master_push_blocks_when_pytest_missing(tmp_path: Path):
    """pytest absent from PATH + master push → BLOCK (no silent skip).

    Without this, ``pip uninstall pytest`` would silently bypass the gate.
    """
    # No bindir → PATH lacks pytest entirely.
    stdin = f"refs/heads/master {NEW_SHA} refs/heads/master {OLD_SHA}\n"

    res = _run_hook(stdin, bindir=None)

    assert res.returncode == 1
    assert "pytest not found" in res.stderr
    assert "BLOCKED" in res.stderr


# --- Mixed payload --------------------------------------------------------


@pytest.mark.integration
def test_mixed_payload_with_master_triggers(tmp_path: Path):
    """If ANY line targets master, the gate fires — even if others don't."""
    bindir = tmp_path / "bin"
    _make_pytest_stub(bindir, exit_code=0)
    stdin = (
        f"refs/heads/feature/foo {NEW_SHA} refs/heads/feature/foo {OLD_SHA}\n"
        f"refs/heads/master {NEW_SHA} refs/heads/master {OLD_SHA}\n"
    )

    res = _run_hook(stdin, bindir)

    assert res.returncode == 0, res.stderr
    assert (bindir / "last_argv").exists(), "pytest should be invoked"
