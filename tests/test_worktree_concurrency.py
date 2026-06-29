"""Concurrency tests for worktree state I/O (HATS-121).

Three race scenarios:

* TC-1 — write/write: two processes hammer ``save_state`` on the same
  key with different payloads; the on-disk JSON must always be valid
  and equal to one of the two payloads.
* TC-2 — read/clear: writer thrashes ``save_state`` + ``_clear_state``
  while a reader loops on ``_load_by_key``; the reader must see either
  a fully valid state or ``None``, never a partial/corrupt JSON.
* TC-3 — acquire timeout: holding the lock from one process makes a
  second acquire raise :class:`WorktreeLockError` once the timeout
  elapses.
"""

from __future__ import annotations

import json
import multiprocessing
import subprocess
import time
from pathlib import Path

import pytest

from ai_hats.paths import worktrees_dir
from ai_hats.worktree import (
    GIT_RETRY_MAX,
    MERGE_RETRY_MAX,
    WorktreeLockError,
    WorktreeManager,
    _acquire,
    _acquire_base_branch_lock,
    _acquire_lifecycle_lock,
    _base_lock_key,
    _base_lock_path,
    _format_git_create_error,
    _is_retriable_git_error,
    _is_retriable_merge_error,
    _lifecycle_lock_path,
    _lock_path,
    _retry_git_merge,
    _retry_worktree_add,
    _state_key,
)


pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, check=True)


@pytest.fixture
def git_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    _git(project, "init")
    _git(project, "config", "user.email", "test@test.com")
    _git(project, "config", "user.name", "Test")
    (project / "README.md").write_text("# Test")
    _git(project, "add", ".")
    _git(project, "commit", "-m", "init")
    return project


# ---------------------------------------------------------------------------
# Worker functions (must be top-level for multiprocessing pickling)
# ---------------------------------------------------------------------------


def _writer_worker(project_dir: str, branch: str, marker: str, iterations: int) -> None:
    """Repeatedly save_state with a payload identifiable by ``marker``."""
    project = Path(project_dir)
    mgr = WorktreeManager(project, branch_name=branch, state_dir=worktrees_dir(project))
    mgr.worktree_path = project / f"fake-wt-{marker}"
    mgr._original_branch = "main"
    for _ in range(iterations):
        mgr.save_state()


def _read_clear_writer(project_dir: str, branch: str, iterations: int) -> None:
    """Cycle save_state + _clear_state to maximize read/clear contention."""
    project = Path(project_dir)
    mgr = WorktreeManager(project, branch_name=branch)
    mgr.worktree_path = project / "fake-wt"
    mgr._original_branch = "main"
    for _ in range(iterations):
        mgr.save_state()
        mgr._clear_state()


def _read_loop(project_dir: str, branch: str, iterations: int, errors: list) -> None:
    """Load by key in a loop; record any unexpected exception."""
    project = Path(project_dir)
    key = _state_key(branch)
    for _ in range(iterations):
        try:
            WorktreeManager._load_by_key(project, key)
        except Exception as exc:  # pragma: no cover — should not happen
            errors.append(repr(exc))


def _hold_lock(state_path_str: str, hold_seconds: float, ready_path: str) -> None:
    """Acquire the lock for ``state_path`` and hold it for ``hold_seconds``."""
    state_path = Path(state_path_str)
    with _acquire(state_path, timeout=10.0):
        Path(ready_path).write_text("ready")
        time.sleep(hold_seconds)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_save_state_write_write_race(git_project: Path) -> None:
    """TC-1: parallel writers never corrupt the JSON."""
    branch = "task/hats-121-tc1"
    iterations = 25

    p1 = multiprocessing.Process(
        target=_writer_worker, args=(str(git_project), branch, "alpha", iterations)
    )
    p2 = multiprocessing.Process(
        target=_writer_worker, args=(str(git_project), branch, "beta", iterations)
    )
    p1.start()
    p2.start()
    p1.join(timeout=30)
    p2.join(timeout=30)

    assert p1.exitcode == 0, "writer alpha failed"
    assert p2.exitcode == 0, "writer beta failed"

    state_path = worktrees_dir(git_project) / f"{_state_key(branch)}.json"
    assert state_path.exists()
    data = json.loads(state_path.read_text())  # never raises
    assert data["branch"] == branch
    assert data["worktree_path"] in {
        str(git_project / "fake-wt-alpha"),
        str(git_project / "fake-wt-beta"),
    }


