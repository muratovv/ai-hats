"""e2e (HATS-2062)

flow:   an agent about to blind the supervisor's review diff with a raw merge
cmds:   git merge task/prox-619                        # on master -> denied
        git checkout master && git merge task/prox-619 # chained   -> denied
        git merge task/prox-619                        # on a task branch -> allowed
        git merge master                               # base into branch -> allowed
expect: the composed PreToolUse chain (all Bash hooks, in order) denies a raw
        merge of a task branch into a base branch with a routing reason naming
        `ai-hats wt merge`, and lets both harmless commands and task-internal
        merges through
why:    PROX-619 merged straight into master before review; the review diff is
        computed against the merge-base, so after the merge it is empty by
        construction. Single-hook tests cannot see a sibling hook overriding
        the verdict — the composed chain is the only honest subject.
"""  # comment-length: allow — the e2e catalog header format

from __future__ import annotations

from pathlib import Path

import pytest

from _helpers.git import git as _git
from _helpers.hook_chain import build_session_settings, run_chain

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
GUARD = "pre_bash_task_merge_guard.sh"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A checkout on `master` with a composed session's settings.json."""
    from ai_hats.assembler import Assembler
    from ai_hats.models import ProjectConfig

    from _helpers.git import init_repo

    root = tmp_path / "proj"
    root.mkdir()
    init_repo(root, branch="master")
    ProjectConfig(provider="claude", library_paths=[]).save(root / "ai-hats.yaml")
    Assembler(root).init()
    return root


@pytest.fixture
def settings(project: Path) -> Path:
    return build_session_settings(project)


def test_chain_denies_raw_merge_on_master(project: Path, settings: Path) -> None:
    """The PROX-619 spelling, denied with the managed road named (HATS-2062)."""
    verdict = run_chain(project, "git merge task/prox-619", settings=settings, cwd=project)
    assert verdict.denied, verdict
    assert verdict.hook == GUARD
    assert "ai-hats wt merge" in verdict.reason


def test_chain_denies_chained_checkout_merge(project: Path, settings: Path) -> None:
    verdict = run_chain(
        project,
        "git checkout master && git merge task/prox-619",
        settings=settings,
        cwd=project,
    )
    assert verdict.denied, verdict
    assert verdict.hook == GUARD


def test_chain_allows_merge_on_a_task_branch(project: Path, settings: Path) -> None:
    _git(project, "checkout", "-q", "-b", "task/hats-2062")
    verdict = run_chain(project, "git merge task/prox-619", settings=settings, cwd=project)
    assert not verdict.gated, verdict


def test_chain_allows_merge_of_base(project: Path, settings: Path) -> None:
    verdict = run_chain(project, "git merge master", settings=settings, cwd=project)
    assert not verdict.gated, verdict


def test_chain_still_allows_harmless_commands(project: Path, settings: Path) -> None:
    """Positive control: the chain fires at all, and this guard does not widen."""
    verdict = run_chain(project, "ls", settings=settings, cwd=project)
    assert not verdict.gated, verdict
