"""e2e (HATS-1878)

flow:   the gate primitive — a requirement over stages, and the run that meets it
cmds:
    scripts/gates.sh check [--rev <commit>] <stage>...
    scripts/gates.sh run [--rev <commit>] [--fresh] <stage>...
    scripts/gates.sh subject [--rev <commit>]
expect: `check` lists what lacks a marker and never calls the runner; `run`
        runs only the unmarked, stamps each green stage for the SUBJECT tree,
        and judges a commit in a one-shot scratch worktree when the checkout is
        dirty or its HEAD is not the subject
why:    a per-GATE, all-or-nothing marker made a wider gate re-run what a
        narrower one had earned; a run "here" judged whatever the desk held
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from _helpers.git import commit_file, git, init_repo

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
GATES = REPO_ROOT / "scripts" / "gates.sh"


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    project = tmp_path / "proj"
    project.mkdir()
    init_repo(project)
    commit_file(project, "README.md", "hello\n", "init")
    return project


@pytest.fixture()
def runner(tmp_path: Path) -> Path:
    """A stage runner OUTSIDE the repo that records every call and exits as told.

    Outside, so the repo stays clean without committing it; the log too, so a
    stage cannot dirty the tree by being recorded. Per-stage exit codes come
    from `GATES_TEST_RC_<STAGE>`; unset means green. What a stage PRINTS comes
    from `GATES_TEST_OUT_<STAGE>`, which is how a test hands a stage a real
    pytest short summary. `--prepare` is the one non-stage verb the primitive
    asks of a runner, and it is a no-op here.
    """
    log = tmp_path / "calls.log"
    script = tmp_path / "runner.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$1" == "--prepare" ]]; then exit 0; fi\n'
        f'printf "%s|%s|%s\\n" "$1" "$PWD" "${{PYTEST_ADDOPTS:-}}" >> "{log}"\n'
        'said="GATES_TEST_OUT_$(printf "%s" "$1" | tr "a-z-" "A-Z_")"\n'
        'if [[ -n "${!said:-}" ]]; then printf "%s\\n" "${!said}"; fi\n'
        'name="GATES_TEST_RC_$(printf "%s" "$1" | tr "a-z-" "A-Z_")"\n'
        'exit "${!name:-0}"\n'
    )
    script.chmod(0o755)
    return script


def _rows(runner: Path) -> list[list[str]]:
    log = runner.parent / "calls.log"
    return [line.split("|", 2) for line in log.read_text().splitlines()] if log.exists() else []


def _calls(runner: Path) -> list[tuple[str, str]]:
    """(stage, cwd) per call."""
    return [(stage, cwd) for stage, cwd, _ in _rows(runner)]


def _addopts(runner: Path) -> dict[str, str]:
    """stage -> the PYTEST_ADDOPTS it was handed."""
    return {stage: addopts for stage, _, addopts in _rows(runner)}


def _gate(
    repo: Path, runner: Path, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    child = {k: v for k, v in os.environ.items() if not k.startswith("GATES_TEST_")}
    child["GATES_STAGE_RUNNER"] = str(runner)
    child.pop("PYTEST_ADDOPTS", None)
    # Every real gate exports this (lib/gate.sh), so a run of this file from
    # INSIDE one would judge the primitive on the gate's resume command.
    child.pop("GATES_RESUME_CMD", None)
    child.update(env or {})
    return subprocess.run(
        ["bash", str(GATES), *args],
        cwd=repo,
        capture_output=True,
        text=True,
        env=child,
        check=False,
    )


def _tree(repo: Path, rev: str = "HEAD") -> str:
    return git(repo, "rev-parse", f"{rev}^{{tree}}").stdout.strip()


def _store(repo: Path) -> Path:
    common = git(repo, "rev-parse", "--git-common-dir").stdout.strip()
    return (repo / common).resolve() / "ai-hats" / "stages"


def _marked(repo: Path, tree: str) -> set[str]:
    where = _store(repo) / tree
    return {p.name for p in where.iterdir()} if where.is_dir() else set()


def _short(repo: Path, rev: str) -> str:
    return git(repo, "rev-parse", "--short", rev).stdout.strip()


def _header(repo: Path, rev: str = "HEAD") -> str:
    return f"[gates] {_short(repo, rev)} (tree {_short(repo, rev + '^{tree}')})"


def _result(repo: Path, verdict: str, rev: str = "HEAD") -> str:
    return f"[gates] RESULT tree {_short(repo, rev + '^{tree}')} ({_short(repo, rev)}): {verdict}"


# ---------------------------------------------------------------------------
# check is a requirement, never an execution
# ---------------------------------------------------------------------------


def test_check_lists_every_unmarked_stage_and_never_calls_the_runner(repo: Path, runner: Path):
    """The positive control lives in the same fixture: `run` below DOES reach
    this runner, so an empty call log here is absence, not a broken runner."""
    out = _gate(repo, runner, "check", "lint", "unit", env={"GATES_TEST_RC_LINT": "99"})

    assert out.returncode == 1, out.stderr
    assert out.stdout.split() == ["lint", "unit"]
    assert _calls(runner) == []

    ran = _gate(repo, runner, "run", "lint", env={"GATES_TEST_RC_LINT": "99"})
    assert ran.returncode == 99
    assert [c[0] for c in _calls(runner)] == ["lint"], "the same runner IS reachable by run"


def test_check_passes_with_empty_stdout_once_every_stage_is_marked(repo: Path, runner: Path):
    assert _gate(repo, runner, "run", "lint", "unit").returncode == 0

    out = _gate(repo, runner, "check", "lint", "unit")

    assert out.returncode == 0, out.stderr
    assert out.stdout == ""


def test_a_marker_whose_tree_line_disagrees_with_its_path_certifies_nothing(
    repo: Path, runner: Path
):
    tree = _tree(repo)
    forged = _store(repo) / tree / "lint"
    forged.parent.mkdir(parents=True)
    forged.write_text("tree=0000000000000000000000000000000000000000\nstage=lint\n")

    out = _gate(repo, runner, "check", "lint")

    assert out.returncode == 1
    assert out.stdout.split() == ["lint"]


# ---------------------------------------------------------------------------
# run is incremental: markers are per stage and are read across runs
# ---------------------------------------------------------------------------


def test_a_wider_run_skips_what_a_narrower_one_already_earned(repo: Path, runner: Path):
    """HATS-1878's motivating case: review's set, then done's superset, on one
    tree — every stage of the review set runs exactly once."""
    assert _gate(repo, runner, "run", "lint", "unit").returncode == 0
    assert _gate(repo, runner, "run", "lint", "unit", "integration", "merge-smoke").returncode == 0

    stages = [c[0] for c in _calls(runner)]
    assert stages == ["lint", "unit", "integration", "merge-smoke"]
    assert _marked(repo, _tree(repo)) == {"lint", "unit", "integration", "merge-smoke"}


def test_fresh_reruns_a_marked_stage(repo: Path, runner: Path):
    assert _gate(repo, runner, "run", "lint").returncode == 0
    assert _gate(repo, runner, "run", "--fresh", "lint").returncode == 0

    assert [c[0] for c in _calls(runner)] == ["lint", "lint"]


def test_a_red_stage_stops_the_run_and_keeps_the_stamps_earned_before_it(repo: Path, runner: Path):
    out = _gate(repo, runner, "run", "lint", "unit", "integration", env={"GATES_TEST_RC_UNIT": "7"})

    assert out.returncode == 7
    lines = out.stderr.splitlines()
    assert lines[0] == _result(repo, "0 cached, 2 ran, FAILED unit (rc=7)")
    assert lines[1] == "[gates] fix unit, then run this again"
    assert lines[2] == "[gates] unit said:", "what the red stage printed follows the verdict"
    assert [c[0] for c in _calls(runner)] == ["lint", "unit"], "nothing after the red one ran"
    assert _marked(repo, _tree(repo)) == {"lint"}, "lint's stamp survives unit's red"


def test_a_new_commit_is_a_new_tree_and_earns_its_own_markers(repo: Path, runner: Path):
    assert _gate(repo, runner, "run", "lint").returncode == 0
    first = _tree(repo)
    commit_file(repo, "b.txt", "b\n", "second")

    out = _gate(repo, runner, "check", "lint")

    assert out.returncode == 1 and out.stdout.split() == ["lint"]
    assert _marked(repo, first) == {"lint"}, "the old tree's marker is untouched"


# ---------------------------------------------------------------------------
# the subject is a commit; where it runs follows from clean ∧ HEAD == subject
# ---------------------------------------------------------------------------


def test_a_clean_checkout_of_the_subject_runs_in_place(repo: Path, runner: Path):
    assert "where=in-place" in _gate(repo, runner, "subject").stdout
    assert _gate(repo, runner, "run", "lint").returncode == 0

    assert _calls(runner) == [("lint", str(repo))]


def test_a_dirty_checkout_judges_head_in_a_one_shot_scratch_worktree(repo: Path, runner: Path):
    (repo / "scratch.txt").write_text("untracked\n")
    before = git(repo, "status", "--porcelain").stdout

    assert "where=scratch" in _gate(repo, runner, "subject").stdout
    out = _gate(repo, runner, "run", "lint")

    assert out.returncode == 0, out.stderr
    ((stage, ran_in),) = _calls(runner)
    assert stage == "lint"
    assert ran_in != str(repo), "the stage ran somewhere other than the dirty desk"
    assert f"{_header(repo)} scratch: {ran_in}" in out.stderr.splitlines()
    assert not Path(ran_in).exists(), "the scratch checkout is gone after the run"
    assert "scratch" not in git(repo, "worktree", "list").stdout, "and un-registered"
    assert _marked(repo, _tree(repo)) == {"lint"}, "stamped for HEAD's tree, readable from here"
    assert git(repo, "status", "--porcelain").stdout == before, "the desk is untouched"


def test_rev_naming_another_commit_is_judged_in_a_scratch_worktree_of_that_commit(
    repo: Path, runner: Path
):
    first = git(repo, "rev-parse", "HEAD").stdout.strip()
    first_tree = _tree(repo)
    commit_file(repo, "b.txt", "b\n", "second")

    assert "where=scratch" in _gate(repo, runner, "subject", "--rev", first).stdout
    out = _gate(repo, runner, "run", "--rev", first, "lint")

    assert out.returncode == 0, out.stderr
    ((_, ran_in),) = _calls(runner)
    assert ran_in != str(repo)
    assert _marked(repo, first_tree) == {"lint"}
    assert _marked(repo, _tree(repo)) == set(), "HEAD's tree earned nothing — it was not judged"


def test_a_stage_that_dirties_the_tree_earns_no_marker(repo: Path, runner: Path, tmp_path: Path):
    """A stage changing tracked content means the next stages would run on
    something other than the subject; the primitive refuses to certify that."""
    dirtying = tmp_path / "dirtying.sh"
    dirtying.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$1" == "--prepare" ]]; then exit 0; fi\n'
        '[[ "$1" == "lint" ]] && echo changed >> README.md\n'
        "exit 0\n"
    )
    dirtying.chmod(0o755)

    out = _gate(repo, dirtying, "run", "lint", "unit")

    assert out.returncode == 1
    assert "left the tree dirty" in out.stderr
    assert out.stderr.splitlines()[0] == _result(
        repo, "0 cached, 1 ran, FAILED lint (rc=1): left the tree dirty"
    )
    assert " M README.md" in out.stderr, "the block names what the stage changed"
    assert _marked(repo, _tree(repo)) == set()


# ---------------------------------------------------------------------------
# the run narrates for a reader who sees the tail first
# ---------------------------------------------------------------------------


def test_a_run_is_one_block_verdict_first(repo: Path, runner: Path):
    """RESULT first, then one line per stage that ran, the cached stages on ONE
    line, the subject, and the transcript dir — what an agent reading a
    captured stream acts on, in that order."""
    assert _gate(repo, runner, "run", "lint", "unit").returncode == 0

    out = _gate(repo, runner, "run", "lint", "unit", "integration")

    assert out.returncode == 0, out.stderr
    lines = out.stderr.splitlines()
    assert lines[0] == _result(repo, "2 cached, 1 ran, green")
    assert lines[1] == "[gates] integration: green"
    assert lines[2] == "[gates] cached (2): lint unit"
    assert lines[3] == f"{_header(repo)} in place: {repo}"
    transcript = next(ln for ln in lines if ln.startswith("[gates] transcript: "))
    assert Path(transcript.split(": ", 1)[1]).is_dir()
    assert "already green" not in out.stderr, "one line per cached stage is the noise this removes"
    assert "running in" not in out.stderr, "the checkout is named once, in the subject line"


def test_nothing_to_run_is_three_lines_and_mints_no_checkout(repo: Path, runner: Path):
    assert _gate(repo, runner, "run", "lint").returncode == 0
    (repo / "scratch.txt").write_text("untracked\n")

    out = _gate(repo, runner, "run", "lint")

    assert out.returncode == 0, out.stderr
    assert out.stderr.splitlines() == [
        _result(repo, "1 cached, 0 ran, green"),
        "[gates] cached (1): lint",
        _header(repo),
    ]
    assert [c[0] for c in _calls(runner)] == ["lint"], "the second run reached no runner"
    assert "scratch" not in git(repo, "worktree", "list").stdout


def test_pytest_options_reach_only_a_pytest_stage_and_the_xdist_line_precedes_it(
    repo: Path, runner: Path
):
    """A checker stage gets no PYTEST_ADDOPTS and no xdist line; the first pytest
    stage that RUNS gets both. The probe is pointed at this test's own
    interpreter, whose pytest carries xdist — the positive control for the
    absence asserted on `lint`."""
    pytest.importorskip("xdist")
    env = {"PYTHON": sys.executable}

    only_checkers = _gate(repo, runner, "run", "lint", env=env)
    assert only_checkers.returncode == 0, only_checkers.stderr
    assert "xdist" not in only_checkers.stderr
    assert _addopts(runner)["lint"] == ""

    both = _gate(repo, runner, "run", "--fresh", "lint", "unit", env=env)
    assert both.returncode == 0, both.stderr
    assert both.stderr.count("pytest-xdist") == 1
    handed = _addopts(runner)
    assert handed["lint"] == ""
    assert "--tb=line" in handed["unit"] and "-n" in handed["unit"]
    assert "--disable-warnings" in handed["unit"], "a 48-line warnings block is not a verdict"


#: What a red pytest stage prints: pytest's short summary, dots and all.
SUMMARY = "\n".join(
    [
        "..F..F",
        "=== short test summary info ===",
        "FAILED tests/e2e/a.py::test_one - AssertionError: nope",
        "FAILED tests/e2e/b.py::test_two[a-b] - ValueError",
        "ERROR tests/e2e/c.py::test_three",
        "2 failed, 4 passed in 1.2s",
    ]
)


def _rerun(stderr: str) -> str:
    """The command under `re-run just these:`, or '' when the block has none."""
    lines = stderr.splitlines()
    for i, line in enumerate(lines):
        if line == "[gates] re-run just these:":
            return lines[i + 1].strip()
    return ""


def test_a_red_pytest_stage_prints_the_command_that_re_runs_just_its_failures(
    repo: Path, runner: Path
):
    """The ids are pytest's; the interpreter is the gate's, and that is the half
    a hand-built invocation gets wrong. The positive control is the same summary
    under a CHECKER stage: no line there, so the absence is the stage kind and
    not a parser that stopped matching."""
    env = {"PYTHON": sys.executable, "GATES_TEST_RC_UNIT": "1", "GATES_TEST_OUT_UNIT": SUMMARY}

    out = _gate(repo, runner, "run", "unit", env=env)

    assert out.returncode == 1, out.stderr
    lines = out.stderr.splitlines()
    assert lines[0] == _result(repo, "0 cached, 1 ran, FAILED unit (rc=1)")
    assert lines[1] == "[gates] fix unit, then run this again"
    assert lines[2] == "[gates] re-run just these:", "the action follows the verdict"
    assert _rerun(out.stderr) == (
        f"{sys.executable} -m pytest tests/e2e/a.py::test_one "
        "'tests/e2e/b.py::test_two[a-b]' tests/e2e/c.py::test_three"
    ), "every failing id, the gate's own interpreter, and no flags of the run's own"

    checker = _gate(
        repo,
        runner,
        "run",
        "lint",
        env={"PYTHON": sys.executable, "GATES_TEST_RC_LINT": "1", "GATES_TEST_OUT_LINT": SUMMARY},
    )
    assert checker.returncode == 1, checker.stderr
    assert _rerun(checker.stderr) == "", "a checker stage runs no pytest, so it offers no re-run"


def test_a_stage_that_failed_too_widely_offers_no_command_at_all(repo: Path, runner: Path):
    """Past the cap a re-run line is not a command any more. The pair is the
    control: one id under the cap DOES produce a line from the same runner."""
    many = "\n".join(f"FAILED tests/e2e/t{n}.py::test_it - boom" for n in range(21))
    env = {"PYTHON": sys.executable, "GATES_TEST_RC_UNIT": "1", "GATES_TEST_OUT_UNIT": many}

    wide = _gate(repo, runner, "run", "unit", env=env)
    assert wide.returncode == 1, wide.stderr
    assert _rerun(wide.stderr) == ""
    assert any(ln.startswith("[gates] transcript: ") for ln in wide.stderr.splitlines())

    env["GATES_TEST_OUT_UNIT"] = "FAILED tests/e2e/t0.py::test_it - boom"
    narrow = _gate(repo, runner, "run", "--fresh", "unit", env=env)
    assert _rerun(narrow.stderr).endswith("tests/e2e/t0.py::test_it")


# ---------------------------------------------------------------------------
# a dirty desk is judged in a scratch checkout, so the run answers about HEAD
# ---------------------------------------------------------------------------


def test_a_dirty_desk_is_told_that_the_run_judges_committed_content(repo: Path, runner: Path):
    """Green or red, the note sits under the verdict: a red one gets believed
    against a fix that was never there, a green one vouches for one that never
    ran. The clean run in the same test is the control."""
    note = "[gates] the desk is dirty"

    clean = _gate(repo, runner, "run", "lint")
    assert clean.returncode == 0, clean.stderr
    assert note not in clean.stderr, "a clean desk IS the subject — nothing to warn about"

    (repo / "scratch.txt").write_text("uncommitted\n")
    green = _gate(repo, runner, "run", "--fresh", "lint")
    assert green.returncode == 0, green.stderr
    lines = green.stderr.splitlines()
    assert lines[0] == _result(repo, "0 cached, 1 ran, green")
    assert lines[1] == (
        f"{note} — this run judges HEAD ({_short(repo, 'HEAD')}) in a scratch checkout; "
        "uncommitted changes are invisible to it"
    )
    assert green.stderr.count(note) == 1, "one line in a captured stream, not two"

    red = _gate(repo, runner, "run", "--fresh", "lint", env={"GATES_TEST_RC_LINT": "1"})
    assert red.stderr.splitlines()[1].startswith(note), "and it precedes what to do about it"


def test_a_rev_that_is_not_head_is_named_by_its_sha(repo: Path, runner: Path):
    first = git(repo, "rev-parse", "HEAD").stdout.strip()
    commit_file(repo, "b.txt", "b\n", "second")
    (repo / "scratch.txt").write_text("uncommitted\n")

    out = _gate(repo, runner, "run", "--rev", first, "lint")

    assert out.returncode == 0, out.stderr
    assert f"judges {_short(repo, first)} in a scratch checkout" in out.stderr
    assert "judges HEAD" not in out.stderr, "HEAD is not the subject here"


# ---------------------------------------------------------------------------
# the sweep is housekeeping: it may revoke a pass, never break a stamp in flight
# ---------------------------------------------------------------------------


def test_the_sweep_spares_a_fresh_empty_tree_dir_and_reaps_an_old_one(repo: Path, runner: Path):
    """Two runs on a fresh tree race: A makes its tree dir, B's sweep deletes
    it as empty, A's mktemp fails with exit 70. An empty dir younger than an
    hour is somebody's stamp in flight; the aged one is the positive control."""
    store = _store(repo)
    fresh = store / ("f" * 40)
    aged = store / ("a" * 40)
    fresh.mkdir(parents=True)
    aged.mkdir(parents=True)
    two_hours_ago = time.time() - 2 * 3600
    os.utime(aged, (two_hours_ago, two_hours_ago))

    assert _gate(repo, runner, "run", "lint").returncode == 0

    assert fresh.is_dir(), "an empty tree dir younger than an hour survives the sweep"
    assert not aged.exists(), "an old empty one is reaped"


