"""Tests for skill-contributed provider runtime hooks (HATS-597).

Mirrors test_assembler_git_hooks.py. Covers the assembler-side collection of
``runtime_hooks:`` declarations from composed skills. Materialization + the
provider settings.json wiring are tested separately.
"""

import subprocess
from pathlib import Path

import pytest

from ai_hats.assembler import Assembler
from ai_hats.constants import HOOK_PRE_TOOL_USE, HOOK_POST_TOOL_USE
from ai_hats.hook_collection import collect_runtime_hooks
from ai_hats_core import ComponentKind, CompositionResult, ResolvedComponent
from ai_hats.models import RuntimeHook


def _skill(name: str, source_path: Path) -> ResolvedComponent:
    return ResolvedComponent(
        name=name,
        component_type=ComponentKind.SKILL,
        source_path=source_path,
    )


def _make_skill_with_runtime_hooks(
    base: Path, name: str, hooks: dict[str, list[tuple[str, str]]]
) -> ResolvedComponent:
    """Create a skill dir whose SKILL.md frontmatter declares runtime_hooks
    under top-level ``ai_hats:`` (HATS-814) + materializes the hook scripts.

    ``hooks`` maps event -> list of (matcher, script_relpath).
    """
    skill_dir = base / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    lines = ["---", f"name: {name}", "ai_hats:", "  runtime_hooks:"]
    for event, rows in hooks.items():
        lines.append(f"    {event}:")
        for matcher, script in rows:
            lines.append(f"      - matcher: {matcher}")
            lines.append(f"        script: {script}")
            script_path = skill_dir / script
            script_path.parent.mkdir(parents=True, exist_ok=True)
            script_path.write_text("#!/usr/bin/env bash\nexit 0\n")
            script_path.chmod(0o755)
    lines += ["---", f"# {name}"]
    (skill_dir / "SKILL.md").write_text("\n".join(lines) + "\n")
    return _skill(name, skill_dir)


@pytest.fixture
def assembler(tmp_path: Path) -> Assembler:
    project = tmp_path / "proj"
    project.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    return Assembler(project_dir=project)


def _result(skills: list[ResolvedComponent]) -> CompositionResult:
    return CompositionResult(
        name="test-role",
        priorities=[],
        rules=[],
        skills=skills,
        injections=[],
    )


class TestCollectSkillRuntimeHooks:
    def test_collects_pre_and_post_from_multiple_skills(self, tmp_path):
        s1 = _make_skill_with_runtime_hooks(
            tmp_path / "skills",
            "skill-a",
            {HOOK_PRE_TOOL_USE: [("Bash", "hooks/a.sh")]},
        )
        s2 = _make_skill_with_runtime_hooks(
            tmp_path / "skills",
            "skill-b",
            {
                HOOK_PRE_TOOL_USE: [("Edit", "hooks/b.sh")],
                HOOK_POST_TOOL_USE: [("Write", "hooks/c.sh")],
            },
        )
        collected = collect_runtime_hooks(_result([s1, s2]))

        assert set(collected) == {HOOK_PRE_TOOL_USE, HOOK_POST_TOOL_USE}
        pre = collected[HOOK_PRE_TOOL_USE]
        assert ("skill-a", RuntimeHook(matcher="Bash", script="hooks/a.sh")) in pre
        assert ("skill-b", RuntimeHook(matcher="Edit", script="hooks/b.sh")) in pre
        post = collected[HOOK_POST_TOOL_USE]
        assert post == [("skill-b", RuntimeHook(matcher="Write", script="hooks/c.sh"))]

    def test_empty_when_no_skill_declares(self, tmp_path):
        plain = tmp_path / "skills" / "plain"
        plain.mkdir(parents=True)
        (plain / "metadata.yaml").write_text("name: plain\n")
        collected = collect_runtime_hooks(_result([_skill("plain", plain)]))
        assert collected == {}
