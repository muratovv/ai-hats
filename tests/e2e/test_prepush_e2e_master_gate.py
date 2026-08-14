"""e2e (HATS-550, HATS-686)

flow:   a maintainer pushes to master, and the pre-push hook decides from a
        stored marker whether the e2e tier has already passed for this commit
cmds:
    git push origin master    # allowed only with a green marker for the pushed sha
expect: a non-master target, a branch deletion and an empty stdin are all
        no-ops; a master push is allowed only when a pass-marker keyed to the
        pushed local_sha sits under <git-common-dir>/ai-hats/e2e-gate/, and is
        blocked when that marker is absent, keyed to another sha, or carries a
        body sha that disagrees; in a mixed payload the master line still needs
        its own marker
why:    the marker is the only evidence the tier ever ran — honour one written
        for a different commit and the gate certifies code nobody tested

flow:   the same maintainer runs the gate itself, which must clear lint and
        unit before spending ~25 minutes on the tier, then record the marker
cmds:
    bash scripts/run-e2e-gate.sh    # thin wrapper over the hook's --run mode
expect: a lint failure blocks before the tier is reached and a unit failure
        names the stage; a green preamble runs both stages and then the suite;
        the marker is written on pass and on rc 5 (nothing selected), but never
        on failure and never from a dirty tree; a missing pytest blocks; the
        argv carries the tier's markers and folders, deselects quarantined
        tests, and arms fail-closed venv strict mode, explaining a venv skip
        only when that is actually the cause; xdist is used when available and
        capped at a worker ceiling, falling back to serial without it; the tmp
        sweep is dry-run unless opted into; and the wrapper errors when the
        hook is absent
why:    pre-push runs while git holds the GitHub SSH connection and is killed
        at ~30s, so the tier cannot run there — splitting check from run is what
        makes the gate possible at all, and a marker written from a dirty tree
        or a failed run certifies something that was never green
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
        f'printf "%s\\n" "$@" ${{PYTEST_ADDOPTS:-}} > "{bindir}/last_argv"\n'
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
        f'printf "%s\\n" "$@" ${{PYTEST_ADDOPTS:-}} > "{bindir}/last_argv"\n'
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
        f'printf "%s\\n" "$@" ${{PYTEST_ADDOPTS:-}} > "{bindir}/last_argv"\n'
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


def _tier_ran(bindir: Path) -> bool:
    """Did the e2e TIER run? The gate also probes `pytest -VV` for xdist before
    the stages, so the mere existence of a recorded argv no longer answers it."""
    argv = bindir / "last_argv"
    return argv.exists() and "tests/e2e/" in argv.read_text()


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
    _write_dispatcher(repo)
    run("add", "-A")
    run("commit", "-qm", "init")
    return repo


def _write_dispatcher(repo: Path, *, stages: str = "e2e") -> Path:
    """The gate asks the project what to run and runs it stage by stage, so the
    sandbox needs a dispatcher that answers `push-gate --stages` (HATS-1604)."""
    path = repo / "scripts" / "ci-local.sh"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/usr/bin/env bash\n"
        f'if [[ "$1" == "--stages" ]]; then echo "{stages}"; exit 0; fi\n'
        'if [[ "$1" == "e2e" ]]; then\n'
        '  exec pytest -m "(integration or smoke) and not quarantine" tests/e2e/ tests/smoke/ -q\n'
        "fi\n"
        "exit 0\n"
    )
    path.chmod(0o755)
    return path


def _marker_dir(repo: Path) -> Path:
    return repo / ".git" / "ai-hats" / "e2e-gate"


def _write_marker(repo: Path, tree: str, stages: str = "e2e") -> Path:
    """Keyed by TREE and carrying its composition, the way the gate writes it."""
    md = _marker_dir(repo)
    md.mkdir(parents=True, exist_ok=True)
    p = md / tree
    p.write_text(f"tree={tree}\ntimestamp=2026-01-01T00:00:00Z\nstages={stages}\npytest_rc=0\n")
    return p


def _tree(repo: Path, rev: str = "HEAD") -> str:
    return subprocess.run(
        ["git", "rev-parse", f"{rev}^{{tree}}"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


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
    pushed = _head(repo)
    _write_marker(repo, _tree(repo))
    stdin = f"refs/heads/master {pushed} refs/heads/master {OLD_SHA}\n"

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
def test_master_push_blocked_with_marker_for_other_tree(tmp_path: Path):
    """A marker exists, but for DIFFERENT content → BLOCK. Pins tree-keying."""
    repo = _git_repo(tmp_path)
    _write_marker(repo, OTHER_SHA)  # a marker for content this repo never held
    stdin = f"refs/heads/master {_head(repo)} refs/heads/master {OLD_SHA}\n"

    res = _check(stdin, bindir=None, cwd=repo)

    assert res.returncode == 1
    assert "BLOCKED" in res.stderr


@pytest.mark.integration
def test_master_push_blocked_when_marker_body_tree_mismatches(tmp_path: Path):
    """A marker file named <tree> whose body records different content → BLOCK.

    Pins the defensive ``grep -qx "tree=$tree"`` body check (a stray/forged file
    named like a tree but lacking the matching ``tree=`` line is rejected).
    """
    repo = _git_repo(tmp_path)
    md = _marker_dir(repo)
    md.mkdir(parents=True, exist_ok=True)
    (md / _tree(repo)).write_text(f"tree={OTHER_SHA}\nstages=e2e\n")  # name != body
    stdin = f"refs/heads/master {_head(repo)} refs/heads/master {OLD_SHA}\n"

    res = _check(stdin, bindir=None, cwd=repo)

    assert res.returncode == 1
    assert "BLOCKED" in res.stderr


@pytest.mark.integration
def test_master_push_blocked_when_the_marker_never_ran_a_demanded_stage(tmp_path: Path):
    """HATS-1601: grow the gate a stage and the markers already on disk stop
    applying — before this, every one of them kept clearing the push."""
    repo = _git_repo(tmp_path)
    _write_dispatcher(repo, stages="lint e2e")
    _write_marker(repo, _tree(repo), stages="e2e")
    stdin = f"refs/heads/master {_head(repo)} refs/heads/master {OLD_SHA}\n"

    res = _check(stdin, bindir=None, cwd=repo)

    assert res.returncode == 1
    assert "BLOCKED" in res.stderr


@pytest.mark.integration
def test_mixed_payload_requires_marker_for_master_line(tmp_path: Path):
    """If ANY line targets master, that line's marker is required (others N/A)."""
    repo = _git_repo(tmp_path)
    pushed = _head(repo)
    _write_marker(repo, _tree(repo))
    stdin = (
        f"refs/heads/feature/foo {OTHER_SHA} refs/heads/feature/foo {OLD_SHA}\n"
        f"refs/heads/master {pushed} refs/heads/master {OLD_SHA}\n"
    )

    res = _check(stdin, bindir=None, cwd=repo)

    assert res.returncode == 0, res.stderr


