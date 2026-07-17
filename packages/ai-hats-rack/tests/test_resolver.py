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
