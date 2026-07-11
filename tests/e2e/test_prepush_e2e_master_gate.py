"""HATS-550 / HATS-686 — end-to-end behaviour of the master-push e2e gate.

Per ``dev_rule_e2e_gate``: this hook is pure bash that no in-process unit
test can meaningfully exercise. Each case spawns ``bash <hook>`` as a real
subprocess.

HATS-686 split the gate into two modes (the slow suite must not run inside
pre-push while git holds the GitHub SSH connection — it gets killed at ~30s):

* **CHECK MODE** (default — git pre-push protocol on stdin): instant marker
  lookup. A master push is allowed iff a green pass-marker keyed to the
  pushed ``local_sha`` exists under ``<git-common-dir>/ai-hats/e2e-gate/``.
* **RUN MODE** (``--run``): runs the real suite and, on pass + clean tree,
  writes the marker keyed to ``git rev-parse HEAD``.

We never invoke the real gated suite here — we stub ``pytest`` on PATH with a
tiny shell script whose exit code we control, and assert the hook's branching
on stdin shape / argv / child exit code / marker side effects.
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
    / "packages/ai-hats-library/src/ai_hats_library/usage/skills/maintainer-quality-gate"
    / "git_hooks/pre-push-e2e-master.sh"
)
WRAPPER = REPO_ROOT / "scripts" / "run-e2e-gate.sh"
ZERO = "0" * 40
NEW_SHA = "1" * 40
OLD_SHA = "2" * 40
OTHER_SHA = "3" * 40


# --- pytest stubs ----------------------------------------------------------


def _make_pytest_stub(bindir: Path, exit_code: int) -> Path:
    """Write a fake ``pytest`` on PATH that records argv and exits with ``exit_code``.

    The stub writes its argv to ``bindir/last_argv`` (so cases can assert
    whether pytest was invoked at all) and the value of
    ``AI_HATS_E2E_REQUIRE_VENV`` to ``bindir/last_require_venv`` (HATS-645).
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

    Lets a case feed the gate a controlled ``$output`` so the HATS-645
    fail-closed conditional can be exercised without a real venv-tier failure.
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


def _make_xdist_aware_stub(bindir: Path, *, has_xdist: bool) -> Path:
    """Fake ``pytest`` whose ``-VV`` banner advertises (or hides) xdist.

    The hook probes ``pytest -VV | grep -qi xdist`` to decide whether to add
    ``-n<N> --dist=loadgroup``. This stub answers that probe, and on the real
    run records argv to ``bindir/last_argv`` and exits 0.
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


def _make_getconf_stub(bindir: Path, count: int) -> Path:
    """Shim ``getconf`` so ``_NPROCESSORS_ONLN`` reports a fixed core count."""
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


def _xdist_n(argv: str) -> int | None:
    """Return N from the ``-nN`` worker-count flag in recorded argv, or None."""
    for line in argv.splitlines():
        if line.startswith("-n") and line[2:].isdigit():
            return int(line[2:])
    return None


# --- temp git repo + marker helpers (HATS-686) -----------------------------


def _git_repo(tmp_path: Path) -> Path:
    """Create a throwaway git repo with one commit (controlled HEAD + tree)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    # HATS-887: strip GIT_* (plumbing) then re-pin config isolation only.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_CONFIG_SYSTEM"] = "/dev/null"
    run = lambda *a: subprocess.run(  # noqa: E731
        ["git", *a], cwd=repo, check=True, capture_output=True, text=True, env=env
    )
    run("init", "-q")
    run("config", "user.email", "t@t")
    run("config", "user.name", "t")
    (repo / "f").write_text("x")
    run("add", "-A")
    run("commit", "-qm", "init")
    return repo


def _marker_dir(repo: Path) -> Path:
    return repo / ".git" / "ai-hats" / "e2e-gate"


