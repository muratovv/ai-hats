"""e2e (HATS-550, HATS-686, HATS-1878)

flow:   a maintainer pushing to master, gated by git's pre-push hook
cmds:
    git push origin master           # allowed only with every stage the hook declares marked
    scripts/run-e2e-gate.sh          # earns the markers out of band, one per stage
expect: check mode reads the pre-push protocol, ignores non-master lines, and
        asks the project's own `gates.sh check` about each pushed commit's TREE;
        run mode runs only the unmarked stages, stops at the first red, and
        stamps each green one for the commit it judged
why:    GitHub closes the push connection ~30s in, so the tier runs out of band
        and the per-stage markers are the only evidence it ran
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HOOK = (
    REPO_ROOT
    / "packages/ai-hats-library/src/ai_hats_library/ai-hats-dev/skills/maintainer-quality-gate"
    / "git_hooks/pre-push-e2e-master.sh"
)
ZERO = "0" * 40
OLD_SHA = "2" * 40
OTHER_SHA = "3" * 40


def _push_gate_stages() -> list[str]:
    out = subprocess.run(
        ["bash", str(HOOK), "--stages"], capture_output=True, text=True, check=True
    )
    return out.stdout.split()


# --- the sandbox -------------------------------------------------------------


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_CONFIG_SYSTEM"] = "/dev/null"
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True, env=env
    )


def _write_runner(where: Path, rcs: dict[str, int] | None = None) -> Path:
    """A fake stage runner OUTSIDE the repo: every stage records itself and exits
    as told (unset → green); `e2e` hands over to whatever `pytest` is on PATH,
    so the tier's own invocation stays observable."""
    arms = "".join(f"  {stage}) exit {rc} ;;\n" for stage, rc in (rcs or {}).items())
    runner = where / "runner.sh"
    runner.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$1" == "--prepare" ]]; then exit 0; fi\n'
        f'printf "%s\\n" "$1" >> "{where / "stages_run"}"\n'
        'case "$1" in\n'
        f"{arms}"
        '  e2e) exec pytest -m "(integration or smoke) and not quarantine and not live_agy" '
        "tests/e2e/ tests/smoke/ -q ;;\n"
        "esac\n"
        "exit 0\n"
    )
    runner.chmod(0o755)
    return runner


def _git_repo(tmp_path: Path, rcs: dict[str, int] | None = None) -> Path:
    """One commit holding the real `scripts/gates.sh`; the fake stage runner sits
    beside the repo and reaches it through GATES_STAGE_RUNNER."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "f").write_text("x")
    (repo / "scripts").mkdir()
    shutil.copy(REPO_ROOT / "scripts" / "gates.sh", repo / "scripts" / "gates.sh")
    _write_runner(tmp_path, rcs)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    return repo


def _stages_run(repo: Path) -> list[str]:
    log = repo.parent / "stages_run"
    return log.read_text().split() if log.exists() else []


def _store(repo: Path) -> Path:
    return repo / ".git" / "ai-hats" / "stages"


def _marked(repo: Path, tree: str) -> set[str]:
    where = _store(repo) / tree
    return {p.name for p in where.iterdir()} if where.is_dir() else set()


def _write_markers(repo: Path, tree: str, stages: list[str] | None = None) -> None:
    """Plant markers the way the primitive writes them: one file per stage."""
    where = _store(repo) / tree
    where.mkdir(parents=True, exist_ok=True)
    for stage in _push_gate_stages() if stages is None else stages:
        (where / stage).write_text(f"tree={tree}\nstage={stage}\n")


def _red(res: subprocess.CompletedProcess[str], stage: str) -> bool:
    """The verdict line names the red stage: `RESULT … FAILED <stage> (rc=N)`."""
    return f"FAILED {stage} (rc=" in res.stderr


def _tree(repo: Path, rev: str = "HEAD") -> str:
    return _git(repo, "rev-parse", f"{rev}^{{tree}}").stdout.strip()


def _head(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _env(repo: Path, bindir: Path | None) -> dict[str, str]:
    """A bare PATH: system bash 3.2 on macOS, and no `python` — so the xdist
    probe falls through to the `pytest` a case puts in `bindir`."""
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": os.environ.get("HOME", "/tmp"),
        "GATES_STAGE_RUNNER": str(repo.parent / "runner.sh"),
    }
    if bindir is not None:
        env["PATH"] = f"{bindir}:{env['PATH']}"
    return env


def _check(stdin: str, *, cwd: Path) -> subprocess.CompletedProcess[str]:
    """The hook in CHECK mode: git's pre-push protocol on stdin."""
    return subprocess.run(
        ["bash", str(HOOK)],
        input=stdin,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=30,
        env=_env(cwd, None),
    )


