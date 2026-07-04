"""HATS-823 — threading helpers: serialize collected hooks for persistence and
degrade gracefully when no role / composition is available.

HATS-865: composition moved to the integrator caller seam
(``wt_effects.collect_carry_for_project``, relocated from ``state`` by
HATS-866); the ``wt_carry`` chokepoint receives the ready result + hooks
manager. These tests drive the caller seam so the whole
compose→serialize→materialize→filter chain stays pinned."""

from __future__ import annotations

from pathlib import Path

from ai_hats_wt import WorktreeHook

from ai_hats.models import ProjectConfig
from ai_hats.paths import managed_wt_hook_filename, wt_hooks_dir
from ai_hats.wt_effects import collect_carry_for_project
from ai_hats.wt_carry import collect_carry_for_role, serialize_collected_hooks


def _project_with_wt_role(tmp_path: Path, *, with_script: bool = True):
    """Project + synthetic library whose role's skill declares a wt_out hook."""
    project = tmp_path / "proj"
    project.mkdir()
    lib = tmp_path / "lib"
    skill = lib / "skills" / "drainer"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: drainer\nai_hats:\n  worktree:\n    wt_out:\n"
        "      - script: drain.sh\n        on: [merge]\n---\n# drainer\n"
    )
    if with_script:
        sp = skill / "drain.sh"
        sp.write_text("#!/usr/bin/env bash\nexit 0\n")
        sp.chmod(0o755)
    trait = lib / "traits" / "trait-base"
    trait.mkdir(parents=True)
    (trait / "config.yaml").write_text(
        "name: trait-base\ncomposition:\n  skills:\n    - drainer\ninjection: B.\n"
    )
    role = lib / "roles" / "wt-role"
    role.mkdir(parents=True)
    (role / "config.yaml").write_text(
        "name: wt-role\npriorities: [Quality]\n"
        "composition:\n  traits:\n    - trait-base\ninjection: R.\n"
    )
    ProjectConfig(
        provider="gemini", library_paths=[str(lib)], active_role="wt-role"
    ).save(project / "ai-hats.yaml")
    return project, lib


def test_serialize_drops_empty_on_for_wt_in():
    collected = {
        "wt_in": [("seeder", WorktreeHook(script="seed.sh"))],
        "wt_out": [("drainer", WorktreeHook(script="drain.sh", on=("merge", "discard")))],
    }
    out = serialize_collected_hooks(collected)
    assert out["wt_in"] == [{"skill": "seeder", "script": "seed.sh"}]
    assert out["wt_out"] == [
        {"skill": "drainer", "script": "drain.sh", "on": ["merge", "discard"]}
    ]


def test_serialize_skips_empty_kinds():
    assert serialize_collected_hooks({}) == {}
    assert serialize_collected_hooks({"wt_in": []}) == {}


def test_collect_carry_for_non_project_is_empty(tmp_path: Path):
    # No ai-hats.yaml / no role → empty carry, no exception (graceful).
    assert collect_carry_for_project(tmp_path) == {}


def test_chokepoint_without_composition_is_empty(tmp_path: Path):
    """HATS-865 signature pin: the brick chokepoint takes (project_dir,
    result, hooks) and degrades to {} when the caller has no composition."""
    assert collect_carry_for_role(tmp_path, None, None) == {}


def test_carry_materializes_backing_script(tmp_path: Path):
    """HATS-833 create-time backstop: a recorded carry row has a backing script
    on disk by construction (materialized at carry-record time)."""
    project, _lib = _project_with_wt_role(tmp_path)
    carry = collect_carry_for_project(project)
    assert carry.get("wt_out"), f"expected a wt_out carry, got {carry}"
    dest = wt_hooks_dir(project) / managed_wt_hook_filename("drainer", "drain.sh")
    assert dest.is_file(), "recorded carry must have a backing script on disk"


def test_carry_drops_row_without_resolvable_script(tmp_path: Path):
    """A declared hook whose source can't be resolved is dropped from the carry
    (with a warn) rather than recorded → fail-closed at teardown."""
    project, _lib = _project_with_wt_role(tmp_path, with_script=False)
    assert collect_carry_for_project(project) == {}
