"""e2e (HATS-1862)

flow:   a developer launching a session whose role composes a skill that
        declares a runtime hook, but the skill no longer ships the script
cmds:
    ai-hats -r hook-role
expect: the session starts, and the pre-launch banner says which gate will not
        run and which file the skill is missing
why:    the manifest writers used to drop such a row in silence, so the session
        ran with the gate off and nothing but a harness stderr line said so
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _helpers.fake_surface import install

from ai_hats.paths import PROJECT_CONFIG

pytestmark = pytest.mark.integration

ROLE = "hook-role"
SKILL = "gate-skill"
SCRIPT = "hooks/gate.sh"


def _library_with_a_gate_nobody_ships(root: Path) -> Path:
    lib = root / "lib"
    trait = lib / "traits" / "trait-base"
    trait.mkdir(parents=True)
    (trait / "config.yaml").write_text("name: trait-base\ninjection: B.\n")

    skill = lib / "skills" / SKILL
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\n"
        f"name: {SKILL}\n"
        "description: A skill declaring one Bash gate.\n"
        "ai_hats:\n"
        "  runtime_hooks:\n"
        "    PreToolUse:\n"
        "      - matcher: Bash\n"
        f"        script: {SCRIPT}\n"
        "---\n"
        f"# {SKILL}\n"
    )
    # The script is deliberately never written: the declaration outlived the file.

    role = lib / "roles" / ROLE
    role.mkdir(parents=True)
    (role / "config.yaml").write_text(
        f"name: {ROLE}\npriorities: [Quality]\n"
        "composition:\n"
        "  traits:\n    - trait-base\n"
        f"  skills:\n    - {SKILL}\n"
        "injection: R.\n"
    )
    return lib


def test_a_gate_the_skill_does_not_ship_is_named_at_launch_and_the_session_starts(
    tmp_project, tmp_path: Path, repo_root: Path
) -> None:
    """The whole chain, in a real process: composer -> composed_rows ->
    artifacts.notices -> the pre-launch banner -> the surface is spawned."""
    lib = _library_with_a_gate_nobody_ships(tmp_path)
    (tmp_project.path / PROJECT_CONFIG).write_text(
        "provider: claude\n"
        "ai_hats_dir: .agent/ai-hats\n"
        f"active_role: {ROLE}\n"
        f"default_role: {ROLE}\n"
        "library_paths:\n  - " + str(lib) + "\n"
    )
    surface = install(tmp_project, tmp_path, repo_root)

    done = surface.run_once(role=ROLE)

    banner = done.stdout
    assert SKILL in banner, f"the banner must name the skill:\n{banner[-1500:]}"
    assert SCRIPT in banner, "and the file it is missing"
    assert "will not run" in banner, "and say plainly that the gate is off"
    assert str(lib / "skills" / SKILL / SCRIPT) in banner, "with the path the author has to fix"
    assert "fake-surface up" in banner, (
        "the author's fault degrades the session; it does not refuse it"
    )
