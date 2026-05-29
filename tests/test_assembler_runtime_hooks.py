"""Tests for skill-contributed provider runtime hooks (HATS-597).

Mirrors test_assembler_git_hooks.py. Covers the assembler-side collection of
``runtime_hooks:`` declarations from composed skills. Materialization + the
provider settings.json wiring are tested separately.
"""

import subprocess
from pathlib import Path

import pytest

from ai_hats.assembler import Assembler
from ai_hats.composer import CompositionResult, ResolvedComponent
from ai_hats.models import ComponentType, HooksConfig, RuntimeHook


def _skill(name: str, source_path: Path) -> ResolvedComponent:
    return ResolvedComponent(
        name=name,
        component_type=ComponentType.SKILL,
        source_path=source_path,
    )


def _make_skill_with_runtime_hooks(
    base: Path, name: str, hooks: dict[str, list[tuple[str, str]]]
) -> ResolvedComponent:
    """Create a skill dir with metadata.yaml declaring runtime_hooks + scripts.

    ``hooks`` maps event -> list of (matcher, script_relpath).
    """
    skill_dir = base / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    lines = ["name: " + name, "runtime_hooks:"]
    for event, rows in hooks.items():
        lines.append(f"  {event}:")
        for matcher, script in rows:
            lines.append(f"    - matcher: {matcher}")
            lines.append(f"      script: {script}")
            script_path = skill_dir / script
            script_path.parent.mkdir(parents=True, exist_ok=True)
            script_path.write_text("#!/usr/bin/env bash\nexit 0\n")
            script_path.chmod(0o755)
    (skill_dir / "metadata.yaml").write_text("\n".join(lines) + "\n")
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
        hooks=HooksConfig(),
        injections=[],
    )


class TestCollectSkillRuntimeHooks:
    def test_collects_pre_and_post_from_multiple_skills(self, assembler, tmp_path):
        s1 = _make_skill_with_runtime_hooks(
            tmp_path / "skills",
            "skill-a",
            {"PreToolUse": [("Bash", "hooks/a.sh")]},
        )
        s2 = _make_skill_with_runtime_hooks(
            tmp_path / "skills",
            "skill-b",
            {
                "PreToolUse": [("Edit", "hooks/b.sh")],
                "PostToolUse": [("Write", "hooks/c.sh")],
            },
        )
        collected = assembler._collect_skill_runtime_hooks(_result([s1, s2]))

        assert set(collected) == {"PreToolUse", "PostToolUse"}
        pre = collected["PreToolUse"]
        assert ("skill-a", RuntimeHook(matcher="Bash", script="hooks/a.sh")) in pre
        assert ("skill-b", RuntimeHook(matcher="Edit", script="hooks/b.sh")) in pre
        post = collected["PostToolUse"]
        assert post == [("skill-b", RuntimeHook(matcher="Write", script="hooks/c.sh"))]

    def test_empty_when_no_skill_declares(self, assembler, tmp_path):
        plain = tmp_path / "skills" / "plain"
        plain.mkdir(parents=True)
        (plain / "metadata.yaml").write_text("name: plain\n")
        collected = assembler._collect_skill_runtime_hooks(
            _result([_skill("plain", plain)])
        )
        assert collected == {}