def _write_marker(repo: Path, sha: str) -> Path:
    md = _marker_dir(repo)
    md.mkdir(parents=True, exist_ok=True)
    p = md / sha
    p.write_text(f"sha={sha}\ntimestamp=2026-01-01T00:00:00Z\npytest_rc=0\n")
    return p


def _head(repo: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


def _env(bindir: Path | None) -> dict[str, str]:
    env = {
        "PATH": "/usr/bin:/bin",  # baseline so `git` / `getconf` / `date` work
        "HOME": os.environ.get("HOME", "/tmp"),
    }
    if bindir is not None:
        env["PATH"] = f"{bindir}:{env['PATH']}"
    return env


def _check(stdin: str, bindir: Path | None, *, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run the hook in CHECK mode (git pre-push protocol on stdin)."""
    return subprocess.run(
        ["bash", str(HOOK)],
        input=stdin,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=20,
        env=_env(bindir),
    )


def _run(
    bindir: Path | None,
    *,
    cwd: Path,
    extra: tuple[str, ...] = (),
    env_extra: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the hook in RUN mode (``--run``)."""
    env = _env(bindir)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["bash", str(HOOK), "--run", *extra],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )


# ===========================================================================
# CHECK MODE — fast-path no-ops (unchanged contract, no marker, no pytest)
# ===========================================================================


@pytest.mark.integration
def test_non_master_target_is_noop(tmp_path: Path):
    """Push to a feature branch must not read markers nor invoke pytest."""
    repo = _git_repo(tmp_path)
    bindir = tmp_path / "bin"
    _make_pytest_stub(bindir, exit_code=99)  # would fail loudly if called
    stdin = f"refs/heads/feature/foo {NEW_SHA} refs/heads/feature/foo {OLD_SHA}\n"

    res = _check(stdin, bindir, cwd=repo)

    assert res.returncode == 0, res.stderr
    assert not (bindir / "last_argv").exists(), "pytest must not be invoked"


@pytest.mark.integration
def test_master_deletion_is_noop(tmp_path: Path):
    """Deleting master (local_sha = 0*40) must not block on a marker."""
    repo = _git_repo(tmp_path)
    bindir = tmp_path / "bin"
    _make_pytest_stub(bindir, exit_code=99)
    stdin = f"refs/heads/master {ZERO} refs/heads/master {OLD_SHA}\n"

    res = _check(stdin, bindir, cwd=repo)

    assert res.returncode == 0, res.stderr
    assert not (bindir / "last_argv").exists(), "pytest must not be invoked"


@pytest.mark.integration
def test_empty_stdin_is_noop(tmp_path: Path):
    """Empty pre-push payload (rare but possible) must exit 0."""
    repo = _git_repo(tmp_path)
    bindir = tmp_path / "bin"
    _make_pytest_stub(bindir, exit_code=99)

    res = _check("", bindir, cwd=repo)

    assert res.returncode == 0, res.stderr
    assert not (bindir / "last_argv").exists(), "pytest must not be invoked"


# ===========================================================================
# CHECK MODE — marker lookup (HATS-686 core)
# ===========================================================================


@pytest.mark.integration
def test_master_push_allowed_with_valid_marker(tmp_path: Path):
    """A green marker for the pushed local_sha → allow, pytest NOT invoked.

    Fail-under-revert: if the hook reverted to running pytest in-line (the
    old HATS-550 behaviour), the tripwire stub (exit 99) would be invoked
    and ``last_argv`` would exist → this assertion fails.
    """
    repo = _git_repo(tmp_path)
    bindir = tmp_path / "bin"
    _make_pytest_stub(bindir, exit_code=99)  # tripwire: must NOT run
    _write_marker(repo, NEW_SHA)
    stdin = f"refs/heads/master {NEW_SHA} refs/heads/master {OLD_SHA}\n"

    res = _check(stdin, bindir, cwd=repo)

    assert res.returncode == 0, res.stderr
    assert not (bindir / "last_argv").exists(), "pytest must not be invoked in check mode"


@pytest.mark.integration
def test_master_push_blocked_without_marker(tmp_path: Path):
    """No marker for the pushed sha → BLOCK with the run command, no pytest.

    Fail-under-revert: drop the "block when marker absent" branch → exit 0.
    """
    repo = _git_repo(tmp_path)
    bindir = tmp_path / "bin"
    _make_pytest_stub(bindir, exit_code=99)
    stdin = f"refs/heads/master {NEW_SHA} refs/heads/master {OLD_SHA}\n"

    res = _check(stdin, bindir, cwd=repo)

    assert res.returncode == 1
    assert "BLOCKED" in res.stderr
    assert "run-e2e-gate.sh" in res.stderr
    assert not (bindir / "last_argv").exists(), "pytest must not be invoked in check mode"


@pytest.mark.integration
def test_master_push_blocked_with_marker_for_other_sha(tmp_path: Path):
    """A marker exists, but for a DIFFERENT sha → BLOCK. Pins sha-keying."""
    repo = _git_repo(tmp_path)
    _write_marker(repo, OTHER_SHA)
    stdin = f"refs/heads/master {NEW_SHA} refs/heads/master {OLD_SHA}\n"

    res = _check(stdin, bindir=None, cwd=repo)

    assert res.returncode == 1
    assert "BLOCKED" in res.stderr


@pytest.mark.integration
def test_master_push_blocked_when_marker_body_sha_mismatches(tmp_path: Path):
    """A marker file named <sha> whose body records a different sha → BLOCK.

    Pins the defensive ``grep -qx "sha=$sha"`` body check (a stray/forged
    file named like a sha but lacking the matching ``sha=`` line is rejected).
    """
    repo = _git_repo(tmp_path)
    md = _marker_dir(repo)
    md.mkdir(parents=True, exist_ok=True)
    (md / NEW_SHA).write_text(f"sha={OTHER_SHA}\n")  # filename != recorded sha
    stdin = f"refs/heads/master {NEW_SHA} refs/heads/master {OLD_SHA}\n"

    res = _check(stdin, bindir=None, cwd=repo)

    assert res.returncode == 1
    assert "BLOCKED" in res.stderr


@pytest.mark.integration
def test_mixed_payload_requires_marker_for_master_line(tmp_path: Path):
    """If ANY line targets master, that line's marker is required (others N/A)."""
    repo = _git_repo(tmp_path)
    _write_marker(repo, NEW_SHA)
    stdin = (
        f"refs/heads/feature/foo {OTHER_SHA} refs/heads/feature/foo {OLD_SHA}\n"
        f"refs/heads/master {NEW_SHA} refs/heads/master {OLD_SHA}\n"
    )

    res = _check(stdin, bindir=None, cwd=repo)

    assert res.returncode == 0, res.stderr


# ===========================================================================
# RUN MODE — suite + marker side effects (HATS-686 core)
# ===========================================================================


@pytest.mark.integration
def test_run_mode_writes_marker_on_pass(tmp_path: Path):
    """`--run` + pytest exit 0 + clean tree → marker keyed to HEAD, exit 0.

    Fail-under-revert: drop the marker write → no file under e2e-gate/ → red.
    """
    repo = _git_repo(tmp_path)
    bindir = tmp_path / "bin"
    _make_pytest_stub(bindir, exit_code=0)

    res = _run(bindir, cwd=repo)

    assert res.returncode == 0, res.stderr
    marker = _marker_dir(repo) / _head(repo)
    assert marker.exists(), f"marker not written; stderr:\n{res.stderr}"
    assert f"sha={_head(repo)}" in marker.read_text()


@pytest.mark.integration
def test_run_mode_no_marker_on_failure(tmp_path: Path):
    """`--run` + pytest exit 1 → NO marker, exit 1 with failure tail."""
    repo = _git_repo(tmp_path)
    bindir = tmp_path / "bin"
    _make_pytest_stub(bindir, exit_code=1)

    res = _run(bindir, cwd=repo)

    assert res.returncode == 1
    assert "FAILED" in res.stderr
    assert "rc=1" in res.stderr
    assert "NO marker" in res.stderr
    assert not _marker_dir(repo).exists() or not any(_marker_dir(repo).iterdir())


@pytest.mark.integration
def test_run_mode_rc5_writes_marker(tmp_path: Path):
    """`--run` + pytest rc=5 (no tests collected) → marker written, exit 0.

    Preserves the HATS-550 defensive allow (renamed marker / empty folder must
    not permanently brick master pushes).
    """
    repo = _git_repo(tmp_path)
    bindir = tmp_path / "bin"
    _make_pytest_stub(bindir, exit_code=5)

    res = _run(bindir, cwd=repo)

    assert res.returncode == 0, res.stderr
    assert "no tests collected" in res.stderr
    assert (_marker_dir(repo) / _head(repo)).exists()


@pytest.mark.integration
def test_run_mode_dirty_tree_writes_no_marker(tmp_path: Path):
    """`--run` + pytest exit 0 but a DIRTY tree → suite runs, NO marker.

    Pins the R2 clean-tree invariant: the marker must reflect the exact
    committed content that will be pushed. Fail-under-revert: drop the
    ``git status --porcelain`` guard → a marker is written for a dirty tree.
    """
    repo = _git_repo(tmp_path)
    (repo / "dirty").write_text("uncommitted")  # untracked → dirty tree
    bindir = tmp_path / "bin"
    _make_pytest_stub(bindir, exit_code=0)

    res = _run(bindir, cwd=repo)

    assert res.returncode == 0, res.stderr
    assert "dirty" in res.stderr.lower()
    assert "NO marker" in res.stderr
    assert not (_marker_dir(repo) / _head(repo)).exists()


@pytest.mark.integration
def test_run_mode_blocks_when_pytest_missing(tmp_path: Path):
    """`--run` with pytest absent from PATH → ABORT, no marker.

    Preserves the HATS-550 guard (``pip uninstall pytest`` must not yield a
    silent green), now enforced in run mode where the suite actually runs.
    """
    repo = _git_repo(tmp_path)

    res = _run(bindir=None, cwd=repo)  # PATH lacks pytest

    assert res.returncode == 1
    assert "pytest not found" in res.stderr
    assert "ABORTED" in res.stderr
    assert not (_marker_dir(repo) / _head(repo)).exists()


# ===========================================================================
# RUN MODE — argv contract carried over from earlier tickets
# ===========================================================================


@pytest.mark.integration
def test_run_mode_argv_has_markers_and_folders(tmp_path: Path):
    """Run mode invokes pytest with both markers and both folders."""
    repo = _git_repo(tmp_path)
    bindir = tmp_path / "bin"
    _make_pytest_stub(bindir, exit_code=0)

    res = _run(bindir, cwd=repo)

    assert res.returncode == 0, res.stderr
    argv = (bindir / "last_argv").read_text()
    assert "integration or smoke" in argv
    assert "tests/e2e/" in argv
    assert "tests/smoke/" in argv


@pytest.mark.integration
def test_run_mode_deselects_quarantined_tests(tmp_path: Path):
    """HATS-676: the gate filter subtracts ``@pytest.mark.quarantine`` via
    ``-m "(integration or smoke) and not quarantine"``.

    Fail-under-revert: drop ``and not quarantine`` from the hook → red.
    """
    repo = _git_repo(tmp_path)
    bindir = tmp_path / "bin"
    _make_pytest_stub(bindir, exit_code=0)

    res = _run(bindir, cwd=repo)

    assert res.returncode == 0, res.stderr
    argv = (bindir / "last_argv").read_text()
    assert "not quarantine" in argv, argv
    assert "integration or smoke" in argv, argv


@pytest.mark.integration
def test_run_mode_arms_require_venv_strict_mode(tmp_path: Path):
    """HATS-645: run mode exports ``AI_HATS_E2E_REQUIRE_VENV=1`` to pytest.

    Fail-under-revert: drop the ``export`` → the stub records ``<unset>`` → red.
    """
    repo = _git_repo(tmp_path)
    bindir = tmp_path / "bin"
    _make_pytest_stub(bindir, exit_code=0)

    res = _run(bindir, cwd=repo)

    assert res.returncode == 0, res.stderr
    captured = (bindir / "last_require_venv").read_text()
    assert captured == "1", f"stub saw {captured!r}"


@pytest.mark.integration
def test_run_mode_explains_fail_closed_venv_skip(tmp_path: Path):
    """HATS-645: when the failure output mentions AI_HATS_E2E_REQUIRE_VENV, the
    gate prints the explicit FAIL-CLOSED explanation."""
    repo = _git_repo(tmp_path)
    bindir = tmp_path / "bin"
    _make_pytest_stub_emitting(
        bindir, exit_code=1,
        message="E venv-tier required (AI_HATS_E2E_REQUIRE_VENV=1) but unavailable",
    )

    res = _run(bindir, cwd=repo)

    assert res.returncode == 1
    assert "FAIL-CLOSED venv-tier skip (HATS-645)" in res.stderr, res.stderr
    assert not (_marker_dir(repo) / _head(repo)).exists()


@pytest.mark.integration
def test_run_mode_generic_failure_omits_fail_closed_explanation(tmp_path: Path):
    """A generic failure (no env-var marker) blocks but omits the venv note."""
    repo = _git_repo(tmp_path)
    bindir = tmp_path / "bin"
    _make_pytest_stub_emitting(
        bindir, exit_code=1, message="E   assert 1 == 2  # an unrelated test bug",
    )

    res = _run(bindir, cwd=repo)

    assert res.returncode == 1
    assert "FAIL-CLOSED venv-tier skip" not in res.stderr, res.stderr
    assert "git push --no-verify" in res.stderr


@pytest.mark.integration
def test_run_mode_uses_xdist_when_available(tmp_path: Path):
    """xdist present → run mode adds ``-n<N> --dist=loadgroup`` (HATS-589/592)."""
    repo = _git_repo(tmp_path)
    bindir = tmp_path / "bin"
    _make_xdist_aware_stub(bindir, has_xdist=True)

    res = _run(bindir, cwd=repo)

    assert res.returncode == 0, res.stderr
    argv = (bindir / "last_argv").read_text()
    n = _xdist_n(argv)
    assert n is not None and n >= 1, argv
    assert "--dist=loadgroup" in argv, argv


@pytest.mark.integration
def test_run_mode_caps_worker_count_to_ceiling(tmp_path: Path):
    """A many-core host is capped at the ceiling (8), not -n<cores> (HATS-592)."""
    repo = _git_repo(tmp_path)
    bindir = tmp_path / "bin"
    _make_xdist_aware_stub(bindir, has_xdist=True)
    _make_getconf_stub(bindir, count=64)

    res = _run(bindir, cwd=repo)

    assert res.returncode == 0, res.stderr
    argv = (bindir / "last_argv").read_text()
    assert _xdist_n(argv) == 8, argv
    assert "--dist=loadgroup" in argv, argv


@pytest.mark.integration
def test_run_mode_falls_back_to_serial_without_xdist(tmp_path: Path):
    """xdist absent → run mode is serial (no ``-n`` flag), still green (HATS-589)."""
    repo = _git_repo(tmp_path)
    bindir = tmp_path / "bin"
    _make_xdist_aware_stub(bindir, has_xdist=False)

    res = _run(bindir, cwd=repo)

    assert res.returncode == 0, res.stderr
    argv = (bindir / "last_argv").read_text()
    assert _xdist_n(argv) is None, argv
    assert "--dist" not in argv, argv


# ===========================================================================
# Run mode — tmp-cruft sweep preamble (HATS-731)
# ===========================================================================


def _seed_sweep_recorder(repo: Path) -> Path:
    """Stub ``scripts/clean-tmp-cruft.sh`` that records the argv it was called with.

    Stands in for the real sweeper so the gate test asserts *whether and how*
    the hook invokes it, without touching the host's TMPDIR.
    """
    scripts = repo / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    sweep = scripts / "clean-tmp-cruft.sh"
    sweep.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$@" > "{repo}/sweep_argv"\n'
    )
    sweep.chmod(0o755)
    return sweep


