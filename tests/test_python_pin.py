"""HATS-1521 — the pin is re-spelled by hand; a partial bump must go red.

The gate cannot judge WHICH version is right (that is the human call this task
makes). What it holds is drift: one site moving without the others, and the pin
drifting out of the matrix that proves it runs.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "check_python_pin.py"

PIN = "3.13"


def _load():
    spec = importlib.util.spec_from_file_location("check_python_pin", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mod = _load()


def _pyproject(pin: str, *, classifiers: list[str] | None = None, ruff: str | None = None) -> str:
    listed = classifiers if classifiers is not None else [pin]
    lines = [
        "[project]",
        'name = "x"',
        f'requires-python = ">={pin}"',
        "classifiers = [",
        '    "Programming Language :: Python :: 3",',
        *[f'    "Programming Language :: Python :: {v}",' for v in listed],
        "]",
    ]
    if ruff is not None:
        lines += ["", "[tool.ruff]", f'target-version = "{ruff}"']
    return "\n".join(lines) + "\n"


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A minimal repo whose every pin site agrees on PIN."""
    (tmp_path / "src/ai_hats").mkdir(parents=True)
    (tmp_path / "src/ai_hats/constants.py").write_text(f'PINNED_PYTHON = "{PIN}"\n')

    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/ai-hats-launcher").write_text(f'uv venv --python {PIN} "$VENV"\n')

    (tmp_path / ".github/workflows").mkdir(parents=True)
    (tmp_path / ".github/workflows/ci.yml").write_text(
        f'jobs:\n  test:\n    strategy:\n      matrix:\n        python-version: ["{PIN}", "3.14"]\n'
    )
    (tmp_path / ".github/workflows/release-packages.yml").write_text(
        f"            uv venv --python {PIN} /tmp/verify-venv\n"
    )

    (tmp_path / "pyproject.toml").write_text(_pyproject(PIN, ruff="py" + PIN.replace(".", "")))
    (tmp_path / "packages/member").mkdir(parents=True)
    (tmp_path / "packages/member/pyproject.toml").write_text(_pyproject(PIN))

    (tmp_path / "tests/e2e").mkdir(parents=True)
    (tmp_path / "tests/e2e/test_build.py").write_text(
        f'_run(["uv", "venv", "--python", "{PIN}", str(venv)])\n'
    )
    return tmp_path


def _sites(tree: Path) -> list[str]:
    return [v.site for v in mod.violations(tree, mod.read_pin(tree))]


def test_a_consistent_tree_passes(tree: Path):
    assert _sites(tree) == []
    assert mod.main([str(tree)]) == 0


def test_launcher_left_behind_by_a_partial_bump(tree: Path):
    (tree / "scripts/ai-hats-launcher").write_text('uv venv --python 3.11 "$VENV"\n')

    assert _sites(tree) == ["scripts/ai-hats-launcher:1"]


def test_e2e_venv_build_left_behind(tree: Path):
    """The argv spelling — 10 of the sites live in e2e tests, not in shell."""
    (tree / "tests/e2e/test_build.py").write_text(
        '_run(["uv", "venv", "--python", "3.11", str(venv)])\n'
    )

    assert _sites(tree) == ["tests/e2e/test_build.py:1"]


def test_a_members_floor_left_behind(tree: Path):
    (tree / "packages/member/pyproject.toml").write_text(_pyproject("3.11"))

    assert "packages/member/pyproject.toml [requires-python]" in _sites(tree)


def test_a_classifier_claiming_a_version_below_the_pin(tree: Path):
    (tree / "packages/member/pyproject.toml").write_text(
        _pyproject(PIN, classifiers=["3.11", "3.12", PIN])
    )

    assert _sites(tree) == ["packages/member/pyproject.toml [classifiers]"]


def test_a_pyproject_not_claiming_the_version_it_pins(tree: Path):
    (tree / "packages/member/pyproject.toml").write_text(_pyproject(PIN, classifiers=["3.14"]))

    assert _sites(tree) == ["packages/member/pyproject.toml [classifiers]"]


def test_ruff_lints_against_another_language_level(tree: Path):
    (tree / "pyproject.toml").write_text(_pyproject(PIN, ruff="py311"))

    assert _sites(tree) == ["pyproject.toml [tool.ruff.target-version]"]


def test_the_pin_is_not_in_the_ci_matrix(tree: Path):
    """Invariant 2 — a pin CI never runs is how HATS-1519 reached every install."""
    (tree / ".github/workflows/ci.yml").write_text(
        'jobs:\n  test:\n    strategy:\n      matrix:\n        python-version: ["3.11", "3.12"]\n'
    )

    assert _sites(tree) == [".github/workflows/ci.yml [test.strategy.matrix]"]


def test_an_unreadable_matrix_is_a_violation_not_a_pass(tree: Path):
    (tree / ".github/workflows/ci.yml").write_text("jobs:\n  test: {}\n")

    assert _sites(tree) == [".github/workflows/ci.yml [test.strategy.matrix]"]


def test_a_gate_that_cannot_find_the_pin_must_not_pass(tree: Path):
    (tree / "src/ai_hats/constants.py").write_text("PINNED_PYTHON = 3.13\n")

    with pytest.raises(LookupError):
        mod.read_pin(tree)
    assert mod.main([str(tree)]) == 1


def test_the_real_repo_is_self_consistent():
    """The gate runs against this checkout in ci-local; keep it honest here too."""
    assert mod.violations(REPO_ROOT, mod.read_pin(REPO_ROOT)) == []