# ===========================================================================
# RUN MODE — the ci-local preamble (HATS-726)
# ===========================================================================


def _commit_dispatcher(
    repo: Path, *, lint_rc: int = 0, unit_rc: int = 0, e2e_catalog_rc: int = 0
) -> Path:
    """Commit a fake ``scripts/ci-local.sh`` with controlled per-stage exit codes.

    It must be committed, not merely written: the gate refuses to write a
    marker for a dirty tree, which would mask what these cases assert.
    """
    scripts = repo / "scripts"
    scripts.mkdir(exist_ok=True)
    dispatcher = scripts / "ci-local.sh"
    # The log lands OUTSIDE the repo: a stray file would dirty the tree and the
    # gate withholds the marker on a dirty tree, masking what these cases assert.
    dispatcher.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$1" == "--stages" ]]; then echo "lint unit e2e-catalog e2e"; exit 0; fi\n'
        f'printf "%s\\n" "$1" >> "{repo.parent / "stages_run"}"\n'
        'case "$1" in\n'
        f"  lint) exit {lint_rc} ;;\n"
        f"  unit) exit {unit_rc} ;;\n"
        f"  e2e-catalog) exit {e2e_catalog_rc} ;;\n"
        '  e2e) exec pytest -m "(integration or smoke) and not quarantine" '
        "tests/e2e/ tests/smoke/ -q ;;\n"
        "esac\n"
        "exit 0\n"
    )
    dispatcher.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_CONFIG_SYSTEM"] = "/dev/null"
    for args in (("add", "-A"), ("commit", "-qm", "add dispatcher")):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=env)
    return dispatcher


def _stages_run(repo: Path) -> list[str]:
    log = repo.parent / "stages_run"
    return log.read_text().split() if log.exists() else []