def test_load_during_clear_race(git_project: Path) -> None:
    """TC-2: reader sees only valid state or ``None``, never corruption."""
    branch = "task/hats-121-tc2"
    writer_iters = 60
    reader_iters = 200

    manager = multiprocessing.Manager()
    errors: list = manager.list()

    writer = multiprocessing.Process(
        target=_read_clear_writer, args=(str(git_project), branch, writer_iters)
    )
    reader = multiprocessing.Process(
        target=_read_loop, args=(str(git_project), branch, reader_iters, errors)
    )
    writer.start()
    reader.start()
    writer.join(timeout=30)
    reader.join(timeout=30)

    assert writer.exitcode == 0
    assert reader.exitcode == 0
    assert list(errors) == [], f"reader observed errors: {list(errors)}"


def test_acquire_timeout_raises_worktree_lock_error(git_project: Path, tmp_path: Path) -> None:
    """TC-3: a stuck lock holder triggers WorktreeLockError on second acquire."""
    state_path = worktrees_dir(git_project) / "task-hats-121-tc3.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    ready = tmp_path / "ready.flag"

    holder = multiprocessing.Process(target=_hold_lock, args=(str(state_path), 5.0, str(ready)))
    holder.start()
    try:
        # Wait for the holder to actually grab the lock.
        for _ in range(50):
            if ready.exists():
                break
            time.sleep(0.05)
        assert ready.exists(), "holder did not acquire lock in time"

        with pytest.raises(WorktreeLockError) as ei:
            with _acquire(state_path, timeout=0.5):
                pass
        msg = str(ei.value)
        assert "locked by another process" in msg
        assert state_path.name in msg
    finally:
        holder.join(timeout=10)
        assert holder.exitcode == 0


# ---------------------------------------------------------------------------
# TC-N4..N6 — _retry_worktree_add (HATS-479 L3)
#
# Pure unit tests of the retry helper. No real git, no fixtures. Inject a
# stubbed ``git_runner`` so we can drive exact failure sequences, and inject
# ``sleep=lambda _: None`` so the suite stays fast.
# ---------------------------------------------------------------------------


def _make_called_process_error(stderr: str) -> subprocess.CalledProcessError:
    exc = subprocess.CalledProcessError(
        returncode=128, cmd=["git", "worktree", "add"], stderr=stderr
    )
    return exc


class _StubGit:
    """Callable stub for ``WorktreeManager._git``.

    Pops one entry per call from ``responses``: either ``None`` (success) or
    a ``CalledProcessError`` to raise. Records the number of calls.
    """

    def __init__(self, responses: list[object]) -> None:
        self._responses = list(responses)
        self.calls = 0

    def __call__(self, *args: str) -> None:
        self.calls += 1
        try:
            r = self._responses.pop(0)
        except IndexError as exc:
            raise AssertionError(f"Unexpected extra call to _git (#{self.calls})") from exc
        if isinstance(r, BaseException):
            raise r


def test_retry_succeeds_after_transient_failures(tmp_path: Path) -> None:
    """TC-N4 — 2 retriable failures then success → 3 calls, no raise."""
    stub = _StubGit(
        [
            _make_called_process_error(
                "fatal: could not lock config file .git/config: File exists"
            ),
            _make_called_process_error("fatal: File exists"),
            None,  # success
        ]
    )
    _retry_worktree_add(stub, "task/n4", tmp_path / "wt", sleep=lambda _: None)
    assert stub.calls == 3


def test_retry_exhausted_raises_last_error(tmp_path: Path) -> None:
    """TC-N5 — all attempts retriable-fail → raises CalledProcessError."""
    stderr = "fatal: could not lock config file .git/config: File exists"
    stub = _StubGit([_make_called_process_error(stderr) for _ in range(GIT_RETRY_MAX)])
    with pytest.raises(subprocess.CalledProcessError) as ei:
        _retry_worktree_add(stub, "task/n5", tmp_path / "wt", sleep=lambda _: None)
    assert stub.calls == GIT_RETRY_MAX
    assert "could not lock config file" in (ei.value.stderr or "")


def test_retry_fails_fast_on_non_retriable(tmp_path: Path) -> None:
    """TC-N6 — non-retriable stderr → 1 call, no retries."""
    stub = _StubGit([_make_called_process_error("fatal: not a valid object name: HEAD")])
    with pytest.raises(subprocess.CalledProcessError):
        _retry_worktree_add(stub, "task/n6", tmp_path / "wt", sleep=lambda _: None)
    assert stub.calls == 1


def test_is_retriable_git_error_classification() -> None:
    """Direct check on the classifier — covers each pattern + a non-match."""
    retriable = [
        "fatal: could not lock config file .git/config: File exists",
        "fatal: File exists",
        "error: Unable to create '.git/worktrees/X/locked'",
    ]
    non_retriable = [
        "fatal: not a valid object name: HEAD",
        "fatal: A branch named 'task/x' already exists.",
        "",
    ]
    for s in retriable:
        assert _is_retriable_git_error(_make_called_process_error(s)), s
    for s in non_retriable:
        assert not _is_retriable_git_error(_make_called_process_error(s)), s