def _run(bindir: Path | None, *, cwd: Path) -> subprocess.CompletedProcess[str]:
    """The hook in RUN mode."""
    return subprocess.run(
        ["bash", str(HOOK), "--run"],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=60,
        env=_env(cwd, bindir),
    )


def _make_pytest_stub(bindir: Path, exit_code: int, *, xdist: bool = False) -> Path:
    """A fake `pytest` on PATH: answers the `-VV` probe (with or without xdist),
    records the tier's argv plus PYTEST_ADDOPTS and the venv switch, exits."""
    bindir.mkdir(parents=True, exist_ok=True)
    banner = "plugins: xdist-3.8.0, cov-4.0" if xdist else "plugins: cov-4.0"
    stub = bindir / "pytest"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'if [[ "$1" == "-VV" ]]; then printf "%s\\n" "{banner}"; exit 0; fi\n'
        f'printf "%s\\n" "$@" ${{PYTEST_ADDOPTS:-}} > "{bindir}/last_argv"\n'
        f'printf "%s" "${{AI_HATS_E2E_REQUIRE_VENV:-<unset>}}" > "{bindir}/last_require_venv"\n'
        f"exit {exit_code}\n"
    )
    stub.chmod(0o755)
    return stub


def _make_getconf_stub(bindir: Path, count: int) -> Path:
    bindir.mkdir(parents=True, exist_ok=True)
    stub = bindir / "getconf"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'if [[ "$1" == "_NPROCESSORS_ONLN" ]]; then echo {count}; exit 0; fi\n'
        'exec /usr/bin/getconf "$@"\n'
    )
    stub.chmod(0o755)
    return stub


def _tier_ran(bindir: Path) -> bool:
    argv = bindir / "last_argv"
    return argv.exists() and "tests/e2e/" in argv.read_text()


def _xdist_n(argv: str) -> int | None:
    for token in argv.split():
        if token.startswith("-n") and token[2:].isdigit():
            return int(token[2:])
    return None


# ===========================================================================
# CHECK MODE — fast-path no-ops
# ===========================================================================


def test_non_master_target_is_noop(tmp_path: Path):
    repo = _git_repo(tmp_path)
    res = _check(f"refs/heads/feature/x {_head(repo)} refs/heads/feature/x {OLD_SHA}\n", cwd=repo)

    assert res.returncode == 0, res.stderr
    assert _stages_run(repo) == []


def test_master_deletion_is_noop(tmp_path: Path):
    repo = _git_repo(tmp_path)
    res = _check(f"(delete) {ZERO} refs/heads/master {OLD_SHA}\n", cwd=repo)

    assert res.returncode == 0, res.stderr


def test_empty_stdin_is_noop(tmp_path: Path):
    """The check runner hands its children stdin=DEVNULL — which is exactly why
    this hook is NOT a checks-channel gate (ADR-0023 D9)."""
    repo = _git_repo(tmp_path)
    res = _check("", cwd=repo)

    assert res.returncode == 0, res.stderr


# ===========================================================================
# CHECK MODE — the marker lookup
# ===========================================================================


def test_master_push_allowed_with_every_stage_marked(tmp_path: Path):
    repo = _git_repo(tmp_path)
    _write_markers(repo, _tree(repo))

    res = _check(f"refs/heads/master {_head(repo)} refs/heads/master {OLD_SHA}\n", cwd=repo)

    assert res.returncode == 0, res.stderr
    assert "push allowed" in res.stderr
    assert _stages_run(repo) == [], "a check runs no stage"


def test_master_push_blocked_without_markers_and_names_what_is_missing(tmp_path: Path):
    """Fail-under-revert: drop the block → exit 0 with no marker at all."""
    repo = _git_repo(tmp_path)

    res = _check(f"refs/heads/master {_head(repo)} refs/heads/master {OLD_SHA}\n", cwd=repo)

    assert res.returncode == 1
    assert "BLOCKED" in res.stderr
    assert "run-e2e-gate.sh" in res.stderr, "the refusal hands over the command that earns it"
    for stage in _push_gate_stages():
        assert stage in res.stderr, f"the refusal must name the missing stage {stage}"
    assert _stages_run(repo) == [], "a refusal runs no stage"