@pytest.mark.integration
def test_run_mode_lint_failure_blocks_before_the_e2e_tier(tmp_path: Path):
    """A red lint stage aborts the gate without starting the 25-minute suite.

    This is the case that reached master on 2026-07-29: 66 ruff errors passed
    the gate untouched because it ran only e2e+smoke.
    Fail-under-revert: drop the preamble → pytest runs and a marker appears.
    """
    repo = _git_repo(tmp_path)
    _commit_dispatcher(repo, lint_rc=1, unit_rc=0)
    bindir = tmp_path / "bin"
    _make_pytest_stub(bindir, exit_code=0)

    res = _run(bindir, cwd=repo)

    assert res.returncode == 1, res.stderr
    assert "stage 'lint' FAILED" in res.stderr
    assert "NO marker written" in res.stderr
    assert not _tier_ran(bindir), "e2e tier ran despite a red preamble"
    assert not _marker_dir(repo).exists() or not any(_marker_dir(repo).iterdir())


@pytest.mark.integration
def test_run_mode_unit_failure_blocks_and_names_the_stage(tmp_path: Path):
    """A green lint but red unit stage still aborts, naming `unit`."""
    repo = _git_repo(tmp_path)
    _commit_dispatcher(repo, lint_rc=0, unit_rc=1)
    bindir = tmp_path / "bin"
    _make_pytest_stub(bindir, exit_code=0)

    res = _run(bindir, cwd=repo)

    assert res.returncode == 1, res.stderr
    assert "stage 'unit' FAILED" in res.stderr
    assert _stages_run(repo) == ["lint", "unit"]
    assert not _tier_ran(bindir)


@pytest.mark.integration
def test_run_mode_e2e_catalog_failure_blocks_and_names_the_stage(tmp_path: Path):
    """HATS-1562: A red e2e-catalog stage in pre-push preamble aborts the gate.

    Fail-under-revert: revert git_hooks/pre-push-e2e-master.sh preamble stage loop ->
    e2e-catalog is not run in preamble, e2e tier runs and writes marker.
    """
    repo = _git_repo(tmp_path)
    _commit_dispatcher(repo, lint_rc=0, unit_rc=0, e2e_catalog_rc=1)
    bindir = tmp_path / "bin"
    _make_pytest_stub(bindir, exit_code=0)

    res = _run(bindir, cwd=repo)

    assert res.returncode == 1, res.stderr
    assert "stage 'e2e-catalog' FAILED" in res.stderr
    assert "NO marker written" in res.stderr
    assert _stages_run(repo) == ["lint", "unit", "e2e-catalog"], "stops at the first red"
    assert not _tier_ran(bindir), "e2e tier ran despite a red e2e-catalog stage"
    assert not _marker_dir(repo).exists() or not any(_marker_dir(repo).iterdir())


@pytest.mark.integration
def test_run_mode_green_preamble_runs_both_stages_then_the_suite(tmp_path: Path):
    """Green lint + unit + e2e-catalog → all stages ran, the suite ran, the marker is written."""
    repo = _git_repo(tmp_path)
    _commit_dispatcher(repo, lint_rc=0, unit_rc=0, e2e_catalog_rc=0)
    bindir = tmp_path / "bin"
    _make_pytest_stub(bindir, exit_code=0)

    res = _run(bindir, cwd=repo)

    assert res.returncode == 0, res.stderr
    # HATS-1604: the tier is a STAGE now, so the selection has one home and the
    # `pytest`-outside-the-dispatcher ratchet finally covers it.
    assert _stages_run(repo) == ["lint", "unit", "e2e-catalog", "e2e"]
    assert (bindir / "last_argv").exists(), "e2e tier did not run"
    assert (_marker_dir(repo) / _tree(repo)).exists()


@pytest.mark.integration
def test_run_mode_without_a_dispatcher_earns_no_marker(tmp_path: Path):
    """HATS-1604: a project that names no composition gets no marker.

    The gate used to fall back to a tier it named itself, which is the library
    stating project content (ADR-0023 D7). With the literal gone there is
    nothing to fall back TO — and a marker for an unnamed composition would
    certify whatever the gate happened to run that day.
    """
    repo = _git_repo(tmp_path)
    (repo / "scripts" / "ci-local.sh").unlink()
    bindir = tmp_path / "bin"
    _make_pytest_stub(bindir, exit_code=0)

    res = _run(bindir, cwd=repo)

    assert res.returncode == 1, res.stderr
    assert "names no push-gate composition" in res.stderr
    assert not _tier_ran(bindir), "nothing may run without a composition"
    assert not _marker_dir(repo).exists() or not any(_marker_dir(repo).iterdir())


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
    marker = _marker_dir(repo) / _tree(repo)
    assert marker.exists(), f"marker not written; stderr:\n{res.stderr}"
    body = marker.read_text()
    assert f"tree={_tree(repo)}" in body
    assert "stages=e2e" in body, "a marker states the composition it certifies (HATS-1601)"


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
    assert "nothing collected" in res.stderr
    assert (_marker_dir(repo) / _tree(repo)).exists()


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
    assert not (_marker_dir(repo) / _tree(repo)).exists()


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
    assert not (_marker_dir(repo) / _tree(repo)).exists()


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
        bindir,
        exit_code=1,
        message="E venv-tier required (AI_HATS_E2E_REQUIRE_VENV=1) but unavailable",
    )

    res = _run(bindir, cwd=repo)

    assert res.returncode == 1
    assert "FAIL-CLOSED venv-tier skip (HATS-645)" in res.stderr, res.stderr
    assert not (_marker_dir(repo) / _tree(repo)).exists()