def test_format_git_create_error_special_cases_already_exists() -> None:
    exc = _make_called_process_error("fatal: A branch named 'task/x' already exists.")
    msg = _format_git_create_error(exc, "task/x")
    assert "task/x" in msg
    assert "already exists" in msg


def test_format_git_create_error_generic_fallback() -> None:
    exc = _make_called_process_error("fatal: something else broke")
    msg = _format_git_create_error(exc, "task/x")
    assert "task/x" in msg
    assert "something else broke" in msg


# ---------------------------------------------------------------------------
# TC-N1..N3 — real-git concurrency scenarios for WorktreeManager.create
# (HATS-479 L1+L2+L4)
# ---------------------------------------------------------------------------


def _create_worker(
    project_dir: str,
    branch: str,
    result_dict: dict,
    key: str,
) -> None:
    """Child process: run WorktreeManager.create + save_state, record outcome.

    Stores ``{"path": str|None, "error": str|None}`` under ``key`` so the
    parent test can assert exactly one winner / one loser.
    """
    from ai_hats.worktree import WorktreeCreateError

    project = Path(project_dir)
    mgr = WorktreeManager(project, branch_name=branch, state_dir=worktrees_dir(project))
    try:
        path = mgr.create()
        mgr.save_state()
        result_dict[key] = {"path": str(path), "error": None}
    except WorktreeCreateError as exc:
        result_dict[key] = {"path": None, "error": str(exc)}
    except Exception as exc:  # pragma: no cover — should be wrapped
        result_dict[key] = {
            "path": None,
            "error": f"UNEXPECTED {type(exc).__name__}: {exc}",
        }


def _list_leftover_tempdirs(prefix: str) -> list[Path]:
    """All /tmp dirs matching a wt prefix that still exist on disk."""
    from tempfile import gettempdir

    root = Path(gettempdir())
    return [p for p in root.iterdir() if p.name.startswith(prefix)]