def test_master_push_blocked_when_one_demanded_stage_is_unmarked(tmp_path: Path):
    """Grow the gate a stage and every marker set on disk stops applying —
    before HATS-1601, all of them kept clearing the push."""
    repo = _git_repo(tmp_path)
    stages = _push_gate_stages()
    _write_markers(repo, _tree(repo), stages[:-1])

    res = _check(f"refs/heads/master {_head(repo)} refs/heads/master {OLD_SHA}\n", cwd=repo)

    assert res.returncode == 1
    missing = res.stderr.split("Missing:", 1)[1].split("Run the gate", 1)[0].split()
    assert missing == [stages[-1]]


def test_master_push_blocked_with_markers_for_another_tree(tmp_path: Path):
    """Pins tree-keying: content this repo never held clears nothing."""
    repo = _git_repo(tmp_path)
    _write_markers(repo, OTHER_SHA)

    res = _check(f"refs/heads/master {_head(repo)} refs/heads/master {OLD_SHA}\n", cwd=repo)

    assert res.returncode == 1
    assert "BLOCKED" in res.stderr


def test_mixed_payload_requires_markers_for_the_master_line_only(tmp_path: Path):
    repo = _git_repo(tmp_path)
    _write_markers(repo, _tree(repo))
    stdin = (
        f"refs/heads/feature/foo {OTHER_SHA} refs/heads/feature/foo {OLD_SHA}\n"
        f"refs/heads/master {_head(repo)} refs/heads/master {OLD_SHA}\n"
    )

    res = _check(stdin, cwd=repo)

    assert res.returncode == 0, res.stderr


def test_a_project_without_gates_sh_cannot_push_to_master(tmp_path: Path):
    """Nothing could have earned a marker, so nothing lets the push through —
    the library never runs a stage itself (D7)."""
    repo = _git_repo(tmp_path)
    (repo / "scripts" / "gates.sh").unlink()
    _git(repo, "commit", "-qam", "drop the runner")

    res = _check(f"refs/heads/master {_head(repo)} refs/heads/master {OLD_SHA}\n", cwd=repo)

    assert res.returncode == 1
    assert "no scripts/gates.sh" in res.stderr


# ===========================================================================
# RUN MODE — stage by stage, through the project's own primitive
# ===========================================================================


def test_run_mode_stops_at_the_first_red_and_the_tier_never_starts(tmp_path: Path):
    """The case that reached master on 2026-07-29: 66 ruff errors passed a gate
    that ran only e2e+smoke. The list leads with the cheap stages, and a red
    one stops the run before the tier."""
    repo = _git_repo(tmp_path, rcs={"lint": 1})
    bindir = tmp_path / "bin"
    _make_pytest_stub(bindir, exit_code=0)

    res = _run(bindir, cwd=repo)

    assert res.returncode == 1, res.stderr
    assert _red(res, "lint"), res.stderr
    stages = _push_gate_stages()
    assert _stages_run(repo) == stages[: stages.index("lint") + 1]
    assert not _tier_ran(bindir), "the e2e tier ran despite a red cheap stage"
    assert "lint" not in _marked(repo, _tree(repo))
    assert "e2e" not in _marked(repo, _tree(repo))


def test_run_mode_green_run_marks_every_stage_and_runs_the_tier(tmp_path: Path):
    repo = _git_repo(tmp_path)
    bindir = tmp_path / "bin"
    _make_pytest_stub(bindir, exit_code=0)

    res = _run(bindir, cwd=repo)

    assert res.returncode == 0, res.stderr
    assert _stages_run(repo) == _push_gate_stages()
    assert _tier_ran(bindir)
    assert _marked(repo, _tree(repo)) == set(_push_gate_stages())
    marker = _store(repo) / _tree(repo) / "e2e"
    assert f"tree={_tree(repo)}" in marker.read_text()