@pytest.mark.integration
def test_run_mode_generic_failure_omits_fail_closed_explanation(tmp_path: Path):
    """A generic failure (no env-var marker) blocks but omits the venv note."""
    repo = _git_repo(tmp_path)
    bindir = tmp_path / "bin"
    _make_pytest_stub_emitting(
        bindir,
        exit_code=1,
        message="E   assert 1 == 2  # an unrelated test bug",
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
    sweep.write_text(f'#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "{repo}/sweep_argv"\n')
    sweep.chmod(0o755)
    return sweep


@pytest.mark.integration
def test_run_mode_tmp_sweep_reaps_by_default(tmp_path: Path):
    """`--run` invokes scripts/clean-tmp-cruft.sh bare — which now DELETES.

    Same argv as the old dry-run preview, opposite meaning (HATS-1624): the
    sweeper reaps only on proof of death, so the gate no longer has to choose
    between deleting a live worktree and freeing nothing. Fail-under-revert:
    drop the sweep block → the recorder is never written → red.
    """
    repo = _git_repo(tmp_path)
    bindir = tmp_path / "bin"
    _make_pytest_stub(bindir, exit_code=0)
    _seed_sweep_recorder(repo)

    res = _run(bindir, cwd=repo)

    assert res.returncode == 0, res.stderr
    recorded = repo / "sweep_argv"
    assert recorded.exists(), f"sweeper not invoked; stderr:\n{res.stderr}"
    # No args → reap-on-proof. --dry-run here would restore the silent gate.
    assert recorded.read_text().strip() == "", "the gate must not preview-only"


@pytest.mark.integration
def test_run_mode_tmp_sweep_force_when_opted_in(tmp_path: Path):
    """``AI_HATS_E2E_CLEAN_TMP=1`` → the sweeper is invoked with ``--force``.

    The escalation, not the on-switch: --force additionally takes the unlocked
    run dirs pytest keeps for triage. Fail-under-revert: drop the opt-in branch
    → no ``--force`` recorded → red.
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
    # HATS-1337: the wrapper resolves the gate out of the ai-hats library rather
    # than a retired `.githooks/pre-push.d/` copy. Stub the interpreter it asks,
    # so this pins the delegation contract without touching the real library —
    # and without any chance of launching the real 30-minute suite.
    libroot = tmp_path / "lib"
    recorder = libroot / "usage/skills/maintainer-quality-gate/git_hooks/pre-push-e2e-master.sh"
    recorder.parent.mkdir(parents=True)
    recorder.write_text(f'#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "{repo}/hook_argv"\nexit 0\n')
    recorder.chmod(0o755)

    fake_python = tmp_path / "python3"
    fake_python.write_text(f'#!/usr/bin/env bash\necho "{libroot}"\n')
    fake_python.chmod(0o755)

    res = subprocess.run(
        ["bash", str(WRAPPER)],
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=20,
        env={**os.environ, "PYTHON": str(fake_python)},
    )

    assert res.returncode == 0, res.stderr
    assert (repo / "hook_argv").read_text().strip() == "--run"


@pytest.mark.integration
def test_run_wrapper_errors_when_hook_absent(tmp_path: Path):
    """The wrapper fails loudly (not silently) when ai-hats is not installed here.

    The interpreter is pinned to one that cannot import the library. Left to
    PATH's python3 the test would resolve the REAL gate and run it for real —
    which is what it did before this pin.
    """
    repo = _git_repo(tmp_path)
    blind_python = tmp_path / "python3"
    blind_python.write_text("#!/usr/bin/env bash\nexit 1\n")
    blind_python.chmod(0o755)

    res = subprocess.run(
        ["bash", str(WRAPPER)],
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=20,
        env={**os.environ, "PYTHON": str(blind_python)},
    )

    assert res.returncode == 1
    assert "cannot resolve the e2e-master gate" in res.stderr
