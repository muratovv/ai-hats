"""Session check snapshot: bound skills copied to ``<sid>/checks/`` (HATS-1241).

ADR-0019 D-b. The snapshot gives a bound check a session-consistent,
surface-independent root: the declaring skill's directory copied WHOLE, through
the ``Materializer`` port, so one code path serves the real session, ``--dry-run``
and the launch record alike.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from ai_hats_core import ComponentKind, CompositionResult, ResolvedCheck, ResolvedComponent

from ai_hats.libraries.models import CheckBindingError

from ai_hats.check_snapshot import legacy_launch_notices, snapshot_checks
from ai_hats.materialization import ApplyMaterializer, PlanMaterializer
from ai_hats.paths import session_checks_dir
from ai_hats.providers import Provider
from ai_hats.session_artifacts import BuiltArtifacts, RunMode, SessionPolicy

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


def test_second_build_for_one_session_keeps_the_first_bytes(tmp_path: Path):
    """Within a session a check runs the SAME bytes start to finish (epic R7).

    A rebuild for a live sid is reachable — the claude engine builds artifacts
    itself when it was handed none. ``copytree`` refuses an existing dest, so
    without this the second build raises; and re-copying would swap the bytes
    under a session that may already have fired the gate.
    """
    skill = _skill(tmp_path)
    result = _result(skills=[skill], checks=[_check(skill)])
    snapshot_checks(tmp_path, result, SID, port=ApplyMaterializer())

    (skill.source_path / "check.sh").write_text("#!/usr/bin/env bash\nexit 2\n")
    port = ApplyMaterializer()
    snapshot_checks(tmp_path, result, SID, port=port)

    snapshot = session_checks_dir(tmp_path, SID) / "gate-skill" / "check.sh"
    assert snapshot.read_text() == "#!/usr/bin/env bash\nexit 0\n"
    assert port.plan.entries == []


def test_dest_outside_the_checks_root_is_refused(tmp_path: Path):
    """The dest is derived from a name, so prove the derivation stays contained.

    Same defect class the sibling resolver already carries a guard for
    (``check_points._resolve_script``): a joined path that is never re-checked
    after ``resolve()`` writes wherever the name points.
    """
    skill = _skill(tmp_path)
    escaping = ResolvedComponent(
        name="../../escape", component_type=ComponentKind.SKILL, source_path=skill.source_path
    )
    result = _result(skills=[escaping], checks=[_check(escaping)])

    with pytest.raises(CheckBindingError, match="outside"):
        snapshot_checks(tmp_path, result, SID, port=ApplyMaterializer())


def test_two_bindings_on_one_skill_copy_it_once(tmp_path: Path):
    """Bindings fan out per point; the snapshot is per skill."""
    skill = _skill(tmp_path)
    result = _result(
        skills=[skill],
        checks=[_check(skill, point="wt:pre-merge"), _check(skill, point="wt:create")],
    )
    port = ApplyMaterializer()

    snapshot_checks(tmp_path, result, SID, port=port)

    assert len(port.plan.entries) == 1
    assert port.plan.duplicates() == []


def test_a_surface_that_cannot_snapshot_says_so(tmp_path: Path):
    """A pre-ADR-0018 surface never reaches the builder — so it never snapshots.

    ``WrapRunner`` degrades such a provider to ``build_session_prompt``
    (``wrap_runner.py:476-489``), which is below the wiring. A declared gate
    would then be missing with nothing said — the exact silence epic R3 exists
    to remove.
    """
    skill = _skill(tmp_path)
    result = _result(skills=[skill], checks=[_check(skill)])

    notices = legacy_launch_notices("legacy", result, SessionPolicy())

    assert len(notices) == 1
    assert "gate-skill" in notices[0]
    assert "NOT snapshotted" in notices[0]


def test_a_legacy_surface_with_no_bindings_is_quiet(tmp_path: Path):
    """Nothing bound, nothing lost — a warning here would be noise."""
    assert legacy_launch_notices("legacy", _result(), SessionPolicy()) == []


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


def test_each_session_snapshots_under_its_own_sid(tmp_path: Path):
    """A sub-agent mints its own session, so it snapshots into its own root.

    Both runners create a session before building artifacts
    (``wrap_runner.py:457``, ``subagent_runner.py:170``) and hand that sid to
    the builder — no special case, but the parent's frozen bytes must not be
    what the child gets, nor the other way round.
    """
    skill = _skill(tmp_path)
    result = _result(skills=[skill], checks=[_check(skill)])
    child_sid = "20260801-000000-2-43"

    snapshot_checks(tmp_path, result, SID, port=ApplyMaterializer())
    (skill.source_path / "check.sh").write_text("#!/usr/bin/env bash\nexit 2\n")
    snapshot_checks(tmp_path, result, child_sid, port=ApplyMaterializer())

    parent = session_checks_dir(tmp_path, SID) / "gate-skill" / "check.sh"
    child = session_checks_dir(tmp_path, child_sid) / "gate-skill" / "check.sh"
    assert parent.read_text() == "#!/usr/bin/env bash\nexit 0\n"
    assert child.read_text() == "#!/usr/bin/env bash\nexit 2\n"


def test_dry_run_reports_the_snapshot_without_writing_it(tmp_path: Path):
    """``--dry-run`` shows the checks a role brings; the port is why it is free.

    The report and the launch record are both rendered from
    ``artifacts.port.plan``, so routing the copy through the port is what makes
    the snapshot visible in ``--dry-run --json`` and in
    ``role_materialization.json`` without a second code path.
    """
    skill = _skill(tmp_path)
    port = PlanMaterializer()

    snapshot_checks(tmp_path, _result(skills=[skill], checks=[_check(skill)]), SID, port=port)

    dest = session_checks_dir(tmp_path, SID) / "gate-skill"
    entry = port.plan.entries[0]
    assert (entry.kind.value, entry.target, entry.source) == ("copy_tree", dest, skill.source_path)
    assert entry.file_count == 3, "the whole dir is reported, not just the script"
    assert not session_checks_dir(tmp_path, SID).exists(), "a dry-run must write nothing"


@pytest.mark.parametrize("run_mode", [RunMode.HITL, RunMode.AUTOMATE])
def test_build_session_artifacts_takes_the_snapshot(tmp_path: Path, run_mode: RunMode):
    """The one wiring that makes every launch path inherit it (ADR-0019 D-a/R6).

    The call sits in the base builder ABOVE the category loop: not a per-surface
    handler a surface could override, and not policy-gated — either would let a
    declared gate go silently missing on some surface or some policy. A sub-agent
    (AUTOMATE) reaches it through the same call, so both modes are asserted here.
    """
    skill = _skill(tmp_path)
    result = _result(skills=[skill], checks=[_check(skill)])

    _StubSurface().build_session_artifacts(
        tmp_path, result, SID, run_mode=run_mode, artifacts=BuiltArtifacts()
    )

    assert (session_checks_dir(tmp_path, SID) / "gate-skill" / "check.sh").is_file()


def test_session_teardown_takes_the_snapshot_with_it(tmp_path: Path):
    """No cleanup of its own: the snapshot lives inside the dir teardown drops.

    ``_cleanup_session_cache`` removes ``<sid>/`` wholesale, and the TTL sweep
    mops up what a SIGKILL orphaned — which is only true while the checks root
    stays UNDER the session dir. Move it out and this fails, which is the point.
    """
    from ai_hats.runtime_common import _cleanup_session_cache

    skill = _skill(tmp_path)
    snapshot_checks(
        tmp_path, _result(skills=[skill], checks=[_check(skill)]), SID, port=ApplyMaterializer()
    )
    assert session_checks_dir(tmp_path, SID).is_dir()

    _cleanup_session_cache(tmp_path, SID)

    assert not session_checks_dir(tmp_path, SID).exists()