@pytest.mark.integration
def test_run_mode_tmp_sweep_is_dry_run_by_default(tmp_path: Path):
    """`--run` invokes scripts/clean-tmp-cruft.sh in DRY-RUN (no ``--force``).

    Default must never auto-delete: the sweeper cannot tell a leaked test
    worktree from a live session, so the gate only previews. Fail-under-revert:
    drop the HATS-731 sweep block → the recorder is never written → red.
    """
    repo = _git_repo(tmp_path)
    bindir = tmp_path / "bin"
    _make_pytest_stub(bindir, exit_code=0)
    _seed_sweep_recorder(repo)

    res = _run(bindir, cwd=repo)

    assert res.returncode == 0, res.stderr
    recorded = repo / "sweep_argv"
    assert recorded.exists(), f"sweeper not invoked; stderr:\n{res.stderr}"
    # invoked with NO args → dry-run preview, not a destructive --force.
    assert recorded.read_text().strip() == "", "default must be dry-run, not --force"


@pytest.mark.integration
def test_run_mode_tmp_sweep_force_when_opted_in(tmp_path: Path):
    """``AI_HATS_E2E_CLEAN_TMP=1`` → the sweeper is invoked with ``--force``.

    Fail-under-revert: drop the opt-in branch → no ``--force`` recorded → red.
    """
    repo = _git_repo(tmp_path)
    bindir = tmp_path / "bin"
    _make_pytest_stub(bindir, exit_code=0)
    _seed_sweep_recorder(repo)

    res = _run(bindir, cwd=repo, env_extra={"AI_HATS_E2E_CLEAN_TMP": "1"})

    assert res.returncode == 0, res.stderr
    recorded = repo / "sweep_argv"
    assert recorded.exists(), f"sweeper not invoked; stderr:\n{res.stderr}"
    assert recorded.read_text().strip() == "--force"


