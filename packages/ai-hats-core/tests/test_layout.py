"""The one resolver's contract: markers, hop, pin, layout (HATS-1606)."""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

from ai_hats_core.layout import (
    cache_home,
    project_key,
    ForeignPinPolicy,
    ForeignProjectPinError,
    ProjectLayout,
    ProjectNotFoundError,
    resolve_root,
)

WARN = ForeignPinPolicy.WARN_AND_IGNORE


# -- markers: the ONE table -------------------------------------------------


def test_agent_dir_marks_a_root(tmp_path: Path) -> None:
    (tmp_path / ".agent").mkdir()
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    assert resolve_root(sub, {}, on_foreign_pin=WARN) == tmp_path


def test_yaml_alone_marks_a_root(tmp_path: Path) -> None:
    """The defect behind counter 1: the historical CLI walk-up never accepted
    ai-hats.yaml as a marker, so this layout resolved differently under rack."""
    (tmp_path / "ai-hats.yaml").write_text("")
    sub = tmp_path / "sub"
    sub.mkdir()
    assert resolve_root(sub, {}, on_foreign_pin=WARN) == tmp_path


def test_nearest_marker_wins_over_stray_ancestor(tmp_path: Path) -> None:
    """A forgotten .agent above the project must not capture it."""
    (tmp_path / ".agent").mkdir()  # the stray
    proj = tmp_path / "proj"
    (proj / "sub").mkdir(parents=True)
    (proj / "ai-hats.yaml").write_text("")
    assert resolve_root(proj / "sub", {}, on_foreign_pin=WARN) == proj


def test_no_markers_raises_instead_of_cwd_fallback(tmp_path: Path) -> None:
    """The silent cwd fallback IS the stray-ancestor bug; 'anchor here' is
    spelled ProjectLayout.at, not a resolver default."""
    sub = tmp_path / "bare"
    sub.mkdir()
    with pytest.raises(ProjectNotFoundError):
        resolve_root(sub, {}, on_foreign_pin=WARN)


# -- the worktree hop -------------------------------------------------------


def _linked_worktree(tmp_path: Path, *, onboarded_main: bool) -> tuple[Path, Path]:
    """A gitlink pair the pure-fs hop can read: main/.git dir + wt/.git file."""
    main = tmp_path / "main"
    wt_git = main / ".git" / "worktrees" / "x"
    wt_git.mkdir(parents=True)
    (wt_git / "commondir").write_text("../..\n")
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text(f"gitdir: {wt_git}\n")
    if onboarded_main:
        (main / ".agent").mkdir()
    return main.resolve(), wt


def test_linked_worktree_hops_to_onboarded_main(tmp_path: Path) -> None:
    """The hop beats the marker walk: a worktree checkout carries a TRACKED
    copy of the markers, and answering with the worktree is the defect."""
    main, wt = _linked_worktree(tmp_path, onboarded_main=True)
    (wt / "ai-hats.yaml").write_text("")  # the stray tracked copy
    assert resolve_root(wt, {}, on_foreign_pin=WARN) == main


def test_hop_is_conditional_on_onboarded_main(tmp_path: Path) -> None:
    """R8: jump only into an onboarded main; otherwise the marker walk answers."""
    main, wt = _linked_worktree(tmp_path, onboarded_main=False)
    (wt / "ai-hats.yaml").write_text("")
    assert resolve_root(wt, {}, on_foreign_pin=WARN) == wt.resolve()


def test_pin_is_checked_after_the_hop(tmp_path: Path) -> None:
    """A sub-agent stands in the worktree while its pin names the main checkout
    BY DESIGN — judging the pin before hopping calls that legitimate pin foreign."""
    main, wt = _linked_worktree(tmp_path, onboarded_main=True)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        root = resolve_root(wt, {"AI_HATS_PROJECT_DIR": str(main)}, on_foreign_pin=WARN)
    assert root == main


# -- the pin: one procedure, the reaction a parameter -----------------------


def _project(tmp_path: Path) -> Path:
    (tmp_path / ".agent").mkdir()
    return tmp_path


def test_foreign_pin_refuse_raises(tmp_path: Path) -> None:
    proj = _project(tmp_path)
    env = {"AI_HATS_PROJECT_DIR": "/somewhere/else"}
    with pytest.raises(ForeignProjectPinError):
        resolve_root(proj, env, on_foreign_pin=ForeignPinPolicy.REFUSE)


def test_foreign_pin_warn_and_ignore_answers_structural_root(tmp_path: Path) -> None:
    proj = _project(tmp_path)
    env = {"AI_HATS_PROJECT_DIR": "/somewhere/else"}
    with pytest.warns(UserWarning, match="another project"):
        assert resolve_root(proj, env, on_foreign_pin=WARN) == proj


def test_agreeing_pin_is_silent(tmp_path: Path) -> None:
    proj = _project(tmp_path)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert resolve_root(proj, {"AI_HATS_PROJECT_DIR": str(proj)}, on_foreign_pin=WARN) == proj


