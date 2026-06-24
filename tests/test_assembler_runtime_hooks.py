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
from ai_hats.models import ComponentType, RuntimeHook


def _skill(name: str, source_path: Path) -> ResolvedComponent:
    return ResolvedComponent(
        name=name,
        component_type=ComponentType.SKILL,
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


class TestMaterializeRuntimeHooks:
    """Materialization of skill-declared runtime-hook scripts (HATS-597 step 3).

    The script declared in a skill's ``runtime_hooks:`` lands under
    :func:`hooks_dir` at the collision-free
    :func:`managed_runtime_hook_filename` path — the SAME path the provider
    writes into settings.json — ``0o755``, tracked in ``.manifest``, swept
    when the skill leaves the composition. The hard-coded HATS-437 guard
    scripts (package data) stay materialized throughout.
    """

    def test_materializes_skill_script_alongside_package_guards(
        self, assembler, tmp_path
    ):
        from ai_hats.paths import hooks_dir, managed_runtime_hook_filename

        s = _make_skill_with_runtime_hooks(
            tmp_path / "skills",
            "skill-a",
            {"PreToolUse": [("Bash", "hooks/guard.sh")]},
        )
        assembler._materialize_pretooluse_hooks(_result([s]))

        target = hooks_dir(assembler.project_dir)
        dest = target / managed_runtime_hook_filename("skill-a", "hooks/guard.sh")
        assert dest.is_file()
        assert dest.read_text() == "#!/usr/bin/env bash\nexit 0\n"
        assert dest.stat().st_mode & 0o777 == 0o755
        manifest = (target / ".manifest").read_text()
        assert dest.name in manifest
        # HATS-437 guard (package data) materialized as before.
        assert (target / "pre_bash_shared_state_guard.sh").is_file()

    def test_removing_skill_sweeps_its_runtime_hook(self, assembler, tmp_path):
        from ai_hats.paths import hooks_dir, managed_runtime_hook_filename

        s = _make_skill_with_runtime_hooks(
            tmp_path / "skills",
            "skill-a",
            {"PreToolUse": [("Bash", "hooks/guard.sh")]},
        )
        target = hooks_dir(assembler.project_dir)
        dest = target / managed_runtime_hook_filename("skill-a", "hooks/guard.sh")

        assembler._materialize_pretooluse_hooks(_result([s]))
        assert dest.is_file()

        # Skill leaves the composition → its script is swept; guard survives.
        assembler._materialize_pretooluse_hooks(_result([]))
        assert not dest.exists()
        assert (target / "pre_bash_shared_state_guard.sh").is_file()

    def test_none_result_materializes_only_package_guards(self, assembler):
        from ai_hats.paths import hooks_dir

        # Legacy bare-bump path (no active role) — guards only, no crash.
        assembler._materialize_pretooluse_hooks(None)
        assert (
            hooks_dir(assembler.project_dir) / "pre_bash_shared_state_guard.sh"
        ).is_file()
