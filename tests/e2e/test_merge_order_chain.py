"""e2e (HATS-2062)

flow:   an agent driving a card through the review road and merging its branch
cmds:   rack transition HATS-001 execute → ai-hats wt merge task/hats-001  # refused
        rack transition HATS-001 review  (branch pre-merged)               # refused
        ai-hats wt merge task/hats-001   (card in review)                  # lands
expect: wt:pre-merge refuses the merge while the card is before the review
        road and names the state; ->review refuses a card whose branch is
        already merged into master; on the road (review) the merge lands
why:    the PreToolUse guard (test_task_merge_guard_chain.py) only keeps the
        agent on the managed path; the card-state invariant itself lives at
        wt:pre-merge and ->review, and only a real merge/transition exercises
        the resolution, the check and the refusal text together
"""  # comment-length: allow — the e2e catalog header format

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from _helpers.env import consented
from _helpers.git import git as _git
from _helpers.sessions import stand_in_wrapped_session

pytestmark = [pytest.mark.integration, pytest.mark.gates, pytest.mark.rack]

REPO_ROOT = Path(__file__).resolve().parents[2]
SESSION = "e2e-merge-order"
PLAN_FILL = (
    "\n## Requirements\n\nprobe\n\n## Scope & Out-of-scope\n\nprobe\n\n"
    "## Steps\n\nprobe\n\n## Verification Protocol\n\nprobe\n"
)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A checkout on `master` whose library is seeded INTO the sandbox: a suite
    running from a linked worktree would otherwise hand check resolution a
    worktree-rooted library — refused by ADR-0019 D9 clause 4."""
    from ai_hats.assembler import Assembler
    from ai_hats.models import ProjectConfig

    from _helpers.git import init_repo

    root = tmp_path / "proj"
    root.mkdir()
    init_repo(root, branch="master")
    lib = tmp_path / "library"
    shutil.copytree(
        REPO_ROOT / "packages/ai-hats-library/src/ai_hats_library",
        lib,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    # A library ROOT holds roles/ skills/ … directly — the shipped layers are
    # the roots here, not the package dir that contains them.
    layers = [str(lib / layer) for layer in ("core", "usage", "ai-hats-dev")]
    ProjectConfig(provider="claude", library_paths=layers).save(root / "ai-hats.yaml")
    Assembler(root).init()
    return root


@pytest.fixture
def env(project: Path, tmp_path: Path, checkout_bin: Path) -> dict:
    e = os.environ.copy()
    for name in [k for k in e if k.startswith("AI_HATS_")]:
        del e[name]
    e["AI_HATS_USER_HOME"] = str(tmp_path / "user-home")
    e["AI_HATS_ROOT_PID"] = str(os.getpid())
    e["PATH"] = os.pathsep.join(
        [str(checkout_bin), str(Path(sys.executable).parent), e.get("PATH", "")]
    )
    return stand_in_wrapped_session(e, project, SESSION)


def _run(env: dict, cwd: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv), cwd=str(cwd), env=env, capture_output=True, text=True, timeout=180
    )


def _rack(env: dict, project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return _run(env, project, "rack", *args)


def _card_id(project: Path, env: dict) -> str:
    created = _rack(env, project, "create", "merge-order probe")
    assert created.returncode == 0, created.stderr
    match = re.search(r"Created: (\S+)", created.stdout)
    assert match, created.stdout
    return match.group(1)


def _to_execute(project: Path, env: dict, card: str) -> None:
    assert _rack(env, project, "transition", card, "plan").returncode == 0
    plan_md = project / ".agent/ai-hats/tracker/backlog/tasks" / card / "plan.md"
    plan_md.write_text(plan_md.read_text(encoding="utf-8") + PLAN_FILL, encoding="utf-8")
    acked = {**consented(env), "AI_HATS_PLAN_ACK": "1"}
    moved = _rack(acked, project, "transition", card, "execute")
    assert moved.returncode == 0, moved.stdout + moved.stderr


def _worktree_of(project: Path, card: str) -> Path:
    listed = _git(project, "worktree", "list", "--porcelain").stdout
    want = f"refs/heads/task/{card.lower()}"
    path: str | None = None
    for line in listed.splitlines():
        if line.startswith("worktree "):
            path = line.partition(" ")[2]
        if line == f"branch {want}" and path is not None:
            return Path(path)
    raise AssertionError(f"no worktree for {card}:\n{listed}")


def _commit_in_worktree(project: Path, card: str) -> None:
    wt = _worktree_of(project, card)
    (wt / "work.txt").write_text("work\n", encoding="utf-8")
    _git(wt, "add", "work.txt")
    _git(wt, "commit", "-q", "-m", "work")


def test_pre_merge_refuses_before_the_review_road(project: Path, env: dict) -> None:
    card = _card_id(project, env)
    _to_execute(project, env, card)
    _commit_in_worktree(project, card)

    refused = _run(consented(env), project, "ai-hats", "wt", "merge", f"task/{card.lower()}")

    assert refused.returncode != 0, f"the merge went through at execute:\n{refused}"
    assert "execute" in refused.stdout + refused.stderr
    assert "work" not in _git(project, "log", "--format=%s", "master").stdout


def test_review_edge_refuses_a_pre_merged_branch(project: Path, env: dict) -> None:
    card = _card_id(project, env)
    _to_execute(project, env, card)
    _commit_in_worktree(project, card)
    # The PROX-619 shape: the branch lands in master before the card enters review.
    _git(project, "merge", "-q", "--no-ff", f"task/{card.lower()}", "-m", "early merge")

    assert _rack(env, project, "transition", card, "document").returncode == 0
    refused = _rack(consented(env), project, "transition", card, "review")

    assert refused.returncode != 0, f"a blind card entered review:\n{refused}"
    assert "was already merged" in refused.stdout + refused.stderr


def test_the_review_road_merges(project: Path, env: dict) -> None:
    """Positive control: the sanctioned chain — execute → document → review →
    consented merge — lands the branch in master (HATS-2062)."""
    card = _card_id(project, env)
    _to_execute(project, env, card)
    _commit_in_worktree(project, card)
    assert _rack(env, project, "transition", card, "document").returncode == 0
    entered = _rack(consented(env), project, "transition", card, "review")
    assert entered.returncode == 0, entered.stdout + entered.stderr

    merged = _run(consented(env), project, "ai-hats", "wt", "merge", f"task/{card.lower()}")

    assert merged.returncode == 0, f"the on-road merge was refused:\n{merged}"
    assert "work" in _git(project, "log", "--format=%s", "master").stdout
