"""HATS-823 — threading helpers: serialize collected hooks for persistence and
degrade gracefully when no role / composition is available.

HATS-865: composition moved to the integrator caller seam
(``wt_effects.collect_carry_for_project``, relocated from ``state`` by
HATS-866); the ``wt_carry`` chokepoint receives the ready result. These tests
drive the caller seam so the whole compose→filter→serialize chain stays
pinned."""

from __future__ import annotations

import logging
from pathlib import Path

from ai_hats_wt import WorktreeHook

from ai_hats.models import ProjectConfig
from ai_hats.wt_effects import collect_carry_for_project
from ai_hats.wt_carry import collect_carry_for_role, serialize_collected_hooks
from ai_hats.paths import PROJECT_CONFIG


def _project_with_wt_role(tmp_path: Path, *, with_script: bool = True, ghost_skill: bool = False):
    """Project + synthetic library whose role's skill declares a wt_out hook.

    ``ghost_skill`` names a second, absent skill on the trait — the composition
    then carries an error while still resolving ``drainer`` (HATS-1592).
    """
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
    trait_skills = "    - drainer\n" + ("    - ghost-skill\n" if ghost_skill else "")
    (trait / "config.yaml").write_text(
        f"name: trait-base\ncomposition:\n  skills:\n{trait_skills}injection: B.\n"
    )
    role = lib / "roles" / "wt-role"
    role.mkdir(parents=True)
    (role / "config.yaml").write_text(
        "name: wt-role\npriorities: [Quality]\n"
        "composition:\n  traits:\n    - trait-base\ninjection: R.\n"
    )
    ProjectConfig(provider="agy", library_paths=[str(lib)], active_role="wt-role").save(
        project / PROJECT_CONFIG
    )
    return project, lib


def test_serialize_drops_empty_on_for_wt_in():
    collected = {
        "wt_in": [("seeder", WorktreeHook(script="seed.sh"))],
        "wt_out": [("drainer", WorktreeHook(script="drain.sh", on=("merge", "discard")))],
    }
    out = serialize_collected_hooks(collected)
    assert out["wt_in"] == [{"skill": "seeder", "script": "seed.sh"}]
    assert out["wt_out"] == [{"skill": "drainer", "script": "drain.sh", "on": ["merge", "discard"]}]


def test_serialize_skips_empty_kinds():
    assert serialize_collected_hooks({}) == {}
    assert serialize_collected_hooks({"wt_in": []}) == {}


def test_collect_carry_for_non_project_is_empty(tmp_path: Path):
    # No ai-hats.yaml / no role → empty carry, no exception (graceful).
    assert collect_carry_for_project(tmp_path) == {}


def test_chokepoint_without_composition_is_empty():
    """Signature pin: the brick chokepoint takes the ready result and degrades
    to {} when the caller has no composition."""
    assert collect_carry_for_role(None) == {}


def test_carry_records_a_resolvable_row(tmp_path: Path):
    """HATS-1269: a recorded row promises its script resolved at create time —
    no second copy on disk backs it."""
    project, _lib = _project_with_wt_role(tmp_path)
    carry = collect_carry_for_project(project)
    assert carry.get("wt_out") == [{"skill": "drainer", "script": "drain.sh", "on": ["merge"]}]
    assert not (project / ".agent" / "ai-hats" / "library" / "wt-hooks").exists()


def test_carry_drops_row_without_resolvable_script(tmp_path: Path):
    """A declared hook whose source can't be resolved is dropped from the carry
    (with a warn) rather than recorded → fail-closed at teardown."""
    project, _lib = _project_with_wt_role(tmp_path, with_script=False)
    assert collect_carry_for_project(project) == {}


def test_carry_warns_when_the_role_does_not_resolve(tmp_path: Path, caplog):
    """HATS-1592: a role that does not resolve composes to zero skills, so the
    carry is empty — and the emptiness used to be indistinguishable from
    'this role declares no hooks'. The teardown data it protects is gone by
    the time anyone notices, so the create-time WARN is the only witness."""
    project = tmp_path / "proj"
    project.mkdir()
    lib = tmp_path / "lib"
    (lib / "roles").mkdir(parents=True)
    ProjectConfig(provider="agy", library_paths=[str(lib)], active_role="ghost-role").save(
        project / PROJECT_CONFIG
    )
    with caplog.at_level(logging.WARNING, logger="ai_hats.wt_carry"):
        assert collect_carry_for_project(project) == {}
    assert "ghost-role" in caplog.text


def test_carry_keeps_surviving_rows_and_warns_when_a_skill_is_missing(tmp_path: Path, caplog):
    """HATS-1592: the nastier half — one skill of the trait does not resolve, so
    the composition errors while ``drainer`` still composes. The surviving row
    MUST stay in the carry (degrading to empty here would destroy the hook that
    did resolve), and the error MUST be announced."""
    project, _lib = _project_with_wt_role(tmp_path, ghost_skill=True)
    with caplog.at_level(logging.WARNING, logger="ai_hats.wt_carry"):
        carry = collect_carry_for_project(project)
    assert carry.get("wt_out") == [{"skill": "drainer", "script": "drain.sh", "on": ["merge"]}]
    assert "ghost-skill" in caplog.text


def test_carry_is_quiet_when_the_composition_is_clean(tmp_path: Path, caplog):
    """The WARN must not become background noise on a healthy composition."""
    project, _lib = _project_with_wt_role(tmp_path)
    with caplog.at_level(logging.WARNING, logger="ai_hats.wt_carry"):
        assert collect_carry_for_project(project)
    assert caplog.text == ""
