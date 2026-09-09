"""HATS-1917 — a hook row's tag must name the row, not just its matcher.

A skill may now declare two scripts on one matcher. The tag is what the catch
journal, the gate-broken channel and a refusal message call the hook that acted,
so two rows sharing one label would report a firing without saying which gate
fired — against exactly the telemetry HATS-1634 built.

All five surfaces are checked together because they interpolate the same shape:
one left behind goes quiet rather than wrong, which is the harder failure to
notice. The composition is real — a fixture skill on disk, no patched imports —
so this also exercises the validator that now allows the second row at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from ai_hats_core import ComponentKind, CompositionResult, ResolvedComponent
from ai_hats_core.layout import ProjectLayout
from ai_hats.materialization import PlanMaterializer
from ai_hats.session_artifacts import BuiltArtifacts

SKILL = "two-guards"
EVENT = "PreToolUse"
SESSION_ID = "test-session-id"


def _skill_with_two_bash_hooks(base: Path) -> ResolvedComponent:
    """A skill dir declaring two PreToolUse/Bash scripts, both present on disk."""
    skill_dir = base / SKILL
    skill_dir.mkdir(parents=True, exist_ok=True)
    lines = ["---", f"name: {SKILL}", "ai_hats:", "  runtime_hooks:", f"    {EVENT}:"]
    for script in ("hooks/first_gate.sh", "hooks/second_gate.sh"):
        lines += ["      - matcher: Bash", f"        script: {script}"]
        sp = skill_dir / script
        sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_text("#!/usr/bin/env bash\nexit 0\n")
        sp.chmod(0o755)  # a declared gate ships executable (HATS-1862 drops one that does not)
    lines += ["---", f"# {SKILL}"]
    (skill_dir / "SKILL.md").write_text("\n".join(lines) + "\n")
    return ResolvedComponent(name=SKILL, component_type=ComponentKind.SKILL, source_path=skill_dir)


def _result(skill: ResolvedComponent) -> CompositionResult:
    return CompositionResult(name="r", priorities=[], rules=[], skills=[skill], injections=[])


def _mirrored(result, skills_dir: Path) -> BuiltArtifacts:
    """The port as the SKILLS handler leaves it: the mirror recorded, nothing written."""
    port = PlanMaterializer()
    for skill in result.skills:
        port.copy_tree(skill.source_path, skills_dir / skill.name)
    return BuiltArtifacts(port=port)


def _claude(result, project, skills_dir):
    from ai_hats.hook_collection import composed_rows

    rows, _notices = composed_rows(result, skills_dir, port=_mirrored(result, skills_dir).port)
    return rows.get(EVENT, [])


def _cline(result, project, skills_dir):
    from ai_hats.surfaces.cline.runtime_hooks import _manifest

    artifacts = _mirrored(result, skills_dir)
    return _manifest(project, result, SESSION_ID, skills_dir=skills_dir, artifacts=artifacts)[
        "hooks"
    ].get(EVENT, [])


def _codex(result, project, skills_dir):
    from ai_hats.surfaces.codex.runtime_hooks import _manifest

    artifacts = _mirrored(result, skills_dir)
    return _manifest(project, result, SESSION_ID, skills_dir=skills_dir, artifacts=artifacts)[
        "hooks"
    ].get(EVENT, [])


def _opencode(result, project, skills_dir):
    from ai_hats.surfaces.opencode.runtime_hooks import _manifest

    artifacts = _mirrored(result, skills_dir)
    return _manifest(
        project,
        result,
        SESSION_ID,
        skills_dir=skills_dir,
        permission_rules=[],
        artifacts=artifacts,
    )["hooks"].get(EVENT, [])


def _agy(result, project, _skills_dir):
    """agy takes its skills dir from the surface, so it gets the whole surface."""
    from ai_hats.surfaces.agy.provider import AgySurface

    surface = AgySurface()
    artifacts = _mirrored(result, surface._session_skills_dir(project, SESSION_ID))
    return surface._hooks_manifest(project, result, SESSION_ID, artifacts).get(EVENT, [])


SURFACES = {
    "agy": _agy,
    "claude": _claude,
    "cline": _cline,
    "codex": _codex,
    "opencode": _opencode,
}


def _rows(tmp_path: Path, surface: str) -> list[dict]:
    project = tmp_path / "proj"
    project.mkdir(exist_ok=True)
    result = _result(_skill_with_two_bash_hooks(tmp_path / "skills"))
    return SURFACES[surface](result, ProjectLayout.at(project), tmp_path / "skills-mirror")


@pytest.mark.parametrize("surface", sorted(SURFACES))
def test_both_rows_survive_with_distinct_tags(tmp_path, surface):
    rows = _rows(tmp_path, surface)
    assert len(rows) == 2, f"{surface}: a row went missing: {rows}"
    tags = [row["tag"] for row in rows]
    assert len(set(tags)) == 2, f"{surface}: two rows share one tag: {tags}"


@pytest.mark.parametrize("surface", sorted(SURFACES))
def test_the_tag_still_starts_with_the_owner_and_skill(tmp_path, surface):
    """The sweeper matches the `ai-hats:` prefix and the display name reads
    segment 1, so extending the tag must not disturb its head."""
    for row in _rows(tmp_path, surface):
        assert row["tag"].startswith(f"ai-hats:{SKILL}:{EVENT}:Bash"), row["tag"]