def test_run_mode_a_red_tier_earns_no_marker_for_it(tmp_path: Path):
    repo = _git_repo(tmp_path)
    bindir = tmp_path / "bin"
    _make_pytest_stub(bindir, exit_code=1)

    res = _run(bindir, cwd=repo)

    assert res.returncode == 1
    assert _red(res, "e2e"), res.stderr
    marked = _marked(repo, _tree(repo))
    assert "e2e" not in marked
    assert marked == set(_push_gate_stages()) - {"e2e"}, "the green cheap stages keep their stamps"


def test_run_mode_a_second_run_pays_only_for_what_is_missing(tmp_path: Path):
    repo = _git_repo(tmp_path)
    bindir = tmp_path / "bin"
    _make_pytest_stub(bindir, exit_code=1)
    assert _run(bindir, cwd=repo).returncode == 1
    _make_pytest_stub(bindir, exit_code=0)

    res = _run(bindir, cwd=repo)

    assert res.returncode == 0, res.stderr
    assert _stages_run(repo) == [*_push_gate_stages(), "e2e"], "only the tier ran the second time"


def test_run_mode_dirty_tree_is_judged_in_a_scratch_checkout(tmp_path: Path):
    """The contract flipped with HATS-1878: a dirty desk no longer means "run,
    but no marker" — the commit is judged in a checkout of its own, so HEAD's
    tree earns its markers and the desk is left alone."""
    repo = _git_repo(tmp_path)
    (repo / "dirty").write_text("uncommitted")
    bindir = tmp_path / "bin"
    _make_pytest_stub(bindir, exit_code=0)

    res = _run(bindir, cwd=repo)

    assert res.returncode == 0, res.stderr
    assert "scratch: " in res.stderr, res.stderr
    assert _marked(repo, _tree(repo)) == set(_push_gate_stages())
    assert (repo / "dirty").exists(), "the desk is untouched"
    assert "scratch" not in _git(repo, "worktree", "list").stdout


def test_run_mode_without_pytest_the_tier_is_red_and_unmarked(tmp_path: Path):
    """`pip uninstall pytest` must not yield a silent green."""
    repo = _git_repo(tmp_path)

    res = _run(bindir=None, cwd=repo)

    assert res.returncode != 0
    assert _red(res, "e2e"), res.stderr
    assert "e2e" not in _marked(repo, _tree(repo))


# ===========================================================================
# RUN MODE — parallelism follows the pytest that will run
# ===========================================================================


def test_run_mode_adds_xdist_flags_capped_at_eight_when_xdist_is_present(tmp_path: Path):
    repo = _git_repo(tmp_path)
    bindir = tmp_path / "bin"
    _make_pytest_stub(bindir, exit_code=0, xdist=True)
    _make_getconf_stub(bindir, count=14)

    res = _run(bindir, cwd=repo)

    assert res.returncode == 0, res.stderr
    argv = (bindir / "last_argv").read_text()
    assert _xdist_n(argv) == 8
    assert "--dist=loadgroup" in argv


def test_run_mode_uses_every_core_below_the_cap(tmp_path: Path):
    repo = _git_repo(tmp_path)
    bindir = tmp_path / "bin"
    _make_pytest_stub(bindir, exit_code=0, xdist=True)
    _make_getconf_stub(bindir, count=4)

    res = _run(bindir, cwd=repo)

    assert res.returncode == 0, res.stderr
    assert _xdist_n((bindir / "last_argv").read_text()) == 4


def test_run_mode_runs_serial_without_xdist(tmp_path: Path):
    repo = _git_repo(tmp_path)
    bindir = tmp_path / "bin"
    _make_pytest_stub(bindir, exit_code=0, xdist=False)

    res = _run(bindir, cwd=repo)

    assert res.returncode == 0, res.stderr
    argv = (bindir / "last_argv").read_text()
    assert _xdist_n(argv) is None
    assert "--dist=loadgroup" not in argv


# ===========================================================================
# the wrapper
# ===========================================================================


def test_the_wrapper_arms_the_venv_switch_and_hands_over_to_the_hook():
    """`scripts/run-e2e-gate.sh` is the maintainer's spelling: housekeeping, the
    fail-closed venv switch, then the hook's own run mode. Probed for its shape,
    not run — running it here would judge this very checkout."""
    text = (REPO_ROOT / "scripts" / "run-e2e-gate.sh").read_text(encoding="utf-8")

    assert "export AI_HATS_E2E_REQUIRE_VENV=1" in text
    assert 'exec bash "$hook" --run "$@"' in text
    assert "pre-push-e2e-master.sh" in text