# -- ProjectLayout.compute: env under scoped trust > config-as-data > default


def test_env_base_override_honoured_without_pin(tmp_path: Path) -> None:
    layout = ProjectLayout.compute(tmp_path, {"AI_HATS_DIR": str(tmp_path / "elsewhere")})
    assert layout.base == tmp_path / "elsewhere"


def test_env_base_override_dropped_under_foreign_pin(tmp_path: Path) -> None:
    env = {"AI_HATS_DIR": str(tmp_path / "elsewhere"), "AI_HATS_PROJECT_DIR": "/not/here"}
    with pytest.warns(UserWarning, match="leaked session pin"):
        layout = ProjectLayout.compute(tmp_path, env)
    assert layout.base == tmp_path / ".agent" / "ai-hats"


def test_config_say_is_data_not_a_read(tmp_path: Path) -> None:
    layout = ProjectLayout.compute(tmp_path, {}, ai_hats_dir="custom/base")
    assert layout.base == tmp_path / "custom" / "base"


def test_at_anchors_without_resolution(tmp_path: Path) -> None:
    layout = ProjectLayout.at(tmp_path / "bare")
    assert layout.base == tmp_path / "bare" / ".agent" / "ai-hats"


# -- per-consumer views -----------------------------------------------------


def test_consumer_views_split_the_tree(tmp_path: Path) -> None:
    layout = ProjectLayout.at(tmp_path)
    base = tmp_path / ".agent" / "ai-hats"
    assert layout.tracker.tasks_dir == base / "tracker" / "backlog" / "tasks"
    assert layout.sessions.runs == base / "sessions" / "runs"
    assert layout.state_md == base / "STATE.md"
    assert layout.default_venv == base / ".venv"


# -- the views that retire the paths leaf's derivations --------------------


def test_library_and_versions_views_are_geometry(tmp_path: Path) -> None:
    layout = ProjectLayout.at(tmp_path)
    base = tmp_path / ".agent" / "ai-hats"
    assert layout.library.root == base / "library"
    assert layout.library.hooks == base / "library" / "hooks"
    assert layout.versions.root == base / "versions"
    assert layout.versions.current_pointer == base / "versions" / "current"
    assert layout.versions.dir("abc") == base / "versions" / "abc"
    assert layout.versions.sentinel("abc") == base / "versions" / "abc" / ".complete"
    assert layout.pipeline_steps == base / "pipeline_steps"
    assert layout.user_hooks == base / "user-hooks"
    assert layout.user_rules == base / "user-rules"
    assert layout.last_backup == base / ".last_backup"


def test_cache_root_is_settled_when_the_layout_is_built(tmp_path: Path) -> None:
    env = {"AI_HATS_CACHE_HOME": str(tmp_path / "cache")}
    layout = ProjectLayout.compute(tmp_path / "proj", env)
    key = project_key(tmp_path / "proj")
    assert layout.cache.root == tmp_path / "cache" / key
    assert layout.cache.session("s1") == tmp_path / "cache" / key / "sessions" / "s1"
    assert layout.cache.worktree_checkouts == tmp_path / "cache" / key / "worktrees"
    # built, not read at access: a later environment change does not move it
    env["AI_HATS_CACHE_HOME"] = str(tmp_path / "elsewhere")
    assert layout.cache.root == tmp_path / "cache" / key


def test_at_takes_the_cache_home_from_the_environment_it_is_given(tmp_path: Path) -> None:
    layout = ProjectLayout.at(tmp_path / "bare", {"AI_HATS_CACHE_HOME": str(tmp_path / "c")})
    assert layout.cache.root.parent == tmp_path / "c"


def test_bare_constructor_has_no_cache_and_says_so(tmp_path: Path) -> None:
    layout = ProjectLayout(root=tmp_path, base=tmp_path / ".agent")
    with pytest.raises(LookupError):
        _ = layout.cache


def test_cache_home_precedence(tmp_path: Path) -> None:
    assert cache_home({"AI_HATS_CACHE_HOME": "/a", "XDG_CACHE_HOME": "/x"}) == Path("/a")
    assert cache_home({"XDG_CACHE_HOME": "/x"}) == Path("/x") / "ai-hats"
    assert cache_home({"AI_HATS_USER_HOME": str(tmp_path)}) == tmp_path / ".cache" / "ai-hats"
    assert cache_home({"AI_HATS_CACHE_HOME": "~/c"}) == Path("~/c").expanduser()


def test_project_key_is_stable_and_separates_same_basename_roots(tmp_path: Path) -> None:
    a = tmp_path / "one" / "proj"
    b = tmp_path / "two" / "proj"
    assert project_key(a) == project_key(a)
    assert project_key(a) != project_key(b)
    assert project_key(a).startswith("proj-")
    assert project_key(tmp_path / "we ird!name").startswith("we-ird-name-")
