"""e2e (HATS-1651)

flow:   a maintainer running the local lint gate over a tree whose one
        unformatted file sits outside src/ and tests/
cmds:
    bash scripts/ci-local.sh lint
expect: the gate exits non-zero and names that file, instead of reporting green
why:    `ruff check` walked the whole tree while `ruff format --check` walked
        only src/ and tests/, so a file under packages/ earned a green gate
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from _helpers.git import init_repo

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Format-dirty, lint-clean: the formatter wants `[1, 2]`, and E2xx whitespace
#: rules are preview-only in ruff, so `ruff check` passes over it. That
#: combination is what isolates the SCOPE of the two stages from their verdicts.
DIRTY = "z = [1,2]\n"
FORMATTED = "z = [1, 2]\n"


def _tree(root: Path, dirty_at: str) -> None:
    """A minimal repo the real `ci-local.sh lint` can run against.

    Carries the project's own `pyproject.toml`, so the ruff configuration under
    test is the shipped one, and the real dispatcher — nothing about the stage
    is restated here. `src/` and `tests/` hold formatted files on purpose: the
    narrow scope must be GREEN, or a failure would prove nothing about scope.
    """
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "scripts" / "ci-local.sh", root / "scripts" / "ci-local.sh")
    shutil.copy2(REPO_ROOT / "pyproject.toml", root / "pyproject.toml")
    for clean in ("src/ok.py", "tests/ok.py"):
        (root / clean).parent.mkdir(parents=True, exist_ok=True)
        (root / clean).write_text(FORMATTED)
    (root / dirty_at).parent.mkdir(parents=True, exist_ok=True)
    (root / dirty_at).write_text(DIRTY)
    init_repo(root)


def _lint(root: Path) -> subprocess.CompletedProcess[str]:
    """Run the lint stage exactly as the gate does, on this interpreter."""
    return subprocess.run(
        ["bash", "scripts/ci-local.sh", "lint"],
        cwd=root,
        env={"PATH": "/usr/bin:/bin", "PYTHON": sys.executable},
        capture_output=True,
        text=True,
        timeout=300,
    )


def test_lint_sees_an_unformatted_file_under_packages(tmp_path):
    """The stage's two halves must walk the same tree.

    Before HATS-1651 this exits 0: `ruff check .` reads the file and has no
    opinion on its formatting, while `ruff format --check src/ tests/` never
    looks at it.
    """
    root = tmp_path / "repo"
    root.mkdir()
    probe = "packages/ai-hats-probe/src/probe.py"
    _tree(root, probe)

    result = _lint(root)
    combined = result.stdout + result.stderr

    assert result.returncode != 0, (
        f"the lint gate passed a tree holding an unformatted {probe} — "
        f"`ruff format --check` does not cover what `ruff check` covers\n{combined}"
    )
    assert "probe.py" in combined, (
        f"the gate failed without naming the unformatted file:\n{combined}"
    )


def test_lint_stays_green_when_that_same_file_is_formatted(tmp_path):
    """The control: the widened scope must refuse only unformatted content.

    Without this, a stage broken in any other way (a bad path, a missing tool)
    would satisfy the assertion above while proving nothing about scope.
    """
    root = tmp_path / "repo"
    root.mkdir()
    _tree(root, "packages/ai-hats-probe/src/probe.py")
    (root / "packages/ai-hats-probe/src/probe.py").write_text(FORMATTED)

    result = _lint(root)

    assert result.returncode == 0, (
        f"the lint gate refused a fully formatted tree:\n{result.stdout}{result.stderr}"
    )
