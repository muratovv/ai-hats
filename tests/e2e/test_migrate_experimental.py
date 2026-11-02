"""e2e (HATS-2009)

flow:   a maintainer regenerates the candidate copy of the shipped library for the
        closure this slice commits, twice, and feeds the script a root it must refuse
cmds:
    python scripts/migrate_experimental.py --root <copy> --only hypothesis-intake worker maintainer
    python scripts/migrate_experimental.py --root <copy> --only hypothesis-intake worker maintainer
    python scripts/migrate_experimental.py --root <synthetic> --only oddball
expect: the copy's experimental/ is byte-equal to the committed one, including
        hook files and the shared hooks payload; a symlink that stays inside the
        library root is kept; the second run changes nothing; a config.yaml key
        outside the ADR-0034 D6 table refuses naming the file; an apps row becomes
        a hook file the composite names
why:    experimental/ is a derived artifact: what is committed must be what the
        script produces, or the candidate reads a hand-edited library (ADR-0034 D6)
"""

from __future__ import annotations

import filecmp
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.library]

REPO = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO / "scripts" / "migrate_experimental.py"
LIBRARY = REPO / "packages" / "ai-hats-library" / "src" / "ai_hats_library"
ONLY = ("hypothesis-intake", "worker", "maintainer")
IGNORE = shutil.ignore_patterns("__pycache__", ".ruff_cache", "*.pyc")


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True, timeout=120
    )


def _tree(root: Path) -> dict[str, bytes | str]:
    """Every path under ``root`` with its bytes, a symlink by its target."""
    out: dict[str, bytes | str] = {}
    for path in sorted(root.rglob("*")):
        if "__pycache__" in path.parts:
            continue
        rel = str(path.relative_to(root))
        if path.is_symlink():
            out[rel] = f"-> {path.readlink()}"
        elif path.is_file():
            out[rel] = path.read_bytes()
    return out


@pytest.fixture
def library_copy(tmp_path: Path) -> Path:
    """The shipped library without its experimental/ copy."""
    copy = tmp_path / "ai_hats_library"
    shutil.copytree(
        LIBRARY,
        copy,
        symlinks=True,
        ignore=shutil.ignore_patterns("__pycache__", ".ruff_cache", "*.pyc", "experimental"),
    )
    assert not (copy / "experimental").exists()
    return copy


def test_the_committed_copy_is_what_the_script_produces(library_copy: Path) -> None:
    first = _run("--root", str(library_copy), "--only", *ONLY)
    assert first.returncode == 0, first.stderr
    produced = _tree(library_copy / "experimental")
    assert (library_copy / "experimental" / "hooks").is_dir()
    assert (
        library_copy
        / "experimental"
        / "core"
        / "hooks"
        / "consent"
        / "fs.remove"
        / "critical-path.yaml"
    ).is_file()
    kept = (
        library_copy
        / "experimental"
        / "core"
        / "skills"
        / "safety-guard"
        / "hooks"
        / "consent_gate"
    )
    assert kept.is_symlink(), "a symlink into the shared hooks payload stays"
    committed = _tree(LIBRARY / "experimental")
    assert produced == committed, sorted(set(produced) ^ set(committed))
    assert filecmp.cmp(
        library_copy / "experimental" / "core" / "hypothesis-intake.yaml",
        LIBRARY / "experimental" / "core" / "hypothesis-intake.yaml",
        shallow=False,
    )

    second = _run("--root", str(library_copy), "--only", *ONLY)
    assert second.returncode == 0, second.stderr
    assert _tree(library_copy / "experimental") == produced, "the second run changes nothing"


def test_a_construct_outside_the_table_refuses_naming_the_file(tmp_path: Path) -> None:
    root = tmp_path / "synthetic"
    for layer in ("core", "usage"):
        (root / layer).mkdir(parents=True)
    role = root / "core" / "roles" / "oddball"
    role.mkdir(parents=True)
    role_yaml = role / "config.yaml"
    role_yaml.write_text(
        "name: oddball\npriorities: [Speed]\ncomposition:\n  traits: []\n"
        "  rules: []\n  skills: []\n  apps:\n    wt:\n      - run: x.sh\n        at: [pre-merge]\n"
        "        on_error: refuse\ninjection: |\n  # ODDBALL\nmascot: capybara\n"
    )
    refused = _run("--root", str(root), "--only", "oddball")
    assert refused.returncode == 1
    assert "refused:" in refused.stderr and str(role_yaml) in refused.stderr
    assert "'mascot'" in refused.stderr
    assert not (root / "experimental").exists(), "a refusal writes nothing"

    role_yaml.write_text(role_yaml.read_text().replace("mascot: capybara\n", ""))
    without_mode = _run("--root", str(root), "--only", "oddball")
    assert without_mode.returncode == 1
    assert "no run mode in RUN_MODE" in without_mode.stderr, "hitl/automate is a person's call"


def test_an_apps_row_becomes_a_hook_the_composite_names(tmp_path: Path) -> None:
    root = tmp_path / "synthetic"
    for layer in ("core", "usage"):
        (root / layer).mkdir(parents=True)
    trait = root / "usage" / "traits" / "gated"
    trait.mkdir(parents=True)
    (trait / "config.yaml").write_text(
        "name: gated\ncomposition:\n  skills: [tool]\n  apps:\n    wt:\n"
        "      - run: tool/check.sh\n        at: [pre-merge]\n        on_error: refuse\n"
        "injection: |\n  # GATED\n"
    )
    skill = root / "usage" / "skills" / "tool"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: tool\ndescription: d\nlicense: MIT\nai_hats:\n  git_hooks:\n"
        "    pre-push: [check.sh]\n  requires: {cli: [rack]}\n---\n# Tool\n"
    )
    (skill / "check.sh").write_text("#!/usr/bin/env bash\nexit 0\n")
    done = _run("--root", str(root), "--only", "gated")
    assert done.returncode == 0, done.stderr
    written = (root / "experimental" / "usage" / "skills" / "tool" / "SKILL.md").read_text()
    assert written == (
        "---\nname: tool\ndescription: d\nlicense: MIT\nai_hats:\n  schema_version: 1\n"
        "  type: skill\n  requires: {cli: [rack]}\n---\n# Tool\n"
    )
    hook = root / "experimental" / "usage" / "hooks" / "tool" / "check.yaml"
    gate = root / "experimental" / "usage" / "hooks" / "tool" / "check-pre-merge.yaml"
    assert hook.is_file() and "kind: git" in hook.read_text() and "at: pre-push" in hook.read_text()
    assert gate.is_file() and "kind: workflow" in gate.read_text()
    expr = (root / "experimental" / "usage" / "gated.yaml").read_text()
    assert "check\n" in expr or " + check" in expr
    assert "check-pre-merge" in expr