# ===========================================================================
# Wrapper — scripts/run-e2e-gate.sh delegates to the hook's --run mode
# ===========================================================================


@pytest.mark.integration
def test_run_wrapper_delegates_to_hook_run_mode(tmp_path: Path):
    """scripts/run-e2e-gate.sh execs the installed hook with ``--run``.

    Builds a fake repo whose installed hook is a recorder, so the test pins
    the delegation contract without running the real suite. Fail-under-revert:
    change the wrapper to not pass ``--run`` → recorder sees no ``--run`` → red.
    """
    repo = _git_repo(tmp_path)
    hookdir = repo / ".githooks" / "pre-push.d"
    hookdir.mkdir(parents=True)
    recorder = hookdir / "maintainer-quality-gate-pre-push-e2e-master.sh"
    recorder.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$@" > "{repo}/hook_argv"\n'
        "exit 0\n"
    )
    recorder.chmod(0o755)

    res = subprocess.run(
        ["bash", str(WRAPPER)],
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert res.returncode == 0, res.stderr
    assert (repo / "hook_argv").read_text().strip() == "--run"


@pytest.mark.integration
def test_run_wrapper_errors_when_hook_absent(tmp_path: Path):
    """The wrapper fails loudly (not silently) when the gate hook isn't installed."""
    repo = _git_repo(tmp_path)  # no .githooks/ installed

    res = subprocess.run(
        ["bash", str(WRAPPER)],
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert res.returncode == 1
    assert "not installed" in res.stderr
