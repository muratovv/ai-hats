"""Walk-up root resolver: pure resolution, typed refusal, zero mkdir (HATS-1021)."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_hats_rack.resolver import (
    DEFAULT_PREFIX,
    NoProjectRootError,
    find_project_root,
    load_root,
    resolve_root,
)

SRC = Path(__file__).resolve().parent.parent / "src" / "ai_hats_rack"


def _snapshot(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def test_walk_up_finds_agent_dir_from_nested_subdir(tmp_path):
    (tmp_path / ".agent").mkdir()
    nested = tmp_path / "a" / "b" / "c"
    nested.mkdir(parents=True)
    assert find_project_root(nested) == tmp_path


def test_walk_up_finds_ai_hats_yaml_marker(tmp_path):
    (tmp_path / "ai-hats.yaml").write_text("task_prefix: XX\n")
    nested = tmp_path / "sub"
    nested.mkdir()
    assert find_project_root(nested) == tmp_path


def test_nearest_marker_wins(tmp_path):
    (tmp_path / ".agent").mkdir()
    inner = tmp_path / "vendored"
    (inner / ".agent").mkdir(parents=True)
    assert find_project_root(inner / ".agent") == inner
    assert find_project_root(inner) == inner


def test_no_marker_returns_none(tmp_path):
    assert find_project_root(tmp_path / "nowhere") is None


def test_load_root_reads_config(tmp_path):
    (tmp_path / "ai-hats.yaml").write_text("ai_hats_dir: .hats\ntask_prefix: SBX\n")
    root = load_root(tmp_path)
    assert root.tasks_dir == tmp_path / ".hats" / "tracker" / "backlog" / "tasks"
    assert root.prefix == "SBX"


def test_load_root_defaults_without_config(tmp_path):
    root = load_root(tmp_path)
    assert root.tasks_dir == tmp_path / ".agent" / "ai-hats" / "tracker" / "backlog" / "tasks"
    assert root.prefix == DEFAULT_PREFIX


def test_load_root_tolerates_malformed_config(tmp_path):
    (tmp_path / "ai-hats.yaml").write_text("- not\n- a\n- mapping\n")
    root = load_root(tmp_path)
    assert root.prefix == DEFAULT_PREFIX


def test_explicit_override_passes_through(tmp_path):
    override = tmp_path / "elsewhere" / "tasks"
    root = resolve_root(tmp_path, override)
    assert root.tasks_dir == override
    assert root.prefix == DEFAULT_PREFIX


# ----- HATS-1038 C2: gitlink hop + override project_dir anchor ----------------


def _make_linked_worktree(tmp_path):
    """Build the git-worktree metadata layout by hand (no real git): a main
    checkout with ``.agent/`` and a linked worktree whose ``.git`` is a gitlink
    file pointing at ``main/.git/worktrees/<name>`` (with a ``commondir``)."""
    main = tmp_path / "main"
    (main / ".agent").mkdir(parents=True)
    wt_meta = main / ".git" / "worktrees" / "wt1"
    wt_meta.mkdir(parents=True)
    (wt_meta / "commondir").write_text("../..\n")  # → main/.git
    wt = tmp_path / "wt1"
    wt.mkdir()
    (wt / ".git").write_text(f"gitdir: {wt_meta}\n")
    return main, wt


def test_gitlink_hop_resolves_main_root_from_worktree(tmp_path):
    main, wt = _make_linked_worktree(tmp_path)
    assert find_project_root(wt) == main
    nested = wt / "src" / "pkg"
    nested.mkdir(parents=True)
    assert find_project_root(nested) == main


def test_gitlink_hop_beats_stray_marker_in_worktree(tmp_path):
    # The worktree carries its own ai-hats.yaml (the K6 sandbox shape) — the hop
    # must still win, or transitions from the worktree mis-anchor to it.
    main, wt = _make_linked_worktree(tmp_path)
    (wt / "ai-hats.yaml").write_text("task_prefix: STRAY\n")
    assert find_project_root(wt) == main


def test_main_checkout_is_not_hopped(tmp_path):
    # A ``.git`` directory (not a gitlink file) means the main repo — no hop.
    main = tmp_path / "main"
    (main / ".agent").mkdir(parents=True)
    (main / ".git").mkdir()
    assert find_project_root(main) == main


def test_override_anchors_project_dir_at_real_root(tmp_path):
    (tmp_path / "ai-hats.yaml").write_text("task_prefix: SBX\n")
    sub = tmp_path / "packages" / "x"
    sub.mkdir(parents=True)
    override = tmp_path / "custom" / "tasks"
    root = resolve_root(sub, override)
    assert root.tasks_dir == override
    assert root.project_dir == tmp_path  # gap #3: not `sub`
    assert root.prefix == "SBX"  # read from the found root's config


def test_foreign_root_is_typed_error_and_never_mkdirs(tmp_path):
    # HATS-839: a start with no project marker must NOT bootstrap a phantom
    # tracker — typed refusal, filesystem byte-identical before and after.
    stray = tmp_path / "just" / "some" / "dir"
    stray.mkdir(parents=True)
    before = _snapshot(tmp_path)
    with pytest.raises(NoProjectRootError) as err:
        resolve_root(stray)
    assert _snapshot(tmp_path) == before
    assert "--tasks-dir" in str(err.value)  # the refusal names the escape hatch


def test_resolution_of_valid_root_is_pure_too(tmp_path):
    (tmp_path / ".agent").mkdir()
    before = _snapshot(tmp_path)
    resolve_root(tmp_path / ".agent")
    assert _snapshot(tmp_path) == before


def test_resolver_and_docstore_never_read_cwd():
    # caller_cwd is threaded through from the CLI entry (HATS-840 discipline).
    import ast

    for module in ("resolver.py", "docstore.py"):
        tree = ast.parse((SRC / module).read_text(encoding="utf-8"))
        offenders = [
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr in ("cwd", "getcwd")
        ]
        assert not offenders, f"{module} reads the process cwd: {offenders}"
