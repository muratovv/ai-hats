"""workspace_pythonpath — the one PYTHONPATH contract (HATS-913).

`src` alone Franken-mixes a workspace checkout: the subprocess gets the
checkout's integrator but resolves `packages/*` via the venv's editable
installs — the MAIN checkout's code. The helper prepends every
`packages/*/src` (sorted) so a worktree run tests the worktree's packages.
"""

from __future__ import annotations

import os
from pathlib import Path

from ai_hats_wt import workspace_pythonpath


def _mk(root: Path, *rel: str) -> None:
    for r in rel:
        (root / r).mkdir(parents=True)


def test_workspace_root_yields_src_then_sorted_package_srcs(tmp_path: Path) -> None:
    _mk(tmp_path, "src", "packages/b-pkg/src", "packages/a-pkg/src")

    result = workspace_pythonpath(tmp_path)

    assert result == os.pathsep.join(
        [
            str(tmp_path / "src"),
            str(tmp_path / "packages" / "a-pkg" / "src"),
            str(tmp_path / "packages" / "b-pkg" / "src"),
        ]
    )


def test_no_packages_dir_yields_src_only(tmp_path: Path) -> None:
    _mk(tmp_path, "src")

    assert workspace_pythonpath(tmp_path) == str(tmp_path / "src")


def test_existing_is_appended_last(tmp_path: Path) -> None:
    _mk(tmp_path, "src", "packages/a-pkg/src")

    result = workspace_pythonpath(tmp_path, existing="/pre/existing")

    assert result.endswith(f"{os.pathsep}/pre/existing")
    assert result.startswith(str(tmp_path / "src"))


def test_empty_existing_leaves_no_trailing_separator(tmp_path: Path) -> None:
    _mk(tmp_path, "src")

    assert not workspace_pythonpath(tmp_path, existing="").endswith(os.pathsep)
