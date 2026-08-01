"""Session check snapshot: bound skills copied to ``<sid>/checks/`` (HATS-1241).

ADR-0019 D-b. The snapshot gives a bound check a session-consistent,
surface-independent root: the declaring skill's directory copied WHOLE, through
the ``Materializer`` port, so one code path serves the real session, ``--dry-run``
and the launch record alike.
"""

from __future__ import annotations

from pathlib import Path

from ai_hats_core import ComponentKind, CompositionResult, ResolvedCheck, ResolvedComponent

from ai_hats.check_snapshot import snapshot_checks
from ai_hats.materialization import ApplyMaterializer, PlanMaterializer
from ai_hats.paths import session_checks_dir
from ai_hats.providers import Provider
from ai_hats.session_artifacts import BuiltArtifacts, RunMode

SID = "20260801-000000-1-42"


def _skill(root: Path, name: str = "gate-skill", script: str = "check.sh") -> ResolvedComponent:
    """A skill dir on disk: an executable script plus a sibling data file."""
    skill_dir = root / "library" / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# gate\n")
    (skill_dir / "data.json").write_text('{"threshold": 3}\n')
    script_path = skill_dir / script
    script_path.write_text("#!/usr/bin/env bash\nexit 0\n")
    script_path.chmod(0o755)
    return ResolvedComponent(name=name, component_type=ComponentKind.SKILL, source_path=skill_dir)


def _check(skill: ResolvedComponent, script: str = "check.sh", point: str = "wt:pre-merge"):
    return ResolvedCheck(
        skill=skill.name,
        script=script,
        point=point,
        on_error="refuse",
        script_path=skill.source_path / script,
        declared_by="trait-x",
    )


def _result(*, skills=(), checks=()) -> CompositionResult:
    return CompositionResult(
        name="tester",
        priorities=["Reliability"],
        rules=[],
        skills=list(skills),
        injections=["body"],
        checks=tuple(checks),
    )


def test_no_bindings_creates_no_checks_dir(tmp_path: Path):
    """A role with no bindings must not create ``checks/`` at all."""
    port = ApplyMaterializer()

    snapshot_checks(tmp_path, _result(skills=[_skill(tmp_path)]), SID, port=port)

    assert not session_checks_dir(tmp_path, SID).exists()
    assert port.plan.entries == []


def test_binding_copies_whole_skill_dir_executable(tmp_path: Path):
    """The WHOLE dir lands — sibling data included — and the script stays runnable.

    ``bundle: dir`` (ADR-0019 D-h): a flatten copy of the script alone drops the
    data files it reads. The exec bit rides ``copytree``'s ``copy2``; nothing
    chmods afterwards (D-g), so a lost bit here is a check that cannot run.
    """
    skill = _skill(tmp_path)
    port = ApplyMaterializer()

    snapshot_checks(tmp_path, _result(skills=[skill], checks=[_check(skill)]), SID, port=port)

    dest = session_checks_dir(tmp_path, SID) / "gate-skill"
    assert (dest / "SKILL.md").read_text() == "# gate\n"
    assert (dest / "data.json").read_text() == '{"threshold": 3}\n'
    assert (dest / "check.sh").stat().st_mode & 0o111, "exec bit lost in the snapshot"
    assert [(e.kind.value, e.target) for e in port.plan.entries] == [("copy_tree", dest)]


class _StubSurface(Provider):
    """A category-aware surface, reduced to what the builder needs."""

    name = "stub"

    def get_cli_command(self) -> list[str]:
        return ["stub-cli"]

    def get_env(self, session_dir: Path, project_dir: Path) -> dict[str, str]:
        return {}

    def rules_dir(self, project_dir: Path) -> Path:
        return project_dir / ".stub" / "rules"

    def system_prompt_path(self, project_dir: Path) -> Path:
        return project_dir / "STUB.md"

    def build_system_prompt(self, result) -> str:
        return "stub prompt"

    def _build_context_hitl(self, project_dir, result, session_id, artifacts) -> None:
        artifacts.full_content = self.build_system_prompt(result)


def test_build_session_artifacts_takes_the_snapshot(tmp_path: Path):
    """The one wiring that makes every launch path inherit it (ADR-0019 D-a/R6).

    The call sits in the base builder ABOVE the category loop: not a per-surface
    handler a surface could override, and not policy-gated — either would let a
    declared gate go silently missing on some surface or some policy.
    """
    skill = _skill(tmp_path)
    result = _result(skills=[skill], checks=[_check(skill)])

    _StubSurface().build_session_artifacts(
        tmp_path, result, SID, run_mode=RunMode.HITL, artifacts=BuiltArtifacts()
    )

    assert (session_checks_dir(tmp_path, SID) / "gate-skill" / "check.sh").is_file()
