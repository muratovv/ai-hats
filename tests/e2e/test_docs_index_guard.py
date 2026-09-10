"""e2e (HATS-444)

flow:   a maintainer commits a change under docs/, and the pre-commit
        docs-index hook decides whether docs/INDEX.md must be staged with it
cmds:
    git add docs/new-doc.md && git commit                  # no-resolve: blocked, path created in flow
    git add docs/new-doc.md docs/INDEX.md && git commit    # no-resolve: allowed, path created in flow
    git mv docs/a.md docs/b.md && git commit               # no-resolve: blocked, path created in flow
    AI_HATS_DOCS_INDEX_ACK=1 git commit                    # allowed, override
expect: adding, renaming or deleting a `docs/*.md` without staging INDEX.md is
        blocked; staging INDEX.md alongside allows it; a content-only edit to an
        existing doc, an empty stage, a non-docs change and an ADR subdir add all
        pass; the env ack overrides the block and names itself on stderr.
        Separately, the initial-wizard config still points at docs/INDEX.md
        instead of hardcoding the per-step bullet list.
why:    INDEX.md is what the initial-wizard role reads at session start, so a
        doc added without registering it is invisible to every later session —
        and the hook is pure bash, unreachable from the unit tier
"""

from __future__ import annotations
from _helpers.git import git as _git_helper

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.guards


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HOOK = (
    REPO_ROOT
    / "packages/ai-hats-library/src/ai_hats_library/core/skills/git-mastery/git_hooks/pre-commit-docs-index.sh"
)
WIZARD_CONFIG = (
    REPO_ROOT / "packages/ai-hats-library/src/ai_hats_library/core/roles/initial-wizard/config.yaml"
)


def _git(cwd: Path, *args: str) -> str:
    return _git_helper(cwd, *args).stdout.strip()


def _run_hook(cwd: Path, env: dict | None = None, timeout: int = 5):
    base_env = os.environ.copy()
    base_env.pop("AI_HATS_DOCS_INDEX_ACK", None)
    if env:
        base_env.update(env)
    return subprocess.run(
        ["bash", str(HOOK)],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=base_env,
    )


@pytest.fixture
def repo_with_docs(tmp_path: Path) -> Path:
    """Repo with an initial commit and a docs/ folder containing INDEX +
    one how-to file, so subsequent tests can simulate add/delete/rename
    against a realistic baseline."""
    subprocess.run(["git", "init", "--quiet"], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "config", "user.email", "t@e.x"], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=str(tmp_path), check=True)
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "INDEX.md").write_text("# INDEX\n\n- existing.md — original\n")
    (docs / "existing.md").write_text("# Existing doc\n\nOriginal content.\n")
    subprocess.run(["git", "add", "docs/"], cwd=str(tmp_path), check=True)
    subprocess.run(
        ["git", "commit", "-m", "init docs", "--quiet"],
        cwd=str(tmp_path),
        check=True,
    )
    return tmp_path


# --- hook scenarios --------------------------------------------------------


@pytest.mark.integration
def test_hook_blocks_add_without_index(repo_with_docs: Path):
    """Staging a brand-new docs/*.md without INDEX must fail."""
    (repo_with_docs / "docs/new.md").write_text("# New\n")
    subprocess.run(["git", "add", "docs/new.md"], cwd=str(repo_with_docs), check=True)
    res = _run_hook(repo_with_docs)
    assert res.returncode == 1, res.stderr
    assert "BLOCKED" in res.stderr
    assert "docs/new.md" in res.stderr or "new.md" in res.stderr


@pytest.mark.integration
def test_hook_allows_add_with_index(repo_with_docs: Path):
    """Staging a new docs file AND INDEX together must pass."""
    (repo_with_docs / "docs/new.md").write_text("# New\n")
    (repo_with_docs / "docs/INDEX.md").write_text(
        "# INDEX\n\n- existing.md — original\n- new.md — fresh\n"
    )
    subprocess.run(
        ["git", "add", "docs/new.md", "docs/INDEX.md"],
        cwd=str(repo_with_docs),
        check=True,
    )
    res = _run_hook(repo_with_docs)
    assert res.returncode == 0, res.stderr


