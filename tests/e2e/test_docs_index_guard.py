"""HATS-444 — end-to-end behaviour of the docs-INDEX freshness hook.

The pre-commit hook (`pre-commit-docs-index.sh`) is a pure-bash surface;
the unit suite cannot meaningfully exercise it. This file drives the
script against a real ephemeral git repo to cover:

  * blocks when docs/*.md is added/deleted/renamed without staging INDEX
  * allows when INDEX is staged alongside
  * allows content-only edits to existing docs (status M)
  * AI_HATS_DOCS_INDEX_ACK=1 overrides the block
  * empty stage / no docs changes → no-op

A separate regression check asserts the wizard injection points at
docs/INDEX.md and no longer hardcodes the per-step bullet-list.

Slow only because of git init + subprocess spin-up (~ms each).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HOOK = (
    REPO_ROOT
    / "packages/ai-hats-library/src/ai_hats_library/core/skills/git-mastery/git_hooks/pre-commit-docs-index.sh"
)
WIZARD_CONFIG = REPO_ROOT / "packages/ai-hats-library/src/ai_hats_library/core/roles/initial-wizard/config.yaml"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


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
    subprocess.run(
        ["git", "config", "user.email", "t@e.x"], cwd=str(tmp_path), check=True
    )
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
    subprocess.run(
        ["git", "add", "docs/new.md"], cwd=str(repo_with_docs), check=True
    )
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
    (repo_with_docs / "docs/existing.md").write_text(
        "# Existing doc\n\nUpdated content.\n"
    )
    subprocess.run(
        ["git", "add", "docs/existing.md"], cwd=str(repo_with_docs), check=True
    )
    res = _run_hook(repo_with_docs)
    assert res.returncode == 0, res.stderr


@pytest.mark.integration
def test_hook_ack_overrides_block(repo_with_docs: Path):
    """Override env must bypass the block."""
    (repo_with_docs / "docs/new.md").write_text("# New\n")
    subprocess.run(
        ["git", "add", "docs/new.md"], cwd=str(repo_with_docs), check=True
    )
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
    subprocess.run(
        ["git", "add", "README.md"], cwd=str(repo_with_docs), check=True
    )
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
        assert snippet not in content, (
            f"old opener bullet must be removed: {snippet!r}"
        )
