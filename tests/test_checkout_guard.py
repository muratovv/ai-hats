"""Tests for tests._checkout_guard (HATS-1242)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._checkout_guard import (
    ENV_IGNORE_FOREIGN_CHECKOUT,
    check_checkout_integrity,
    discover_subpackages,
    foreign_source_checkout,
    remedy_message,
)


def test_foreign_source_checkout_matching_root(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    init_py = repo_root / "src" / "ai_hats" / "__init__.py"
    init_py.parent.mkdir(parents=True)
    init_py.touch()

    assert foreign_source_checkout(init_py, repo_root) is None


def test_foreign_source_checkout_different_root(tmp_path: Path) -> None:
    repo_root = tmp_path / "worktree"
    foreign_root = tmp_path / "main_repo"

    init_py = foreign_root / "src" / "ai_hats" / "__init__.py"
    init_py.parent.mkdir(parents=True)
    init_py.touch()

    assert foreign_source_checkout(init_py, repo_root) == foreign_root.resolve()


def test_foreign_source_checkout_site_packages(tmp_path: Path) -> None:
    repo_root = tmp_path / "worktree"
    site_packages = tmp_path / ".venv" / "lib" / "python3.11" / "site-packages"
    init_py = site_packages / "ai_hats" / "__init__.py"
    init_py.parent.mkdir(parents=True)
    init_py.touch()

    assert foreign_source_checkout(init_py, repo_root) is None


def test_discover_subpackages(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    pkg1 = repo_root / "packages" / "ai-hats-core"
    pkg2 = repo_root / "packages" / "surfaces" / "agy"
    pkg1.mkdir(parents=True)
    pkg2.mkdir(parents=True)
    (pkg1 / "pyproject.toml").touch()
    (pkg2 / "pyproject.toml").touch()

    found = discover_subpackages(repo_root)
    assert found == [pkg1, pkg2]


def test_remedy_message(tmp_path: Path) -> None:
    repo_root = tmp_path / "worktree"
    foreign = tmp_path / "main_repo"
    pkg = repo_root / "packages" / "surfaces" / "agy"
    pkg.mkdir(parents=True)
    (pkg / "pyproject.toml").touch()

    msg = remedy_message(repo_root, foreign)
    assert "WRONG checkout" in msg
    assert "uv venv .venv" in msg
    assert "-e 'packages/surfaces/agy'" in msg
    assert ENV_IGNORE_FOREIGN_CHECKOUT in msg


def test_remedy_message_names_the_path_step(tmp_path: Path) -> None:
    """Provisioning the venv is only half the fix (HATS-1245).

    A bare 'pytest' resolves through PATH, so a message that stops after the
    install describes a fix that does not work when followed. (The git hooks no
    longer need this step — HATS-1291/1314 take the checkout's own .venv — which
    is why the one-shot form now prefixes 'pytest', not 'git commit'.) Pinned by
    the exact string an operator copy-pastes — an absolute venv bin dir, so the
    line is runnable from any cwd, not just the worktree root.
    """
    repo_root = tmp_path / "worktree"
    foreign = tmp_path / "main_repo"

    msg = remedy_message(repo_root, foreign)
    bin_dir = f"{repo_root}/.venv/bin"
    assert f'export PATH="{bin_dir}:$PATH"' in msg
    assert f'PATH="{bin_dir}:$PATH" pytest' in msg


def test_check_checkout_integrity_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_IGNORE_FOREIGN_CHECKOUT, raising=False)
    repo_root = tmp_path / "worktree"
    foreign_root = tmp_path / "main_repo"

    init_py = foreign_root / "src" / "ai_hats" / "__init__.py"
    init_py.parent.mkdir(parents=True)
    init_py.touch()

    with pytest.raises(RuntimeError, match="WRONG checkout"):
        check_checkout_integrity(init_py, repo_root)


def test_check_checkout_integrity_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_IGNORE_FOREIGN_CHECKOUT, "1")
    repo_root = tmp_path / "worktree"
    foreign_root = tmp_path / "main_repo"

    init_py = foreign_root / "src" / "ai_hats" / "__init__.py"
    init_py.parent.mkdir(parents=True)
    init_py.touch()

    # Should not raise when env flag set
    check_checkout_integrity(init_py, repo_root)