# ---------------------------------------------------------------------------
# argv discipline: a stage runs bare, a typo is not a verdict
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ("check",),
        ("run",),
        ("check", "unit", "-k", "foo"),
        ("run", "unit", "--", "x"),
        ("check", "--fresh", "unit"),
        ("subject", "unit"),
        ("run", "--rev"),
    ],
)
def test_usage_errors_exit_64_and_run_nothing(repo: Path, runner: Path, argv: tuple[str, ...]):
    out = _gate(repo, runner, *argv)

    assert out.returncode == 64, (argv, out.stderr)
    assert _calls(runner) == []


def test_outside_a_repository_is_not_a_verdict(tmp_path: Path, runner: Path):
    bare = tmp_path / "nowhere"
    bare.mkdir()

    out = _gate(bare, runner, "check", "lint")

    assert out.returncode == 70
    assert "not inside a git repository" in out.stderr


def test_the_real_repository_wires_up_without_side_effects():
    """`check` against THIS checkout: the real runner path resolves and the
    verdict is one of the two legal ones. `run` is not exercised here — it
    would stamp the developer's own git dir."""
    out = subprocess.run(
        ["bash", str(GATES), "check", "python-pin"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert out.returncode in (0, 1), out.stderr
    assert out.stdout.split() in ([], ["python-pin"])
