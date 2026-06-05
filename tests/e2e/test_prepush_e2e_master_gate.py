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
import shlex
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

    The stub writes its argv to ``bindir/last_argv`` (so cases can assert
    whether pytest was invoked at all) and the value of
    ``AI_HATS_E2E_REQUIRE_VENV`` to ``bindir/last_require_venv`` (HATS-645 — so
    cases can assert the gate armed the tier-2 fail-closed env).
    """
    bindir.mkdir(parents=True, exist_ok=True)
    stub = bindir / "pytest"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$@" > "{bindir}/last_argv"\n'
        f'printf "%s" "${{AI_HATS_E2E_REQUIRE_VENV:-<unset>}}" > "{bindir}/last_require_venv"\n'
        f"exit {exit_code}\n"
    )
    stub.chmod(0o755)
    return stub


def _make_pytest_stub_emitting(bindir: Path, exit_code: int, message: str) -> Path:
    """Like :func:`_make_pytest_stub` but also prints ``message`` to stdout.

    The gate captures ``output=$(pytest ... 2>&1)`` and conditionally prints the
    fail-closed explanation when ``$output`` mentions ``AI_HATS_E2E_REQUIRE_VENV``
    (HATS-645). This stub lets a case feed the gate a controlled ``$output`` so
    that conditional can be exercised without a real venv-tier failure.
    """
    bindir.mkdir(parents=True, exist_ok=True)
    stub = bindir / "pytest"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$@" > "{bindir}/last_argv"\n'
        f"printf '%s\\n' {shlex.quote(message)}\n"
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
def test_master_push_deselects_quarantined_tests(tmp_path: Path):
    """HATS-676: the gate filter subtracts ``@pytest.mark.quarantine`` tests via
    ``-m "(integration or smoke) and not quarantine"``.

    The quarantine marker is a concrete HYP-002 known-flaky registry — a handful
    of stateful real-pip / shared-venv e2e tests fail intermittently under the
    gate's ``-n8 --dist=loadgroup`` contention (a different one each run) and
    would block clean master pushes despite a sound diff. They still run under a
    normal/solo ``pytest`` invocation, so dev coverage is not lost.

    Fail-under-revert: drop ``and not quarantine`` from the hook → the recorded
    marker expression loses the clause → this assertion fails.
    """
    bindir = tmp_path / "bin"
    _make_pytest_stub(bindir, exit_code=0)
    stdin = f"refs/heads/master {NEW_SHA} refs/heads/master {OLD_SHA}\n"

    res = _run_hook(stdin, bindir)

    assert res.returncode == 0, res.stderr
    argv = (bindir / "last_argv").read_text()
    # Keep the integration/smoke selection AND subtract the quarantine set.
    assert "not quarantine" in argv, (
        f"gate must deselect quarantined known-flaky tests; argv:\n{argv}"
    )
    assert "integration or smoke" in argv, argv


@pytest.mark.integration
def test_master_push_arms_require_venv_strict_mode(tmp_path: Path):
    """HATS-645: a master push exports ``AI_HATS_E2E_REQUIRE_VENV=1`` into the
    pytest child env, arming the tier-2 venv fixture's fail-closed mode.

    Without this the venv fixture *skips* (not fails) when it cannot build its
    venv offline, pytest exits 0, and the gate green-lights a push whose tier-2
    e2e never ran — the exact false-green that shipped master with real
    failures. Fail-under-revert: drop the ``export`` from the hook → the stub
    records ``<unset>`` → this assertion fails.
    """
    bindir = tmp_path / "bin"
    _make_pytest_stub(bindir, exit_code=0)
    stdin = f"refs/heads/master {NEW_SHA} refs/heads/master {OLD_SHA}\n"

    res = _run_hook(stdin, bindir)

    assert res.returncode == 0, res.stderr
    captured = (bindir / "last_require_venv").read_text()
    assert captured == "1", (
        "gate must export AI_HATS_E2E_REQUIRE_VENV=1 so the venv tier "
        f"fails-closed; stub saw {captured!r}"
    )


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
def test_master_push_explains_fail_closed_venv_skip(tmp_path: Path):
    """HATS-645: when the failure output mentions AI_HATS_E2E_REQUIRE_VENV, the
    gate prints the explicit FAIL-CLOSED explanation (why blocked + how to fix).

    Drives the conditional ``if echo "$output" | grep -q AI_HATS_E2E_REQUIRE_VENV``
    branch. Fail-under-revert: drop that branch → the explanation vanishes → red.
    """
    bindir = tmp_path / "bin"
    _make_pytest_stub_emitting(
        bindir, exit_code=1,
        message="E venv-tier required (AI_HATS_E2E_REQUIRE_VENV=1) but unavailable",
    )
    stdin = f"refs/heads/master {NEW_SHA} refs/heads/master {OLD_SHA}\n"

    res = _run_hook(stdin, bindir)

    assert res.returncode == 1
    assert "BLOCKED" in res.stderr
    assert "FAIL-CLOSED venv-tier skip (HATS-645)" in res.stderr, res.stderr


@pytest.mark.integration
def test_master_push_generic_failure_omits_fail_closed_explanation(tmp_path: Path):
    """A generic test failure (output WITHOUT the env-var marker) must block but
    NOT print the venv-specific FAIL-CLOSED explanation — only the generic footer.

    Pins the conditional so the explanation can't regress into firing on every
    failure (which would be misleading noise on unrelated test breakage).
    """
    bindir = tmp_path / "bin"
    _make_pytest_stub_emitting(
        bindir, exit_code=1, message="E   assert 1 == 2  # an unrelated test bug",
    )
    stdin = f"refs/heads/master {NEW_SHA} refs/heads/master {OLD_SHA}\n"

    res = _run_hook(stdin, bindir)

    assert res.returncode == 1
    assert "BLOCKED" in res.stderr
    assert "FAIL-CLOSED venv-tier skip" not in res.stderr, res.stderr
    # The generic footer still guides the contributor.
    assert "git push --no-verify" in res.stderr


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


# --- HATS-589: xdist opt-in vs serial fallback ----------------------------


def _make_xdist_aware_stub(bindir: Path, *, has_xdist: bool) -> Path:
    """Fake ``pytest`` whose ``-VV`` banner advertises (or hides) xdist.

    The hook probes ``pytest -VV | grep -qi xdist`` to decide whether to
    add ``-n<N> --dist=loadgroup``. This stub answers that probe, and on the
    real run records argv to ``bindir/last_argv`` and exits 0.
    """
    bindir.mkdir(parents=True, exist_ok=True)
    stub = bindir / "pytest"
    banner = "plugins: xdist-3.8.0, cov-4.0\n" if has_xdist else "plugins: cov-4.0\n"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$1" == "-VV" ]]; then\n'
        f'  printf "%s" "{banner}"\n'
        "  exit 0\n"
        "fi\n"
        f'printf "%s\\n" "$@" > "{bindir}/last_argv"\n'
        "exit 0\n"
    )
    stub.chmod(0o755)
    return stub


def _xdist_n(argv: str) -> int | None:
    """Return N from the ``-nN`` worker-count flag in recorded argv, or None.

    The pytest stub records each arg on its own line, so the worker-count
    flag appears as a standalone ``-n<digits>`` token.
    """
    for line in argv.splitlines():
        if line.startswith("-n") and line[2:].isdigit():
            return int(line[2:])
    return None


def _make_getconf_stub(bindir: Path, count: int) -> Path:
    """Shim ``getconf`` so ``_NPROCESSORS_ONLN`` reports a fixed core count.

    Lets the cap test force a high core count deterministically without
    depending on the host's real CPU count. Other ``getconf`` queries
    delegate to the real binary.
    """
    bindir.mkdir(parents=True, exist_ok=True)
    stub = bindir / "getconf"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$1" == "_NPROCESSORS_ONLN" ]]; then\n'
        f"  echo {count}\n"
        "  exit 0\n"
        "fi\n"
        'exec /usr/bin/getconf "$@"\n'
    )
    stub.chmod(0o755)
    return stub


@pytest.mark.integration
def test_master_push_uses_xdist_when_available(tmp_path: Path):
    """xdist present → gate runs ``-n<N> --dist=loadgroup`` with an adaptive,
    host-derived worker count (HATS-589, HATS-592)."""
    bindir = tmp_path / "bin"
    _make_xdist_aware_stub(bindir, has_xdist=True)
    stdin = f"refs/heads/master {NEW_SHA} refs/heads/master {OLD_SHA}\n"

    res = _run_hook(stdin, bindir)

    assert res.returncode == 0, res.stderr
    argv = (bindir / "last_argv").read_text()
    # Count is host-adaptive (min(cores, ceiling)), not a literal -n4.
    n = _xdist_n(argv)
    assert n is not None and n >= 1, argv
    assert "--dist=loadgroup" in argv, argv
    assert "integration or smoke" in argv


@pytest.mark.integration
def test_master_push_caps_worker_count_to_ceiling(tmp_path: Path):
    """A many-core host is capped at the ceiling, not handed ``-n<cores>``
    (HATS-592). Proves the ``min(cores, ceiling)`` cap, not just "some -n"."""
    bindir = tmp_path / "bin"
    _make_xdist_aware_stub(bindir, has_xdist=True)
    _make_getconf_stub(bindir, count=64)
    stdin = f"refs/heads/master {NEW_SHA} refs/heads/master {OLD_SHA}\n"

    res = _run_hook(stdin, bindir)

    assert res.returncode == 0, res.stderr
    argv = (bindir / "last_argv").read_text()
    # 64 cores capped down to the ceiling (8), never -n64.
    assert _xdist_n(argv) == 8, argv
    assert "--dist=loadgroup" in argv, argv


@pytest.mark.integration
def test_master_push_falls_back_to_serial_without_xdist(tmp_path: Path):
    """xdist absent → gate runs serial (no ``-n`` flag), still green (HATS-589)."""
    bindir = tmp_path / "bin"
    _make_xdist_aware_stub(bindir, has_xdist=False)
    stdin = f"refs/heads/master {NEW_SHA} refs/heads/master {OLD_SHA}\n"

    res = _run_hook(stdin, bindir)

    assert res.returncode == 0, res.stderr
    argv = (bindir / "last_argv").read_text()
    assert _xdist_n(argv) is None, argv
    assert "--dist" not in argv, argv
    assert "integration or smoke" in argv