def test_parallel_create_same_branch_exactly_one_winner(
    git_project: Path,
) -> None:
    """TC-N1: 2 procs create same branch → 1 success, 1 friendly error,
    no leaked tempdir, exactly 1 branch on disk."""
    branch = "task/hats-479-n1"
    prefix = f"ai-hats-wt-{branch.replace('/', '-')}-"

    pre_existing_dirs = {p.name for p in _list_leftover_tempdirs(prefix)}

    manager = multiprocessing.Manager()
    results = manager.dict()

    p1 = multiprocessing.Process(
        target=_create_worker, args=(str(git_project), branch, results, "p1")
    )
    p2 = multiprocessing.Process(
        target=_create_worker, args=(str(git_project), branch, results, "p2")
    )
    p1.start()
    p2.start()
    p1.join(timeout=30)
    p2.join(timeout=30)
    assert p1.exitcode == 0 and p2.exitcode == 0

    outcomes = [dict(results["p1"]), dict(results["p2"])]
    winners = [o for o in outcomes if o["error"] is None]
    losers = [o for o in outcomes if o["error"] is not None]
    assert len(winners) == 1, f"expected 1 winner, got {outcomes}"
    assert len(losers) == 1, f"expected 1 loser, got {outcomes}"

    # Loser sees a friendly message — not an opaque CalledProcessError.
    err = losers[0]["error"]
    assert "already exists" in err.lower(), err
    assert "CalledProcessError" not in err

    # Exactly one branch.
    branches = subprocess.run(
        ["git", "branch", "--list", branch],
        cwd=str(git_project),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert branches.count(branch) == 1, branches

    # State JSON exists exactly once.
    state_path = worktrees_dir(git_project) / f"{_state_key(branch)}.json"
    assert state_path.exists()

    # No new leaked tempdirs from the loser.
    after = {p.name for p in _list_leftover_tempdirs(prefix)}
    new_dirs = after - pre_existing_dirs
    # The winner's dir is still around (real worktree). At most 1 new dir.
    assert len(new_dirs) <= 1, f"leaked tempdirs: {new_dirs}"
    if new_dirs:
        # The remaining dir must be the winner's worktree.
        assert str(list(new_dirs)[0]) in winners[0]["path"]


def test_parallel_create_different_branches_both_succeed(
    git_project: Path,
) -> None:
    """TC-N2: parallel create on different branches → both succeed."""
    branch_a = "task/hats-479-n2-a"
    branch_b = "task/hats-479-n2-b"

    manager = multiprocessing.Manager()
    results = manager.dict()

    p1 = multiprocessing.Process(
        target=_create_worker, args=(str(git_project), branch_a, results, "p1")
    )
    p2 = multiprocessing.Process(
        target=_create_worker, args=(str(git_project), branch_b, results, "p2")
    )
    p1.start()
    p2.start()
    p1.join(timeout=30)
    p2.join(timeout=30)
    assert p1.exitcode == 0 and p2.exitcode == 0

    for k in ("p1", "p2"):
        outcome = dict(results[k])
        assert outcome["error"] is None, outcome
        assert outcome["path"] is not None

    # Both state files exist.
    assert (worktrees_dir(git_project) / f"{_state_key(branch_a)}.json").exists()
    assert (worktrees_dir(git_project) / f"{_state_key(branch_b)}.json").exists()


# TC-N3 (test_create_failure_cleans_tempdir_preserves_pre_existing_branch)
# was deleted in HATS-603. It asserted the pre-HATS-517 contract — that
# `create()` RAISES on a pre-existing branch. HATS-517 Case A changed that:
# a branch with no owning worktree is now ATTACHED to a new linked worktree
# (`git worktree add <path> <branch>`, no `-b`), so create() succeeds. The
# current contract is covered by tests/test_worktree.py::
# TestBranchExistsClassifier (Case A attach + full lifecycle) and the e2e
# Case A test; L4 tempdir-cleanup-on-failure is covered by TC-N1
# (test_parallel_create_same_branch_exactly_one_winner).


# ---------------------------------------------------------------------------
# TC-N7 — `task transition execute` adopt-on-race (HATS-479 + state.py)
# ---------------------------------------------------------------------------


def _setup_worktree_worker(project_dir: str, task_id: str, result_dict: dict, key: str) -> None:
    """Child process: invoke TaskManager._setup_worktree, record outcome."""
    from datetime import datetime, timezone

    from ai_hats.models import TaskCard, TaskState
    from ai_hats.state import TaskManager

    tm = TaskManager(Path(project_dir), prefix="HATS")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    task = TaskCard(
        id=task_id,
        title="t",
        state=TaskState.EXECUTE,
        description="",
        priority="medium",
        role="",
        reviewer="user",
        parent_task="",
        depends_on=[],
        tags=[],
        created=now,
        updated=now,
    )
    try:
        path = tm._setup_worktree(task)
        result_dict[key] = {"path": str(path) if path else None, "error": None}
    except Exception as exc:
        result_dict[key] = {
            "path": None,
            "error": f"{type(exc).__name__}: {exc}",
        }


def test_setup_worktree_concurrent_adopts_peer(git_project: Path) -> None:
    """TC-N7: parallel transition `execute` for same task → both succeed,
    both return the SAME worktree path (one creates, one adopts), one branch."""
    task_id = "HATS-N7"
    branch = f"task/{task_id.lower()}"

    manager = multiprocessing.Manager()
    results = manager.dict()

    p1 = multiprocessing.Process(
        target=_setup_worktree_worker,
        args=(str(git_project), task_id, results, "p1"),
    )
    p2 = multiprocessing.Process(
        target=_setup_worktree_worker,
        args=(str(git_project), task_id, results, "p2"),
    )
    p1.start()
    p2.start()
    p1.join(timeout=30)
    p2.join(timeout=30)
    assert p1.exitcode == 0 and p2.exitcode == 0

    o1 = dict(results["p1"])
    o2 = dict(results["p2"])
    assert o1["error"] is None, o1
    assert o2["error"] is None, o2
    # Both processes converge on the SAME worktree path.
    assert o1["path"] == o2["path"], (o1, o2)
    assert o1["path"] is not None

    # Exactly one branch exists.
    branches = subprocess.run(
        ["git", "branch", "--list", branch],
        cwd=str(git_project),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert branches.count(branch) == 1, branches


# ---------------------------------------------------------------------------
# HATS-481 L3' — _retry_git_merge unit tests
#
# Pure unit tests of the merge-retry helper. Stubbed git_runner; sleep
# injected as no-op so the suite stays fast.
# ---------------------------------------------------------------------------


def test_retry_git_merge_succeeds_after_transient_failures(tmp_path: Path) -> None:
    """TC-N8: 2 retriable failures (index.lock contention) then success."""
    stub = _StubGit(
        [
            _make_called_process_error(
                "fatal: Unable to create '.git/index.lock': File exists.\n"
                "Another git process seems to be running in this repository."
            ),
            _make_called_process_error("fatal: Unable to create '.git/index.lock': File exists."),
            None,  # success
        ]
    )
    _retry_git_merge(stub, "merge", "--no-ff", "task/x", sleep=lambda _: None)
    assert stub.calls == 3


def test_retry_git_merge_exhausted_raises_last_error(tmp_path: Path) -> None:
    """TC-N9: all MERGE_RETRY_MAX retriable failures → raises last."""
    stderr = (
        "fatal: Unable to create '.git/index.lock': File exists.\n"
        "Another git process seems to be running in this repository."
    )
    stub = _StubGit([_make_called_process_error(stderr) for _ in range(MERGE_RETRY_MAX)])
    with pytest.raises(subprocess.CalledProcessError) as ei:
        _retry_git_merge(stub, "merge", "--no-ff", "task/y", sleep=lambda _: None)
    assert stub.calls == MERGE_RETRY_MAX
    assert "index.lock" in (ei.value.stderr or "").lower()


def test_retry_git_merge_fails_fast_on_non_retriable(tmp_path: Path) -> None:
    """TC-N10: non-retriable stderr (e.g. merge conflict) → 1 call, no retry."""
    stub = _StubGit(
        [
            _make_called_process_error(
                "Automatic merge failed; fix conflicts and then commit the result."
            )
        ]
    )
    with pytest.raises(subprocess.CalledProcessError):
        _retry_git_merge(stub, "merge", "--no-ff", "task/z", sleep=lambda _: None)
    assert stub.calls == 1


def test_is_retriable_merge_error_classification() -> None:
    """Each pattern in _RETRIABLE_MERGE_STDERR_PATTERNS matches; clear cases don't."""
    retriable = [
        "fatal: Unable to create '.git/index.lock': File exists",
        "Another git process seems to be running in this repository",
        "error: could not lock config file .git/config",
        "fatal: Unable to create '.git/HEAD.lock': File exists",
    ]
    non_retriable = [
        "Automatic merge failed; fix conflicts and then commit the result.",
        "fatal: refusing to merge unrelated histories",
        "fatal: not a valid object name: HEAD",
        "",
    ]
    for s in retriable:
        assert _is_retriable_merge_error(_make_called_process_error(s)), s
    for s in non_retriable:
        assert not _is_retriable_merge_error(_make_called_process_error(s)), s


# ---------------------------------------------------------------------------
# HATS-481 L1' — _acquire_base_branch_lock
# ---------------------------------------------------------------------------


def test_base_lock_key_sanitization() -> None:
    """TC-N11: branch names sanitized consistently with _state_key."""
    assert _base_lock_key("master") == "master"
    assert _base_lock_key("main") == "main"
    assert _base_lock_key("feat/foo") == "feat-foo"
    assert _base_lock_key("Develop") == "develop"
    assert _base_lock_key("release/2026-Q2") == "release-2026-q2"


def test_base_lock_path_under_state_dir(git_project: Path) -> None:
    """Lock file lives next to other worktree state, under .agent."""
    path = _base_lock_path(worktrees_dir(git_project), "master")
    assert path.name == ".base-master.lock"
    assert path.parent == worktrees_dir(git_project)


def _hold_base_lock(project_dir_str: str, base: str, hold_s: float, ready: str) -> None:
    """Acquire L1' for ``base`` and hold for ``hold_s`` seconds."""
    with _acquire_base_branch_lock(worktrees_dir(Path(project_dir_str)), base):
        Path(ready).write_text("ready")
        time.sleep(hold_s)


def test_base_lock_serializes_same_base(git_project: Path, tmp_path: Path) -> None:
    """A held base lock makes the second acquire wait until the first releases.

    Sanity for L1' — without filelock semantics on the right key,
    concurrent merges into the same base would race on .git/index.lock.
    """
    ready = tmp_path / "ready.flag"
    holder = multiprocessing.Process(
        target=_hold_base_lock, args=(str(git_project), "master", 0.5, str(ready))
    )
    holder.start()
    try:
        for _ in range(50):
            if ready.exists():
                break
            time.sleep(0.02)
        assert ready.exists(), "holder failed to grab base lock"

        # Second acquire with a short timeout MUST raise — holder is still active.
        with pytest.raises(WorktreeLockError):
            with _acquire_base_branch_lock(worktrees_dir(git_project), "master", timeout=0.1):
                pass  # pragma: no cover
    finally:
        holder.join(timeout=5)
        assert holder.exitcode == 0


def test_base_lock_independent_per_base(git_project: Path, tmp_path: Path) -> None:
    """Locks on different base refs do NOT serialize each other."""
    ready = tmp_path / "ready.flag"
    holder = multiprocessing.Process(
        target=_hold_base_lock, args=(str(git_project), "master", 1.0, str(ready))
    )
    holder.start()
    try:
        for _ in range(50):
            if ready.exists():
                break
            time.sleep(0.02)
        assert ready.exists()

        # Different base → must NOT block.
        t0 = time.monotonic()
        with _acquire_base_branch_lock(worktrees_dir(git_project), "develop"):
            assert time.monotonic() - t0 < 0.2, "different-base lock blocked"
    finally:
        holder.join(timeout=5)
        assert holder.exitcode == 0


# ---------------------------------------------------------------------------
# TC-N12: parallel merge into same base — both succeed under L1'
# ---------------------------------------------------------------------------


def _create_and_merge_worker(
    project_dir: str,
    branch: str,
    payload_file: str,
    payload_content: str,
    result_dict: dict,
    key: str,
) -> None:
    """Child process: create worktree, commit a unique file, merge.

    Exercises the full L1'+L3' merge path. Result captures whether the
    merge raised and what HEAD looks like afterwards.

    ``accept_drift=True`` (HATS-602): both workers branch off the same base
    tip, so whichever merges SECOND legitimately sees the base advance
    under it and the HATS-457 drift guard refuses by default — a real,
    correct refusal, not a race we want to test here. Accepting drift
    models the post-457 workflow (the second agent re-verified against the
    moved base) so the test can isolate the property it actually asserts:
    the L1' base lock lets two index.lock-contending merges into the same
    base BOTH complete with both commits landing — no corruption, no lost
    merge.

    No retry is needed on ``WorktreeMainRepoMidMergeError``: HATS-602 moved
    that guard INSIDE the base lock (``_refuse_if_mid_merge``), so the
    second merger no longer observes the first's transient ``MERGE_HEAD``.
    This test is the regression guard for that move — revert it (check back
    in ``merge()`` outside the lock) and this test flakes again.
    """
    project = Path(project_dir)
    mgr = WorktreeManager(project, branch_name=branch)
    try:
        wt_path = mgr.create()
        mgr.save_state()
        # Use raw git (no commit hooks in this test fixture).
        subprocess.run(
            ["git", "config", "user.email", "tc@n12.test"],
            cwd=str(wt_path),
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "TC-N12"],
            cwd=str(wt_path),
            check=True,
        )
        (wt_path / payload_file).write_text(payload_content)
        subprocess.run(
            ["git", "add", payload_file],
            cwd=str(wt_path),
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", f"wt: {payload_file}"],
            cwd=str(wt_path),
            check=True,
            capture_output=True,
        )
        mgr.merge(accept_drift=True)  # HATS-602: see worker docstring
        result_dict[key] = {"error": None}
    except Exception as exc:
        result_dict[key] = {"error": f"{type(exc).__name__}: {exc}"}


def test_parallel_merges_into_same_base_both_succeed(git_project: Path) -> None:
    """TC-N12: two parallel `WorktreeManager.merge` into the same base ref
    both succeed and both commits land in master.

    Without L1' this would race on `.git/index.lock` — L3' retry covers
    short windows but is not a guarantee under multi-second hold; L1'
    provides the guarantee.

    Workers pass ``accept_drift=True`` (HATS-602): both branch off the same
    base tip, so the second merge legitimately drifts (the HATS-457 guard
    correctly refuses by default). Drift is orthogonal to the L1' property
    under test; accepting it lets the test assert that property cleanly.
    See `_create_and_merge_worker` for the full rationale."""
    branch_a = "task/hats-481-n12-a"
    branch_b = "task/hats-481-n12-b"

    manager = multiprocessing.Manager()
    results = manager.dict()

    p1 = multiprocessing.Process(
        target=_create_and_merge_worker,
        args=(
            str(git_project),
            branch_a,
            "file-a.txt",
            "alpha\n",
            results,
            "p1",
        ),
    )
    p2 = multiprocessing.Process(
        target=_create_and_merge_worker,
        args=(
            str(git_project),
            branch_b,
            "file-b.txt",
            "beta\n",
            results,
            "p2",
        ),
    )
    p1.start()
    p2.start()
    p1.join(timeout=60)
    p2.join(timeout=60)
    assert p1.exitcode == 0 and p2.exitcode == 0

    o1 = dict(results["p1"])
    o2 = dict(results["p2"])
    assert o1["error"] is None, o1
    assert o2["error"] is None, o2

    # Both unique files on the base branch.
    assert (git_project / "file-a.txt").read_text() == "alpha\n"
    assert (git_project / "file-b.txt").read_text() == "beta\n"


# ---------------------------------------------------------------------------
# TC-N14..N16 — HATS-480 lifecycle-lock helpers (pure unit, no real git)
# ---------------------------------------------------------------------------


def test_lifecycle_lock_path_is_sibling_of_state(tmp_path: Path) -> None:
    """TC-N14: lifecycle lock filename = <state>.json.lifecycle.lock — and is
    DISTINCT from the HATS-121 state-lock (``<state>.json.lock``).

    Distinct lock files matter: state-lock is held only across millisecond-
    scoped JSON I/O, lifecycle-lock spans tens of seconds (merge + fetch +
    remove). Reusing the same lock would block ``wt list`` / ``load_for_branch``
    on peers throughout a merge.
    """
    state = tmp_path / "task-hats-480.json"
    lc = _lifecycle_lock_path(state)
    assert lc.name == "task-hats-480.json.lifecycle.lock"
    assert lc.parent == state.parent
    # Must NOT collide with the HATS-121 state-lock.
    assert lc != _lock_path(state)


def test_acquire_lifecycle_lock_timeout_raises(tmp_path: Path) -> None:
    """TC-N15: a held lifecycle lock makes the second acquire raise
    WorktreeLockError after the timeout elapses.

    Mirrors the existing HATS-121 / HATS-481 timeout tests — uses a child
    process to hold the lock so the second acquire actually sees fcntl
    contention (in-process re-entry would succeed under filelock's reentrant
    semantics).
    """
    state = tmp_path / "state.json"
    ready = tmp_path / "ready.flag"
    holder = multiprocessing.Process(
        target=_hold_lifecycle_lock,
        args=(str(state), 0.5, str(ready)),
    )
    holder.start()
    try:
        for _ in range(50):
            if ready.exists():
                break
            time.sleep(0.02)
        assert ready.exists(), "holder failed to grab lifecycle lock"

        with pytest.raises(WorktreeLockError) as exc_info:
            with _acquire_lifecycle_lock(state, timeout=0.1):
                pass  # pragma: no cover
        # Error message points at the lock file for manual recovery (479/481 pattern).
        assert str(_lifecycle_lock_path(state)) in str(exc_info.value)
    finally:
        holder.join(timeout=5)
        assert holder.exitcode == 0


def _hold_lifecycle_lock(state_path_str: str, hold_s: float, ready: str) -> None:
    """Child-process worker — must be top-level for multiprocessing pickling."""
    state = Path(state_path_str)
    state.parent.mkdir(parents=True, exist_ok=True)
    with _acquire_lifecycle_lock(state):
        Path(ready).write_text("ready")
        time.sleep(hold_s)


# TC-N16 (helper `_load_state_or_none`) was dropped in review: the
# idempotency gate inside merge()/discard()/cleanup() uses
# ``worktree_path.exists()`` directly — cheaper and matches the actual
# semantic ("peer's _remove_worktree happened"). The helper was an
# orphan; deleted alongside this test.


# ---------------------------------------------------------------------------
# TC-N17 — parallel merge + discard on the SAME branch (HATS-480 R-03)
# ---------------------------------------------------------------------------


def _setup_wt_for_lifecycle_race(project_dir: Path, branch: str, payload_file: str) -> None:
    """Create + populate a worktree, save state, leave it ready for merge/discard.

    Shared setup for TC-N17/N18: the race test only exercises merge()/discard(),
    NOT create(). Doing setup in the parent process keeps the race timeline
    tight (no fork/exec overhead between create and the lifecycle op).
    """
    mgr = WorktreeManager(project_dir, branch_name=branch)
    wt_path = mgr.create()
    mgr.save_state()
    subprocess.run(["git", "config", "user.email", "tc@n17.test"], cwd=str(wt_path), check=True)
    subprocess.run(["git", "config", "user.name", "TC-N17"], cwd=str(wt_path), check=True)
    (wt_path / payload_file).write_text("payload\n")
    subprocess.run(["git", "add", payload_file], cwd=str(wt_path), check=True)
    subprocess.run(
        ["git", "commit", "-m", f"wt: {payload_file}"],
        cwd=str(wt_path),
        check=True,
        capture_output=True,
    )


def _merge_worker(project_dir: str, branch: str, results: dict, key: str, barrier) -> None:
    """Child process: load worktree by branch, sync on barrier, merge it.

    Loading BEFORE the barrier ensures both workers have a valid manager
    instance and reach the race window simultaneously — without this, a
    fast peer could fully complete before the slow peer even loads state,
    bypassing the R-03 race we're trying to exercise.
    """
    project = Path(project_dir)
    try:
        mgr = WorktreeManager.load_for_branch(project, branch)
        if mgr is None:
            results[key] = {"error": "state was None at pre-race load"}
            return
        barrier.wait(timeout=10)
        mgr.merge()
        results[key] = {"error": None, "op": "merge"}
    except Exception as exc:
        results[key] = {"error": f"{type(exc).__name__}: {exc}", "op": "merge"}


def _discard_worker(project_dir: str, branch: str, results: dict, key: str, barrier) -> None:
    """Child process: load worktree by branch, sync on barrier, discard it."""
    project = Path(project_dir)
    try:
        mgr = WorktreeManager.load_for_branch(project, branch)
        if mgr is None:
            results[key] = {"error": "state was None at pre-race load"}
            return
        barrier.wait(timeout=10)
        mgr.discard()
        results[key] = {"error": None, "op": "discard"}
    except Exception as exc:
        results[key] = {"error": f"{type(exc).__name__}: {exc}", "op": "discard"}


def test_parallel_merge_and_discard_same_branch_consistent_outcome(
    git_project: Path,
) -> None:
    """TC-N17: parallel ``merge()`` + ``discard()`` on the same wt branch
    → one wins cleanly, the other no-ops idempotently. NO half-merged state.

    R-03 from HATS-476: without the lifecycle lock, the second op walks
    into the middle of the first (``_remove_worktree`` mid-merge, or
    ``branch -D`` while merge is still resolving refs). Outcome was
    non-deterministic — either half-merged commit on master, or opaque
    ``CalledProcessError``, or branch graveyard.

    Under the lifecycle lock, two outcomes are valid and consistent:
      * Merge wins: file present on base branch, HEAD advanced, branch
        deleted, state cleared.
      * Discard wins: file absent on base, HEAD unchanged, branch
        deleted, state cleared.

    Either way: NO half-state, BOTH workers return error=None, branch
    deleted exactly once, state cleared exactly once.
    """
    branch = "task/hats-480-n17"
    _setup_wt_for_lifecycle_race(git_project, branch, "feature.txt")

    # Snapshot the base branch — used to decide which side won.
    initial_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(git_project),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    manager = multiprocessing.Manager()
    results = manager.dict()
    barrier = manager.Barrier(2)

    p_merge = multiprocessing.Process(
        target=_merge_worker,
        args=(str(git_project), branch, results, "merge", barrier),
    )
    p_discard = multiprocessing.Process(
        target=_discard_worker,
        args=(str(git_project), branch, results, "discard", barrier),
    )
    p_merge.start()
    p_discard.start()
    p_merge.join(timeout=60)
    p_discard.join(timeout=60)
    assert p_merge.exitcode == 0, "merge worker died"
    assert p_discard.exitcode == 0, "discard worker died"

    o_m = dict(results["merge"])
    o_d = dict(results["discard"])
    assert o_m["error"] is None, f"merge raised: {o_m}"
    assert o_d["error"] is None, f"discard raised: {o_d}"

    # State cleared exactly once — both finished, no leftover JSON.
    assert WorktreeManager.load_for_branch(git_project, branch) is None

    # Branch deleted exactly once (whichever winner reached _delete_branch).
    branch_ls = subprocess.run(
        ["git", "branch", "--list", branch],
        cwd=str(git_project),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert branch_ls == "", f"branch should be deleted, got: {branch_ls!r}"

    # Final base-branch state: either merge won (file present + HEAD advanced)
    # or discard won (no file + HEAD unchanged). NEVER half-merged.
    feature_path = git_project / "feature.txt"
    final_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(git_project),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if feature_path.exists():
        assert final_head != initial_head, "file present but HEAD didn't advance"
    else:
        assert final_head == initial_head, (
            f"file absent but HEAD advanced ({initial_head[:8]} -> {final_head[:8]}) "
            f"— half-merged state!"
        )


# ---------------------------------------------------------------------------
# TC-N18 — parallel discard + discard on the SAME branch (idempotency)
# ---------------------------------------------------------------------------


def test_parallel_discard_same_branch_idempotent(git_project: Path) -> None:
    """TC-N18: two parallel ``discard()`` calls on the same wt branch both
    exit cleanly; neither raises despite only one of them actually doing
    the destructive work.

    Without the lifecycle lock, the second discard hits a partially
    torn-down state: ``_remove_worktree`` fails because the dir is gone,
    falls back to ``shutil.rmtree(ignore_errors=True)``; ``branch -D``
    fails (already deleted), swallowed at DEBUG (B-02). The user sees
    inconsistent CLI exit codes and silent failures in the log — a
    discoverability bug.

    Under the lock + idempotency check, the second worker observes
    ``worktree_path`` gone and no-ops with a single INFO log line.
    """
    branch = "task/hats-480-n18"
    _setup_wt_for_lifecycle_race(git_project, branch, "discardable.txt")

    manager = multiprocessing.Manager()
    results = manager.dict()
    barrier = manager.Barrier(2)

    p1 = multiprocessing.Process(
        target=_discard_worker,
        args=(str(git_project), branch, results, "d1", barrier),
    )
    p2 = multiprocessing.Process(
        target=_discard_worker,
        args=(str(git_project), branch, results, "d2", barrier),
    )
    p1.start()
    p2.start()
    p1.join(timeout=60)
    p2.join(timeout=60)
    assert p1.exitcode == 0, "discard p1 worker died"
    assert p2.exitcode == 0, "discard p2 worker died"

    o1 = dict(results["d1"])
    o2 = dict(results["d2"])
    assert o1["error"] is None, f"discard p1 raised: {o1}"
    assert o2["error"] is None, f"discard p2 raised: {o2}"

    # State + branch cleaned up exactly once.
    assert WorktreeManager.load_for_branch(git_project, branch) is None
    branch_ls = subprocess.run(
        ["git", "branch", "--list", branch],
        cwd=str(git_project),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert branch_ls == "", f"branch should be deleted, got: {branch_ls!r}"
