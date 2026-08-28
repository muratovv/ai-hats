"""Tests for tests._checkout_guard (HATS-1242, HATS-1429)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._checkout_guard import (
    ENV_IGNORE_FOREIGN_CHECKOUT,
    check_checkout_integrity,
    check_library_integrity,
    discover_subpackages,
    foreign_library_layers,
    foreign_source_checkout,
    library_remedy_message,
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
    site_packages = tmp_path / ".venv" / "lib" / "python3.13" / "site-packages"
    init_py = site_packages / "ai_hats" / "__init__.py"
    init_py.parent.mkdir(parents=True)
    init_py.touch()

    assert foreign_source_checkout(init_py, repo_root) is None


def test_discover_subpackages(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    pkg1 = repo_root / "packages" / "ai-hats-core"
    pkg2 = repo_root / "packages" / "ai-hats-wt"
    pkg1.mkdir(parents=True)
    pkg2.mkdir(parents=True)
    (pkg1 / "pyproject.toml").touch()
    (pkg2 / "pyproject.toml").touch()

    found = discover_subpackages(repo_root)
    assert found == [pkg1, pkg2]


def test_remedy_message(tmp_path: Path) -> None:
    repo_root = tmp_path / "worktree"
    foreign = tmp_path / "main_repo"
    pkg = repo_root / "packages" / "ai-hats-wt"
    pkg.mkdir(parents=True)
    (pkg / "pyproject.toml").touch()

    msg = remedy_message(repo_root, foreign)
    assert "WRONG checkout" in msg
    assert "uv venv .venv" in msg
    assert "-e 'packages/ai-hats-wt'" in msg
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


# --- library-layer provenance (HATS-1429) ----------------------------------
# The guard above checks where the *package* resolves; these check where the
# *library layers* resolve — the axis it is blind to.

LAYER_SUBPATH = ("packages", "ai-hats-library", "src", "ai_hats_library")


def _make_layers(checkout: Path) -> list[Path]:
    """Build ``[core, usage]`` under a checkout, as builtin_library_layers returns."""
    root = checkout.joinpath(*LAYER_SUBPATH)
    layers = [root / "core", root / "usage"]
    for layer in layers:
        layer.mkdir(parents=True)
    return layers


def test_foreign_library_layers_inside_root(tmp_path: Path) -> None:
    repo_root = tmp_path / "worktree"
    layers = _make_layers(repo_root)

    assert foreign_library_layers(layers, repo_root) is None


def test_foreign_library_layers_in_another_checkout(tmp_path: Path) -> None:
    """The live defect: tests in the worktree, layers resolved in main."""
    repo_root = tmp_path / "worktree"
    main_checkout = tmp_path / "main_repo"
    repo_root.mkdir()
    layers = _make_layers(main_checkout)

    assert foreign_library_layers(layers, repo_root) == main_checkout.resolve()


def test_foreign_library_layers_site_packages_is_not_foreign(tmp_path: Path) -> None:
    """A wheel install is a legitimate downstream resolution, not a wrong checkout."""
    repo_root = tmp_path / "worktree"
    repo_root.mkdir()
    site_packages = tmp_path / ".venv" / "lib" / "python3.13" / "site-packages"
    layers = [site_packages / "ai_hats_library" / name for name in ("core", "usage")]
    for layer in layers:
        layer.mkdir(parents=True)

    assert foreign_library_layers(layers, repo_root) is None


def test_foreign_library_layers_empty_is_not_foreign(tmp_path: Path) -> None:
    """A broken install resolves to no layers; that is the install's problem, not ours."""
    assert foreign_library_layers([], tmp_path) is None


def test_library_remedy_message_names_both_paths(tmp_path: Path) -> None:
    """The message must state what was measured vs what was expected.

    An operator hitting this has just been handed a plausible-looking result from
    the wrong checkout; the message's whole job is to name both ends of the split.
    """
    repo_root = tmp_path / "worktree"
    foreign = tmp_path / "main_repo"

    msg = library_remedy_message(repo_root, foreign)

    assert "WRONG library" in msg
    assert str(repo_root) in msg
    assert str(foreign) in msg


def test_library_remedy_names_a_cause_conftest_has_not_already_fixed(tmp_path: Path) -> None:
    """The remedy must be actionable, not describe an already-neutralized cause.

    conftest drops the AI_HATS_PROJECT_DIR/AI_HATS_DIR pin at import, so "unset the
    pin" cannot be the advice — by the time this fires, the pin is long gone and the
    operator would be chasing a variable that is not set. The live causes are the
    library-root override and cwd.
    """
    msg = library_remedy_message(tmp_path / "worktree", tmp_path / "main_repo")

    assert "AI_HATS_LIBRARY_ROOT" in msg
    assert "cwd" in msg


def test_check_library_integrity_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_IGNORE_FOREIGN_CHECKOUT, raising=False)
    repo_root = tmp_path / "worktree"
    repo_root.mkdir()
    layers = _make_layers(tmp_path / "main_repo")

    with pytest.raises(RuntimeError, match="WRONG library"):
        check_library_integrity(layers, repo_root)


def test_check_library_integrity_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Shares the 1242 escape hatch — one flag for 'I know, run it anyway'."""
    monkeypatch.setenv(ENV_IGNORE_FOREIGN_CHECKOUT, "1")
    repo_root = tmp_path / "worktree"
    repo_root.mkdir()
    layers = _make_layers(tmp_path / "main_repo")

    check_library_integrity(layers, repo_root)
