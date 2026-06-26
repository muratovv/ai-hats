"""HATS-823 / ADR-0013 — worktree lifecycle hook execution through the bundle.

Exercises the real WorktreeManager + the real ai-hats hook-running bundle
(``wt_lifecycle.HOOK_LIFECYCLE``) against a real git repo: wt_in runs after
checkout; wt_out runs fail-closed before every teardown; --skip-hooks forces;
cleanup() preserves on hook failure; on-filtering; create→persist→teardown
round-trip; pre-upgrade (carry-less) state warns instead of crashing.

Post-ADR-0013 (P1, HATS-849) the *core* is hook-agnostic: a bare manager runs
NO hooks, so every manager here is constructed / loaded with the real bundle
(``_mgr`` / ``lifecycle=HOOK_LIFECYCLE``). Fail-closed now raises the core
``WorktreeTeardownAborted`` with a ``WorktreeHookError`` ``__cause__``.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from ai_hats.paths import managed_wt_hook_filename, worktrees_dir, wt_hooks_dir
from ai_hats.worktree import WorktreeManager, WorktreeTeardownAborted
from ai_hats.wt_lifecycle import HOOK_LIFECYCLE, WorktreeHookError


def _mgr(project: Path, branch: str) -> WorktreeManager:
    """A manager wired with the real ai-hats hook-running bundle (ADR-0013)."""
    return WorktreeManager(project, branch_name=branch, lifecycle=HOOK_LIFECYCLE)


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True
    )


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


def _place_hook(project: Path, skill: str, basename: str, body: str) -> None:
    dest = wt_hooks_dir(project) / managed_wt_hook_filename(skill, basename)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("#!/usr/bin/env bash\nset -e\n" + body)
    dest.chmod(0o755)


def _carry_out(skill="s", script="drain.sh", on=("merge", "discard", "cleanup")):
    return {"wt_out": [{"skill": skill, "script": script, "on": list(on)}]}


def _commit_in_wt(wt: Path) -> None:
    (wt / "work.txt").write_text("x")
    _git(wt, "add", ".")
    _git(wt, "commit", "-m", "work")


def test_wt_in_runs_after_checkout(git_project, tmp_path):
    sentinel = tmp_path / "seeded"
    _place_hook(git_project, "s", "seed.sh", f'touch "{sentinel}"\n')
    mgr = _mgr(git_project, "task/a")
    mgr.create(wt_hooks={"wt_in": [{"skill": "s", "script": "seed.sh"}]})
    assert sentinel.exists()


def test_wt_out_runs_on_merge_then_tears_down(git_project, tmp_path):
    sentinel = tmp_path / "drained"
    _place_hook(git_project, "s", "drain.sh", f'touch "{sentinel}"\n')
    mgr = _mgr(git_project, "task/b")
    wt = mgr.create(wt_hooks=_carry_out())
    _commit_in_wt(wt)
    mgr.merge()
    assert sentinel.exists()
    assert not wt.exists()  # torn down after the hook ran


def test_failing_wt_out_aborts_merge_fail_closed(git_project):
    _place_hook(git_project, "s", "drain.sh", "exit 1\n")
    mgr = _mgr(git_project, "task/c")
    wt = mgr.create(wt_hooks=_carry_out())
    _commit_in_wt(wt)
    with pytest.raises(WorktreeTeardownAborted) as ei:
        mgr.merge()
    assert isinstance(ei.value.__cause__, WorktreeHookError)  # hook detail rides as cause
    assert wt.exists()  # preserved
    assert WorktreeManager.branch_exists(git_project, "task/c")


def test_failing_wt_out_aborts_discard_fail_closed(git_project):
    _place_hook(git_project, "s", "drain.sh", "exit 1\n")
    mgr = _mgr(git_project, "task/d")
    wt = mgr.create(wt_hooks=_carry_out())
    with pytest.raises(WorktreeTeardownAborted) as ei:
        mgr.discard()
    assert isinstance(ei.value.__cause__, WorktreeHookError)
    assert wt.exists()


def test_skip_hooks_forces_discard_through(git_project):
    _place_hook(git_project, "s", "drain.sh", "exit 1\n")
    mgr = _mgr(git_project, "task/e")
    wt = mgr.create(wt_hooks=_carry_out())
    mgr.discard(skip_hooks=True)  # must not raise
    assert not wt.exists()


def test_on_filtering_skips_non_matching_event(git_project, tmp_path):
    sentinel = tmp_path / "ran"
    _place_hook(git_project, "s", "drain.sh", f'touch "{sentinel}"\n')
    mgr = _mgr(git_project, "task/f")
    wt = mgr.create(wt_hooks=_carry_out(on=("discard",)))  # not merge
    _commit_in_wt(wt)
    mgr.merge()
    assert not sentinel.exists()  # hook bound to discard only
    assert not wt.exists()  # merge still tore down


def test_cleanup_preserves_worktree_on_hook_failure(git_project):
    # S19 (cleanup-contract guard): a cleanup hook-fail must preserve the dir
    # AND propagate NO exception — the abort is suppressed (warn + return). The
    # call sits OUTSIDE pytest.raises, so a propagated abort fails the test.
    _place_hook(git_project, "s", "drain.sh", "exit 1\n")
    mgr = _mgr(git_project, "task/g")
    wt = mgr.create(wt_hooks=_carry_out())
    mgr.cleanup()  # must NOT raise (auto path)
    assert wt.exists()  # preserved, not removed


def test_persistence_roundtrip_runs_create_time_hooks(git_project, tmp_path):
    sentinel = tmp_path / "drained2"
    _place_hook(git_project, "s", "drain.sh", f'touch "{sentinel}"\n')
    mgr = _mgr(git_project, "task/h")
    wt = mgr.create(wt_hooks=_carry_out())
    mgr.save_state()
    # Fresh manager loaded from persisted state (separate-CLI-invocation path),
    # re-attaching the bundle as the teardown call sites do (ADR-0013 D3).
    loaded = WorktreeManager.load_for_branch(
        git_project, "task/h", lifecycle=HOOK_LIFECYCLE
    )
    assert loaded is not None
    assert loaded._wt_hooks == _carry_out()
    _commit_in_wt(wt)
    loaded.merge()
    assert sentinel.exists()


def test_legacy_state_without_wt_hooks_warns_and_does_not_crash(
    git_project, caplog
):
    mgr = _mgr(git_project, "task/i")
    wt = mgr.create()
    mgr.save_state()
    # Simulate a pre-upgrade state file: strip the wt_hooks key.
    state_file = next(worktrees_dir(git_project).glob("*.json"))
    data = json.loads(state_file.read_text())
    data.pop("wt_hooks", None)
    state_file.write_text(json.dumps(data))

    loaded = WorktreeManager.load_for_branch(
        git_project, "task/i", lifecycle=HOOK_LIFECYCLE
    )
    assert loaded is not None
    assert loaded._wt_hooks_legacy is True
    with caplog.at_level("WARNING"):
        loaded.discard()  # no crash
    assert not wt.exists()
    assert any("predates wt-hooks" in r.message for r in caplog.records)


# --- ADR-0013 scenario-matrix cells added by P1 (S3/S8/S10/S12/S20) ----------


def test_wt_in_failure_does_not_abort_create(git_project):
    # S3: a failing wt_in hook is create-time friction, not data loss — the
    # worktree is still created (warn-continue; on_created never raises).
    _place_hook(git_project, "s", "seed.sh", "exit 1\n")
    mgr = _mgr(git_project, "task/s3")
    wt = mgr.create(wt_hooks={"wt_in": [{"skill": "s", "script": "seed.sh"}]})
    assert wt.exists()
    assert wt != git_project  # a real worktree was created despite the failure


def test_skip_hooks_forces_merge_through(git_project):
    # S8: merge --skip-hooks proceeds despite a failing wt_out hook (data-loss
    # consciously accepted). Mirror of the discard-skip cell (S16).
    _place_hook(git_project, "s", "drain.sh", "exit 1\n")
    mgr = _mgr(git_project, "task/s8")
    wt = mgr.create(wt_hooks=_carry_out())
    _commit_in_wt(wt)
    mgr.merge(skip_hooks=True)  # must not raise
    assert not wt.exists()  # merged + torn down


def test_already_merged_short_circuit_hook_fail_aborts(git_project):
    # S10: the HATS-596 already-merged short-circuit (worktree.py:784) still
    # harvests fail-closed. A failing wt_out hook aborts BEFORE _remove_worktree;
    # the branch is preserved so a retry re-hits 596 and re-runs the hook.
    _place_hook(git_project, "s", "drain.sh", "exit 1\n")
    mgr = _mgr(git_project, "task/s10")
    wt = mgr.create(wt_hooks=_carry_out())
    _commit_in_wt(wt)
    _git(git_project, "merge", "--no-ff", "--no-edit", "task/s10")  # already merged
    with pytest.raises(WorktreeTeardownAborted) as ei:
        mgr.merge()
    assert isinstance(ei.value.__cause__, WorktreeHookError)
    assert wt.exists()  # abort before teardown on the 596 route
    assert WorktreeManager.branch_exists(git_project, "task/s10")


def test_obm_route_hook_fail_takes_precedence(git_project):
    # S12: on the OriginalBranchMissing route (worktree.py:862) a failing wt_out
    # hook aborts BEFORE _remove_worktree — hook-fail takes precedence, the
    # OriginalBranchMissingError is never reached, wt + branch preserved.
    _git(git_project, "checkout", "-b", "doomed")
    _place_hook(git_project, "s", "drain.sh", "exit 1\n")
    mgr = _mgr(git_project, "task/s12")
    wt = mgr.create(wt_hooks=_carry_out())
    _commit_in_wt(wt)
    _git(git_project, "checkout", "-")  # back to base
    _git(git_project, "branch", "-D", "doomed")
    with pytest.raises(WorktreeTeardownAborted) as ei:
        mgr.merge()
    assert isinstance(ei.value.__cause__, WorktreeHookError)
    assert wt.exists()  # OBM not reached — hook-fail aborted first
    assert WorktreeManager.branch_exists(git_project, "task/s12")


def test_exit_with_inflight_error_surfaces_original_not_abort(git_project):
    # S20 (cleanup-contract guard): __exit__ runs cleanup; a failing wt_out hook
    # aborts teardown but the abort is SUPPRESSED — it must NOT mask the agent's
    # in-flight exception. The original error surfaces; the worktree is preserved.
    _place_hook(git_project, "s", "drain.sh", "exit 1\n")
    mgr = _mgr(git_project, "task/s20")
    boom = RuntimeError("agent boom")
    captured: dict[str, Path] = {}
    with pytest.raises(RuntimeError) as ei:
        with mgr as wd:
            captured["wt"] = wd
            mgr._wt_hooks = _carry_out()  # simulate a collected/persisted carry
            raise boom
    assert ei.value is boom  # original surfaced, not the WorktreeTeardownAborted
    assert captured["wt"].exists()  # preserved (cleanup suppressed the abort)
