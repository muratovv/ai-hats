"""A bound check resolves from the surface's skill mirror (HATS-1540).

Successor of ``test_check_snapshot.py``. ADR-0019 D9 gave the channel a private
``<sid>/checks/`` copy so a binding had a session-consistent, surface-independent
root. That argument expired: every surface now writes an unconditional mirror of
EVERY composed skill, with the same lifetime, one writer and the same TTL — and
the private copy held only the BOUND skills, so a session started before a
binding existed had no root at all and refused every transition until restart.

What is asserted here: the second materialization is gone, the mirror is what a
check resolves against, and the two leaf-naming conventions are now one.
"""

from __future__ import annotations

from ai_hats_core.layout import ProjectLayout

from pathlib import Path

import pytest
from ai_hats_core import ComponentKind, CompositionResult, ResolvedCheck, ResolvedComponent

from ai_hats.check_resolve import CheckResolutionError, resolve_carried_checks
from ai_hats.check_snapshot import legacy_launch_notices
from ai_hats.materialization import PlanMaterializer
from ai_hats.surfaces import Surface
from ai_hats.session_artifacts import BuiltArtifacts, RunMode, SessionPolicy

SID = "20260801-000000-1-42"
EDGE = "review->done"


def _skill(root: Path, name: str = "gate-skill", script: str = "check.sh") -> ResolvedComponent:
    """A skill dir on disk: an executable script plus a sibling data file."""
    skill_dir = root / "library" / "skills" / name.replace("::", "-")
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# gate\n")
    (skill_dir / "data.json").write_text('{"threshold": 3}\n')
    script_path = skill_dir / script
    script_path.write_text("#!/usr/bin/env bash\nexit 0\n")
    script_path.chmod(0o755)
    return ResolvedComponent(name=name, component_type=ComponentKind.SKILL, source_path=skill_dir)