@pytest.mark.integration
def test_hook_blocks_rename_without_index(repo_with_docs: Path):
    """Renaming docs/existing.md → docs/renamed.md without INDEX must fail."""
    subprocess.run(
        ["git", "mv", "docs/existing.md", "docs/renamed.md"],
        cwd=str(repo_with_docs),
        check=True,
    )
    res = _run_hook(repo_with_docs)
    assert res.returncode == 1, res.stderr
    assert "BLOCKED" in res.stderr


@pytest.mark.integration
def test_hook_blocks_deletion_without_index(repo_with_docs: Path):
    """Deleting an existing docs file without INDEX must fail."""
    subprocess.run(
        ["git", "rm", "docs/existing.md"],
        cwd=str(repo_with_docs),
        check=True,
    )
    res = _run_hook(repo_with_docs)
    assert res.returncode == 1, res.stderr
    assert "BLOCKED" in res.stderr


@pytest.mark.integration
def test_hook_allows_content_edit_without_index(repo_with_docs: Path):
    """Modifying content of an existing docs/*.md (status M) is not
    structural — hook must allow it without INDEX update."""
    (repo_with_docs / "docs/existing.md").write_text("# Existing doc\n\nUpdated content.\n")
    subprocess.run(["git", "add", "docs/existing.md"], cwd=str(repo_with_docs), check=True)
    res = _run_hook(repo_with_docs)
    assert res.returncode == 0, res.stderr


@pytest.mark.integration
def test_hook_ack_overrides_block(repo_with_docs: Path):
    """Override env must bypass the block."""
    (repo_with_docs / "docs/new.md").write_text("# New\n")
    subprocess.run(["git", "add", "docs/new.md"], cwd=str(repo_with_docs), check=True)
    res = _run_hook(repo_with_docs, env={"AI_HATS_DOCS_INDEX_ACK": "1"})
    assert res.returncode == 0, res.stderr
    assert "AI_HATS_DOCS_INDEX_ACK=1" in res.stderr


@pytest.mark.integration
def test_hook_allows_empty_stage(repo_with_docs: Path):
    """Nothing staged → no-op."""
    res = _run_hook(repo_with_docs)
    assert res.returncode == 0, res.stderr


@pytest.mark.integration
def test_hook_allows_non_docs_change(repo_with_docs: Path):
    """Staging an unrelated file (outside docs/) must not trigger the hook."""
    (repo_with_docs / "README.md").write_text("# Repo\n")
    subprocess.run(["git", "add", "README.md"], cwd=str(repo_with_docs), check=True)
    res = _run_hook(repo_with_docs)
    assert res.returncode == 0, res.stderr


@pytest.mark.integration
def test_hook_allows_adr_subdir_add(repo_with_docs: Path):
    """Adding a docs/adr/*.md (a SUBDIR doc) must NOT block — ADRs are
    referenced collectively in INDEX.md, not catalogued per-file. The
    :(glob) pathspec keeps the guard to top-level docs/*.md only."""
    (repo_with_docs / "docs/adr").mkdir()
    (repo_with_docs / "docs/adr/0001-x.md").write_text("# ADR 1\n")
    subprocess.run(["git", "add", "docs/adr/0001-x.md"], cwd=str(repo_with_docs), check=True)
    res = _run_hook(repo_with_docs)
    assert res.returncode == 0, res.stderr


# --- wizard regression -----------------------------------------------------


@pytest.mark.integration
def test_wizard_injection_references_index():
    """The initial-wizard role injection must point at docs/INDEX.md and
    no longer hardcode the per-step bullet-list of how-to files."""
    content = WIZARD_CONFIG.read_text()
    assert "docs/INDEX.md" in content, (
        "wizard config must reference docs/INDEX.md as the source of truth"
    )
    # Old per-step catalog header («Companion docs (full catalog)»)
    # must be gone — strongest structural regression signal.
    assert "Companion docs (full catalog)" not in content, (
        "Per-step catalog must be removed; wizard reads docs/INDEX.md instead"
    )
    # Old opener listed 6 docs (configure, glossary, how-to, feedback-loop,
    # extend, ARCHITECTURE). New fallback keeps 3 (configure, extend,
    # glossary). These four must NOT appear in the opener bullets anymore.
    opener_dropped = [
        "`docs/how-to.md` — overlay cookbook",
        "`docs/how-to-feedback-loop.md` — feedback-policy details",
        "`docs/ARCHITECTURE.md` — composition model",
    ]
    for snippet in opener_dropped:
        assert snippet not in content, f"old opener bullet must be removed: {snippet!r}"
