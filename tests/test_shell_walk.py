"""HATS-1899 — the shared command walk behind the worktree guards on Bash.

The module owns no verdict; what it owes its callers is that a segment is
reported with the directory it will really run in, and that a line nobody can
read comes back as None rather than as a guess.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE = (
    REPO_ROOT
    / "packages/ai-hats-library/src/ai_hats_library/core/skills/worktree-isolation"
    / "hooks/shell_walk.py"
)


@pytest.fixture(scope="module")
def walk_mod():
    """Imported by location, the way the flattened hooks import each other."""
    spec = importlib.util.spec_from_file_location("shell_walk", MODULE)
    module = importlib.util.module_from_spec(spec)
    sys.modules["shell_walk"] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("separator", ["&&", "||", ";", "|", "&"])
def test_every_separator_starts_a_new_command(walk_mod, separator):
    assert walk_mod.segments(["a", separator, "b"]) == [["a"], ["b"]]


def test_an_inline_path_assignment_is_reported_not_swallowed(walk_mod):
    """`PATH=<venv>/bin:$PATH pytest` is a remedy the project prints, so the
    caller has to resolve THROUGH it rather than read the bare name."""
    segment, path_override = walk_mod.strip_prefix(["env", "PATH=/w/.venv/bin", "pytest"])
    assert segment == ["pytest"]
    assert path_override == "/w/.venv/bin"


def test_an_unrelated_assignment_leaves_no_override(walk_mod):
    segment, path_override = walk_mod.strip_prefix(["FOO=1", "pytest"])
    assert segment == ["pytest"]
    assert path_override is None


def test_a_cd_moves_the_directory_the_next_segment_runs_in(tmp_path, walk_mod):
    """The reason the walk exists: reading only the payload's cwd puts this
    command in the checkout the agent just left."""
    (tmp_path / "wt").mkdir()
    steps = walk_mod.walk(f"cd {tmp_path / 'wt'} && pytest tests/", tmp_path)
    assert [seg for seg, _cwd, _path in steps] == [["pytest", "tests/"]]
    assert steps[0][1] == (tmp_path / "wt").resolve()


def test_a_cd_does_not_leak_backwards(tmp_path, walk_mod):
    """A segment BEFORE the cd still belongs to where it was issued."""
    (tmp_path / "wt").mkdir()
    steps = walk_mod.walk(f"pytest a ; cd {tmp_path / 'wt'} ; pytest b", tmp_path)
    assert [cwd for _seg, cwd, _path in steps] == [tmp_path, (tmp_path / "wt").resolve()]


def test_a_separator_with_no_space_around_it_swallows_the_line(walk_mod):
    """Today's boundary, pinned so it is a known limit and not a surprise.

    ``shlex.split`` is whitespace-driven, so ``/x;git`` is one word: the whole
    line reads as a single ``cd`` and the walk reports NO segment at all. For a
    guard that nudges, that is a missed nudge; for one that denies, it is a way
    through. Inherited verbatim from the guard this module was extracted from,
    so fixing it here would change that guard's behaviour in the same commit
    that claims to change none — it belongs to whichever caller must not miss
    ``cd /x;git reset --hard``."""
    assert walk_mod.walk("cd /x;git reset --hard", Path("/tmp")) == []
    assert walk_mod.walk("pytest a;pytest b", Path("/tmp")) == [
        (["pytest", "a;pytest", "b"], Path("/tmp"), None)
    ]


def test_an_unreadable_line_is_none_rather_than_a_guess(walk_mod):
    """Unbalanced quotes: shlex cannot say what runs, so neither can a caller."""
    assert walk_mod.walk("cd 'unbalanced && pytest", Path("/tmp")) is None


def test_a_cd_that_cannot_be_resolved_abandons_the_whole_line(walk_mod):
    """An embedded null byte reaches Path.resolve, and losing the directory
    invalidates every LATER segment — so the answer is None, not a partial walk."""
    assert walk_mod.walk("cd \x00bad && pytest", Path("/tmp")) is None