def _check(skill: ResolvedComponent, script: str = "check.sh", point: str = EDGE) -> ResolvedCheck:
    return ResolvedCheck(
        app="rack",
        path=("tasks",),
        run=f"{skill.name}/{script}",
        at=(point,),
        cargo={},
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


def _topology():
    from ai_hats_rack.fsm import Topology

    return Topology(
        initial="review",
        states=("review", "done"),
        edges={"review": ("done",), "done": ()},
    )


class _MirrorSurface(Surface):
    """A category-aware surface whose skill mirror is a plain named dir."""

    name = "stub"

    def get_cli_command(self, args: list[str] | None = None) -> list[str]:
        return ["stub-cli"]

    def get_env(self, session_dir: Path, layout) -> dict[str, str]:
        return {}

    def rules_dir(self, project_dir: Path) -> Path:
        return project_dir / ".stub" / "rules"

    def system_prompt_path(self, layout) -> Path:
        return layout.root / "STUB.md"

    def build_system_prompt(self, result) -> str:
        return "stub prompt"

    def session_skills_root(self, layout, session_id: str) -> Path:

        return layout.cache.session(session_id) / "stub-skills"

    def _build_skills_hitl(self, layout, result, session_id, artifacts) -> None:
        from ai_hats.skills_dir import materialize_skills_dir

        materialize_skills_dir(
            self.session_skills_root(layout, session_id),
            result.skills,
            layout,
            artifacts.port,
        )

    def _build_context_hitl(self, layout, result, session_id, artifacts) -> None:
        artifacts.full_content = self.build_system_prompt(result)


def _mirrored(project: Path, skill: ResolvedComponent, sid: str = SID) -> Path:
    """Build the session's artifacts and return the mirror's copy of ``skill``."""
    _MirrorSurface().build_session_artifacts(
        ProjectLayout.at(project),
        _result(skills=[skill]),
        sid,
        run_mode=RunMode.HITL,
        artifacts=BuiltArtifacts(),
    )
    return _MirrorSurface().session_skills_root(ProjectLayout.at(project), sid) / skill.name


def _identity(project: Path, sid: str = SID, skills_root: str | None = None):
    """The envelope a session of ``sid`` would carry (HATS-1594).

    ``skills_root`` defaults to what the stub surface mirrors, which is what the
    launch would have written into it; pass ``""`` for a surface that mirrors
    none.
    """
    from ai_hats.session_identity import SessionIdentity

    if skills_root is None:
        skills_root = str(_MirrorSurface().session_skills_root(ProjectLayout.at(project), sid))
    return SessionIdentity(
        id=sid,
        role="stub-role",
        provider="stub",
        project_dir=project,
        session_dir=project / ".agent" / "ai-hats" / "sessions" / "runs" / f"session_{sid}",
        skills_root=skills_root,
    )


def _resolve(project: Path, skill: ResolvedComponent, sid: str = SID, **kw):
    result = _result(skills=[skill], checks=[_check(skill)])
    # No sid is the live-resolution mode: no session, so no envelope.
    kw.setdefault("identity", _identity(project, sid) if sid else None)
    return resolve_carried_checks(
        project,
        "rack",
        compose=lambda _: result,
        **kw,
    )


def _project(tmp_path: Path) -> Path:
    (tmp_path / "ai-hats.yaml").write_text(
        "schema_version: 4\nprovider: stub\nai_hats_dir: .agent/ai-hats\n"
    )
    return tmp_path


@pytest.fixture(autouse=True)
def _stub_provider(monkeypatch):
    """Register the stub surface under the name the project config names."""
    from ai_hats import surface_registry as providers

    monkeypatch.setattr(providers, "get_surface", lambda name: _MirrorSurface())
    yield


# ---------------------------------------------------------------------------
# Tombstone: the second materialization is gone
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("run_mode", [RunMode.HITL, RunMode.AUTOMATE])
def test_no_session_ever_gets_a_checks_dir(tmp_path: Path, run_mode: RunMode):
    """F: ``<sid>/checks/`` is not created under any condition — the tombstone.

    Asserted with a binding present and through both run modes, because that is
    where the retired step used to fire: above the category loop, outside the
    policy gate. A literal path, not a helper: the helper is gone, and a test
    that imported one could not fail if the step came back under a new name.
    """

    skill = _skill(tmp_path)
    result = _result(skills=[skill], checks=[_check(skill)])

    _MirrorSurface().build_session_artifacts(
        ProjectLayout.at(tmp_path), result, SID, run_mode=run_mode, artifacts=BuiltArtifacts()
    )

    assert not (ProjectLayout.at(tmp_path).cache.session(SID) / "checks").exists()


def test_the_snapshot_writer_is_gone_from_the_module(tmp_path: Path):
    """The step is removed, not merely unwired — an unwired writer re-lands."""
    import ai_hats.check_snapshot as module

    assert not hasattr(module, "snapshot_checks")

    from ai_hats import paths

    assert not hasattr(paths, "session_checks_dir")


# ---------------------------------------------------------------------------
# Resolution against the mirror
# ---------------------------------------------------------------------------


def test_a_bound_check_resolves_to_the_mirrors_copy(tmp_path: Path):
    """In a session the script that runs is the mirror's, not the library's."""
    project = _project(tmp_path)
    skill = _skill(project)
    mirrored = _mirrored(project, skill)

    resolved = _resolve(project, skill)

    assert resolved[0].script_path == (mirrored / "check.sh").resolve()
    assert resolved[0].script_path.is_file()
    assert resolved[0].script_path.stat().st_mode & 0o111, "exec bit lost in the mirror"


def test_the_rebase_keeps_the_library_path_it_replaced(tmp_path: Path):
    """HATS-1651: the mirror is what RUNS; the library path is what it replaced.

    Only this rebase ever holds both, so a refusal downstream can ask "are these
    bytes still current?" only if the pair survives it. Keeping the path is not a
    second resolution root — nothing reads bytes from it in order to run them.
    """
    project = _project(tmp_path)
    skill = _skill(project)
    mirrored = _mirrored(project, skill)

    resolved = _resolve(project, skill)

    assert resolved[0].script_path == (mirrored / "check.sh").resolve()
    assert resolved[0].source_path == skill.source_path / "check.sh"


def test_outside_a_session_there_is_no_replaced_path_to_keep(tmp_path: Path):
    """Live resolution froze nothing, so there is no pair and nothing to compare."""
    project = _project(tmp_path)
    skill = _skill(project)

    resolved = _resolve(project, skill, sid="")

    assert resolved[0].source_path is None


def test_the_whole_skill_dir_is_there_not_just_the_script(tmp_path: Path):
    """ADR-0019 D-h ``bundle: dir``: a script that reads a sibling still can."""
    project = _project(tmp_path)
    skill = _skill(project)

    mirrored = _mirrored(project, skill)

    assert (mirrored / "data.json").read_text() == '{"threshold": 3}\n'


def test_a_session_that_predates_the_binding_still_resolves(tmp_path: Path):
    """E: the regression HATS-1538 withdrew the shipped row for.

    The mirror copies EVERY composed skill, so a session built before any
    ``checks:`` row existed already holds the bytes a binding added later names.
    Modelled exactly that way: the session's artifacts are built from a
    composition with NO checks, and the binding appears only afterwards.
    """
    project = _project(tmp_path)
    skill = _skill(project)
    _MirrorSurface().build_session_artifacts(
        ProjectLayout.at(project),
        _result(skills=[skill]),  # no checks at session build time
        SID,
        run_mode=RunMode.HITL,
        artifacts=BuiltArtifacts(),
    )

    resolved = _resolve(project, skill)

    assert resolved[0].script_path.is_file(), (
        "a session older than the binding must resolve, not report CORRUPT"
    )


def test_a_namespaced_skill_resolves_to_the_leaf_the_mirror_wrote(tmp_path: Path):
    """The two conventions become one, and the mirror is the authority.

    Every surface writes the leaf as the composed skill's raw ``name``; this
    module used to re-derive it with ``resolve_namespace``, so ``dev::python``
    was looked up as ``dev/python`` — a directory no surface had ever written.
    """
    project = _project(tmp_path)
    skill = _skill(project, name="dev::python")
    mirrored = _mirrored(project, skill)

    resolved = _resolve(project, skill)

    assert resolved[0].script_path == (mirrored / "check.sh").resolve()
    assert resolved[0].script_path.is_file()


def test_each_session_resolves_under_its_own_sid(tmp_path: Path):
    """A sub-agent mints its own session, so it reads its own mirror."""
    project = _project(tmp_path)
    skill = _skill(project)
    child_sid = "20260801-000000-2-43"
    _mirrored(project, skill, SID)
    (skill.source_path / "check.sh").write_text("#!/usr/bin/env bash\nexit 2\n")
    _mirrored(project, skill, child_sid)

    parent = _resolve(project, skill, SID)[0].script_path
    child = _resolve(project, skill, child_sid)[0].script_path

    assert parent.read_text() == "#!/usr/bin/env bash\nexit 0\n"
    assert child.read_text() == "#!/usr/bin/env bash\nexit 2\n"


def test_a_rebuild_within_one_session_re_copies_the_source(tmp_path: Path):
    """Stated consequence of retiring the private copy — not an accident.

    The retired snapshot was first-writer-wins, so its bytes were frozen for the
    session's lifetime. Every surface's mirror is wipe-and-rebuild (HATS-1248),
    and a rebuild for a live sid is reachable — the claude SDK engine builds
    artifacts itself when handed none. So a check now runs exactly the bytes the
    session's OWN runtime hooks run: one mirror, one rebuild, everything moves
    together. Asserted rather than assumed, because it is the one property the
    move away from ``<sid>/checks/`` did not preserve.
    """  # comment-length: allow — a deliberately weakened property must say so
    project = _project(tmp_path)
    skill = _skill(project)
    _mirrored(project, skill)

    (skill.source_path / "check.sh").write_text("#!/usr/bin/env bash\nexit 2\n")
    _mirrored(project, skill)

    assert _resolve(project, skill)[0].script_path.read_text() == "#!/usr/bin/env bash\nexit 2\n"


def test_the_mirror_lives_inside_what_session_teardown_drops(tmp_path: Path):
    """No cleanup of its own: teardown removes ``<sid>/`` wholesale."""
    from ai_hats.runtime_common import _cleanup_session_cache

    project = _project(tmp_path)
    skill = _skill(project)
    assert _mirrored(project, skill).is_dir()

    _cleanup_session_cache(ProjectLayout.at(project).cache.session(SID))

    assert not _MirrorSurface().session_skills_root(ProjectLayout.at(project), SID).exists()


# ---------------------------------------------------------------------------
# Fail-closed branches
# ---------------------------------------------------------------------------


def test_a_surface_that_mirrors_nothing_refuses(tmp_path: Path):
    """The ABC default is ``None``, and a gate with no bytes must not pass.

    HATS-1594 moved WHERE the surface is asked, not whether: the launch turns a
    ``None`` root into an empty ``skills_root``, and the refusal fires here on
    that. The launch half is pinned by ``test_session_identity_launch.py``.
    """
    project = _project(tmp_path)
    skill = _skill(project)

    with pytest.raises(CheckResolutionError, match="mirrors no skills"):
        _resolve(project, skill, identity=_identity(project, skills_root=""))


# HATS-1594 retired `test_an_unloadable_provider_refuses`. The channel no longer
# loads a provider at all — it reads the root off the envelope — so there is
# nothing left here to be unloadable. A session cannot exist under a provider
# that will not load: `build_composition_payload` resolves it through
# `get_surface` before anything launches, and refuses there.


def test_a_broken_composition_refuses_even_when_the_caller_supplied_it(tmp_path: Path):
    """HATS-1594: the ``result.errors`` refusal belongs to the CHANNEL.

    An in-process caller hands its own composition through ``compose=`` to avoid
    composing twice. While that refusal lived inside the fail-closed composer,
    passing ``compose=`` skipped it — so a role whose composition reported errors
    armed nothing while looking armed, at the one point that kills a launch.
    """
    from dataclasses import replace

    project = _project(tmp_path)
    skill = _skill(project)
    broken = replace(
        _result(skills=[skill], checks=[_check(skill)]), errors=["Role 'ghost' not found"]
    )

    with pytest.raises(CheckResolutionError, match="broken composition"):
        resolve_carried_checks(
            project, "rack", identity=_identity(project), compose=lambda _: broken
        )


def test_a_script_escaping_the_mirror_root_is_refused(tmp_path: Path):
    """The rebase joins a declared relative path, so prove it stays contained."""
    project = _project(tmp_path)
    skill = _skill(project)
    _mirrored(project, skill)
    result = _result(skills=[skill], checks=[_check(skill, script="../../../etc/passwd")])

    with pytest.raises(CheckResolutionError, match="outside this session's mirror root"):
        resolve_carried_checks(
            project, "rack", identity=_identity(project), compose=lambda _: result
        )


def test_outside_a_session_the_library_copy_runs(tmp_path: Path):
    """D: the live-resolution mode is unchanged — no mirror, no rebase."""
    project = _project(tmp_path)
    skill = _skill(project)

    resolved = _resolve(project, skill, sid="")

    assert resolved[0].script_path == skill.source_path / "check.sh"


# ---------------------------------------------------------------------------
# Launch-time notice for a surface below the builder
# ---------------------------------------------------------------------------


def test_a_surface_below_the_builder_says_it_mirrors_nothing(tmp_path: Path):
    """``WrapRunner`` degrades such a provider to ``build_session_prompt``, which
    is below the wiring — the loss is announced instead of discovered."""
    skill = _skill(tmp_path)
    result = _result(skills=[skill], checks=[_check(skill)])

    notices = legacy_launch_notices("legacy", result, SessionPolicy())

    assert len(notices) == 1
    assert "gate-skill" in notices[0]
    assert "NOT mirrored" in notices[0]


def test_a_legacy_surface_with_no_bindings_is_quiet(tmp_path: Path):
    """Nothing bound, nothing lost — a warning here would be noise."""
    assert legacy_launch_notices("legacy", _result(), SessionPolicy()) == []


# ---------------------------------------------------------------------------
# The launch-time skew notice (HATS-1540 review)
# ---------------------------------------------------------------------------


class _StaleSurface(_MirrorSurface):
    """A surface package older than the accessor: implements the ADR-0018 seam
    perfectly well, inherits the `Provider` default of ``None`` for the root."""

    def session_skills_root(self, layout, session_id: str):
        return Surface.session_skills_root(self, layout, session_id)

    def _build_skills_hitl(self, layout, result, session_id, artifacts) -> None:
        # It DOES write a mirror — it just will not say where. That asymmetry IS
        # the skew, so the fixture must not model it as "mirrors nothing".
        from ai_hats.skills_dir import materialize_skills_dir

        materialize_skills_dir(
            layout.cache.session(session_id) / "stub-skills",
            result.skills,
            layout,
            artifacts.port,
        )


def test_a_surface_that_cannot_root_a_bound_check_says_so_at_launch(tmp_path: Path):
    """The notice `legacy_launch_notices` does NOT give, and the ADR claimed it did.

    An out-of-date agy/cline handles the artifact-builder seam, so it never
    reaches the legacy notice — and then EVERY transition in its sessions is
    refused with a message about a missing file. Measured: the real e2e tier
    caught exactly this against a published surface package.
    """
    from ai_hats.check_snapshot import surface_skew_notice

    skill = _skill(tmp_path)
    result = _result(skills=[skill], checks=[_check(skill)])

    notice = surface_skew_notice("agy", _StaleSurface(), ProjectLayout.at(tmp_path), result)

    assert notice is not None
    assert "gate-skill" in notice
    assert "session_skills_root" in notice, "the notice must name the fix"


def test_a_surface_that_roots_checks_is_quiet(tmp_path: Path):
    """No skew, no noise — the notice must not fire on every ordinary launch."""
    from ai_hats.check_snapshot import surface_skew_notice

    skill = _skill(tmp_path)
    result = _result(skills=[skill], checks=[_check(skill)])

    assert surface_skew_notice("stub", _MirrorSurface(), ProjectLayout.at(tmp_path), result) is None


def test_a_stale_surface_with_no_bindings_is_quiet(tmp_path: Path):
    """Nothing bound, nothing to root — the skew is harmless and stays silent."""
    from ai_hats.check_snapshot import surface_skew_notice

    assert (
        surface_skew_notice("agy", _StaleSurface(), ProjectLayout.at(tmp_path), _result()) is None
    )


# --- what the launch report says about the bindings (HATS-1548) ---


def _preview(provider, result):
    """The read-only payload ``dry_run_hitl`` composes through."""
    from ai_hats.composition_seam import CompositionPayload

    return CompositionPayload(result=result, provider=provider, effective_role="tester")


class _SkilllessSurface(_MirrorSurface):
    """Names a mirror root and delivers no skills into it — so a binding
    resolves against a tree this launch never writes."""

    def _build_skills_hitl(self, layout, result, session_id, artifacts) -> None:
        return None


def _described(project: Path, result, provider=None, sid: str = SID):
    """Build this session's artifacts, then describe its checks off that plan."""
    from ai_hats.check_snapshot import describe_checks

    surface = provider or _MirrorSurface()
    artifacts = BuiltArtifacts(port=PlanMaterializer())
    surface.build_session_artifacts(
        ProjectLayout.at(project), result, sid, run_mode=RunMode.HITL, artifacts=artifacts
    )
    return describe_checks(surface, ProjectLayout.at(project), result, sid, artifacts.port.plan)


def test_a_planned_gate_is_reported_as_armed(tmp_path: Path):
    """The happy case, stated once: resolved into the mirror the launch writes."""
    project = _project(tmp_path)
    skill = _skill(project)
    result = _result(skills=[skill], checks=[_check(skill)])

    reported, notes = _described(project, result)

    (check,) = reported
    assert (
        check.runs_from
        == _MirrorSurface().session_skills_root(ProjectLayout.at(project), SID)
        / skill.name
        / "check.sh"
    )
    assert check.planned is True
    assert notes == ()


def test_a_surface_that_mirrors_nothing_leaves_every_gate_unresolved(tmp_path: Path):
    """``session_skills_root`` is None — no root, so no bytes, so no gate.

    Silent by design: ``surface_skew_notice`` already says this once at launch,
    and repeating it per binding would bury the rows it annotates.
    """
    project = _project(tmp_path)
    skill = _skill(project)
    result = _result(skills=[skill], checks=[_check(skill)])

    reported, notes = _described(project, result, provider=_StaleSurface())

    (check,) = reported
    assert check.runs_from is None
    assert check.planned is False
    assert notes == ()


def test_a_binding_over_an_uncomposed_skill_is_named_not_raised(tmp_path: Path):
    """A report that dies on a broken gate tells the operator less than one
    that names it — so the resolution error becomes a note, not an exception."""
    project = _project(tmp_path)
    composed = _skill(project)
    absent = _skill(project, name="ghost-skill")
    result = _result(skills=[composed], checks=[_check(absent)])

    reported, notes = _described(project, result)

    (check,) = reported
    assert check.runs_from is None
    assert check.planned is False
    assert len(notes) == 1
    assert "ghost-skill" in notes[0]


def test_a_gate_the_launch_does_not_write_is_reported_unplanned(tmp_path: Path):
    """Resolution settling is not the same as the bytes being there.

    The skill is composed, so it resolves — but this launch delivers no skills
    (the surface has no SKILLS handler), so nothing writes the tree the script
    would be read out of. That gap is exactly what ``planned`` exists to show.
    """
    project = _project(tmp_path)
    skill = _skill(project)
    result = _result(skills=[skill], checks=[_check(skill)])

    reported, notes = _described(project, result, provider=_SkilllessSurface())

    (check,) = reported
    assert check.runs_from is not None, "it resolves — the skill IS composed"
    assert check.planned is False
    assert notes == ()


def test_a_dry_run_under_a_stale_surface_warns_before_the_session_starts(
    tmp_path: Path, monkeypatch
):
    """The launch says the gate cannot root; the dry-run has to say it too.

    Staying quiet here is the same silence the notice exists to remove — the
    operator would learn it one session too late, which is the HATS-1538 shape.
    """
    from ai_hats import surface_registry as providers
    from ai_hats.dry_run import dry_run_hitl

    project = _project(tmp_path)
    skill = _skill(project)
    result = _result(skills=[skill], checks=[_check(skill)])
    monkeypatch.setattr(providers, "get_surface", lambda name: _StaleSurface())
    monkeypatch.setattr(
        "ai_hats.composition_seam.build_preview_payload",
        lambda *a, **kw: _preview(_StaleSurface(), result),
    )

    report = dry_run_hitl(ProjectLayout.at(project))

    assert any("session_skills_root" in note for note in report.notes), report.notes
    assert [c.runs_from for c in report.checks] == [None]


def test_a_gate_under_a_symlinked_root_is_still_reported_as_armed(tmp_path: Path):
    """`_plan_covers` compares a plan target against a RESOLVED `runs_from`.

    The plan records its target as the writer spelled it; `rebase_onto_mirror`
    returns a resolved path. Where the cache root contains a symlink — the macOS
    default, where /tmp is a link to /private/tmp — the unresolved parent never
    matched the resolved child, so EVERY armed gate was reported "NOT written by
    this launch". A false alarm in the one report whose job is to say otherwise.

    Asserted on the predicate, not through a session build: the surfaces under
    test resolve their own roots, so a session-level test passes either way and
    proves nothing (measured — the first version of this test did exactly that).
    """
    from ai_hats.check_snapshot import _plan_covers
    from ai_hats.materialization import MaterializationPlan, describe_copy_tree

    real = tmp_path / "real"
    (real / "skills" / "gate-skill").mkdir(parents=True)
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)

    plan = MaterializationPlan(entries=[describe_copy_tree(real, link / "skills" / "gate-skill")])
    runs_from = (real / "skills" / "gate-skill" / "check.sh").resolve()

    assert _plan_covers(plan, runs_from) is True


def test_resolve_checks_at_filters_by_app_as_well_as_point(tmp_path):
    """HATS-1581: the query is (app, point), not point alone.

    Both rows below name the SAME point on purpose. Nothing stops two apps from
    spelling a point identically — the app key exists precisely so they may — so
    a filter that looked only at ``at`` would hand the wt caller the session-start
    row, and the gate would run against the wrong lifecycle.
    """
    from dataclasses import replace

    from ai_hats.check_points import AI_HATS_APP, WT_APP
    from ai_hats.check_resolve import resolve_checks_at

    skill = _skill(tmp_path)
    base = _check(skill, point="shared")
    result = _result(
        skills=[skill],
        checks=(
            replace(base, app=WT_APP, path=()),
            replace(base, app=AI_HATS_APP, path=()),
        ),
    )

    for app in (WT_APP, AI_HATS_APP):
        got = resolve_checks_at(tmp_path, app, "shared", compose=lambda _p: result)

        assert [c.app for c in got] == [app], f"{app!r} must see only its own row"
