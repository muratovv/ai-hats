"""HATS-887 — unit tests for the repo-integrity snapshot/diff helpers.

Detection logic proven against a throwaway repo (never the real one). The
staged-deletion case is the RED-under-revert proof that the snapshot covers the
INDEX, not just the HEAD tree.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests._pytester_env import pythonpath_with_repo_root
from tests._repo_integrity import diff_repo, snapshot_repo

_CONFTEST = Path(__file__).resolve().parent / "conftest.py"


def _git(root: Path, *args: str) -> str:
    # No env= — relies on the autouse `_isolate_git_env` conftest strip (the
    # sanctioned 285-site pattern), so this helper is not a re-leak vector.
    proc = subprocess.run(
        ["git", *args], cwd=str(root), capture_output=True, text=True, check=True
    )
    return proc.stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "throwaway"
    r.mkdir()
    _git(r, "init")
    _git(r, "config", "user.email", "t@example.com")  # ai-hats: allow-secret
    _git(r, "config", "user.name", "Throwaway")
    (r / "a.txt").write_text("a\n")
    (r / "b.txt").write_text("b\n")
    _git(r, "add", ".")
    _git(r, "commit", "-m", "init")
    return r


def test_diff_none_when_unchanged(repo: Path) -> None:
    before = snapshot_repo(repo)
    after = snapshot_repo(repo)
    assert diff_repo(before, after) is None


def test_diff_detects_landed_commit(repo: Path) -> None:
    before = snapshot_repo(repo)
    (repo / "c.txt").write_text("c\n")
    _git(repo, "add", "c.txt")
    _git(repo, "commit", "-m", "second")
    delta = diff_repo(before, snapshot_repo(repo))
    assert delta is not None and "HEAD" in delta


def test_diff_detects_staged_deletion(repo: Path) -> None:
    """The −178393 incident shape: a staged delete with no commit.

    RED-under-revert of snapshot index-awareness — HEAD + ref tips are unchanged
    by ``git rm --cached``, so only the ``git ls-files`` count exposes it.
    """
    before = snapshot_repo(repo)
    _git(repo, "rm", "--cached", "a.txt")  # stage a deletion, do NOT commit
    after = snapshot_repo(repo)
    assert after.head == before.head, "guard precondition: HEAD must be unchanged"
    delta = diff_repo(before, after)
    assert delta is not None and "index" in delta


def test_diff_ignores_sibling_ref_change(repo: Path) -> None:
    """Concurrency tolerance: a NON-HEAD ref change (another agent merging its own
    branch in the shared clone) must NOT trip the tripwire — only HEAD + index do.
    """
    before = snapshot_repo(repo)
    _git(repo, "branch", "sibling-from-another-agent")
    assert diff_repo(before, snapshot_repo(repo)) is None


def test_snapshot_non_repo_is_noop(tmp_path: Path) -> None:
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    state = snapshot_repo(plain)
    assert not state.is_repo
    assert diff_repo(state, snapshot_repo(plain)) is None


def test_snapshot_no_git_binary_is_noop(repo: Path, tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    """HATS-892: git binary unreachable (empty PATH) → sentinel, never raises.

    RED-under-revert of the ``_git`` FileNotFoundError guard: without it,
    ``snapshot_repo`` raises here — the exact crash the empty-PATH offline
    subprocess in ``test_venv_strict_mode`` hit via the session-autouse tripwire.
    """
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))  # `repo` has .git, but no git binary
    state = snapshot_repo(repo)
    assert not state.is_repo
    assert diff_repo(state, snapshot_repo(repo)) is None


@pytest.mark.integration
def test_tripwire_fires_on_real_repo_mutation(pytester, tmp_path, monkeypatch) -> None:
    """The session-scoped tripwire (real conftest) fails a session whose test
    mutated the watched repo.

    Drives the ACTUAL conftest fixture in an isolated inner session (verbatim
    copy, precedent ``test_tmp_hygiene.py``) pointed at a throwaway victim via
    ``AI_HATS_REPO_INTEGRITY_ROOT``. RED-under-revert: de-autouse the fixture or
    drop its diff-check and the inner session passes clean.
    """
    victim = tmp_path / "victim"
    victim.mkdir()
    _git(victim, "init")
    _git(victim, "config", "user.email", "v@example.com")  # ai-hats: allow-secret
    _git(victim, "config", "user.name", "Victim")
    (victim / "keep.txt").write_text("keep\n")
    _git(victim, "add", ".")
    _git(victim, "commit", "-m", "baseline")

    pytester.makeconftest(_CONFTEST.read_text())
    pytester.makepyfile(
        f"""
        import subprocess

        def test_rogue_commit_into_real_repo():
            subprocess.run(
                ["git", "commit", "--allow-empty", "-m", "rogue"],
                cwd=r{str(victim)!r}, check=True,
            )
        """
    )
    monkeypatch.setenv("AI_HATS_REPO_INTEGRITY_ROOT", str(victim))
    monkeypatch.setenv("PYTHONPATH", pythonpath_with_repo_root())
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider")

    assert result.ret != 0, "the tripwire must fail the inner session"
    assert any("repo-integrity" in line for line in result.outlines), (
        "the tripwire must name itself in the failure output"
    )
