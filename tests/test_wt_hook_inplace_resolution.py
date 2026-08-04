"""HATS-1269 S2: a carry row resolves in place, inside its declaring skill.

The carry is persisted state, so a tampered row is attacker-influenced input to
a path join. ``managed_wt_hook_filename`` used to reduce both halves to
``Path(...).name``, which contained the join as a side effect of flattening;
in-place resolution must keep that property on purpose (ADR-0021 M11).
"""

from __future__ import annotations

from pathlib import Path

from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG
from ai_hats.wt_lifecycle import resolve_hook_script


def _project_with_skill(root: Path, *, skill: str = "drainer") -> Path:
    """A project whose local ``libraries/`` ships one skill with a hook script."""
    hooks = root / "libraries" / "skills" / skill / "hooks"
    hooks.mkdir(parents=True)
    (hooks / "drain.sh").write_text("#!/usr/bin/env bash\nexit 0\n")
    (root / "libraries" / "skills" / skill / "SKILL.md").write_text("# drainer\n")
    ProjectConfig(provider="agy").save(root / PROJECT_CONFIG)
    return hooks / "drain.sh"


def test_row_resolves_into_the_declaring_skill_dir(tmp_path):
    expected = _project_with_skill(tmp_path)

    resolved, why = resolve_hook_script(tmp_path, {"skill": "drainer", "script": "hooks/drain.sh"})

    assert resolved == expected.resolve(), why


def test_traversing_script_is_refused(tmp_path):
    _project_with_skill(tmp_path)
    (tmp_path / "evil.sh").write_text("#!/usr/bin/env bash\ntouch /tmp/pwned\n")

    resolved, why = resolve_hook_script(
        tmp_path, {"skill": "drainer", "script": "../../../../evil.sh"}
    )

    assert resolved is None
    assert "escapes" in why


def test_traversing_skill_name_is_refused(tmp_path):
    _project_with_skill(tmp_path)
    outside = tmp_path / "libraries" / "skills" / "drainer" / "hooks"

    resolved, why = resolve_hook_script(
        tmp_path, {"skill": "../skills/drainer", "script": "hooks/drain.sh"}
    )

    assert resolved is None, f"resolved to {resolved} (expected refusal); {outside} is bait"


def test_row_missing_a_half_is_refused(tmp_path):
    _project_with_skill(tmp_path)

    assert resolve_hook_script(tmp_path, {"skill": "drainer"})[0] is None
    assert resolve_hook_script(tmp_path, {"script": "hooks/drain.sh"})[0] is None


def test_skill_living_only_in_the_worktree_resolves(tmp_path):
    """HATS-831 asymmetry: composition inside a worktree re-points the
    project-local layer to THAT worktree, so a skill the agent added there is
    what create saw. Teardown runs from the main checkout, so the worktree's own
    ``libraries/`` has to stay reachable or the hook fails closed on a merge."""
    project = tmp_path / "main"
    project.mkdir()
    ProjectConfig(provider="agy").save(project / PROJECT_CONFIG)
    worktree = tmp_path / "linked"
    expected = _project_with_skill(worktree)

    resolved, why = resolve_hook_script(
        project,
        {"skill": "drainer", "script": "hooks/drain.sh"},
        worktree_path=worktree,
    )

    assert resolved == expected.resolve(), why


def test_unknown_skill_names_itself_in_the_reason(tmp_path):
    _project_with_skill(tmp_path)

    resolved, why = resolve_hook_script(
        tmp_path, {"skill": "uncomposed", "script": "hooks/drain.sh"}
    )

    assert resolved is None
    assert "uncomposed" in why and "library" in why


def test_symlinked_skill_layer_resolves(tmp_path):
    """HATS-1494: a skill living under a symlinked library layer (e.g.
    libraries/skills -> external/skills) resolves to its target script path
    without being refused by search root containment checks."""
    project = tmp_path / "proj"
    project.mkdir()
    ProjectConfig(provider="agy").save(project / PROJECT_CONFIG)

    external_skills = tmp_path / "external_skills"
    hooks = external_skills / "symlinked_skill" / "hooks"
    hooks.mkdir(parents=True)
    script_file = hooks / "drain.sh"
    script_file.write_text("#!/usr/bin/env bash\nexit 0\n")
    (external_skills / "symlinked_skill" / "SKILL.md").write_text("# symlinked\n")

    skills_link = project / "libraries" / "skills"
    skills_link.parent.mkdir(parents=True)
    skills_link.symlink_to(external_skills, target_is_directory=True)

    resolved, why = resolve_hook_script(
        project, {"skill": "symlinked_skill", "script": "hooks/drain.sh"}
    )

    assert resolved == script_file.resolve(), why

