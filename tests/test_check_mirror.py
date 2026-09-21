"""A bound check resolves from the surface's skill mirror (HATS-1540), and the
launch record says where it runs from (ADR-0036 D6).

Successor of ``test_check_snapshot.py``. ADR-0019 D9 gave the channel a private
``<sid>/checks/`` copy so a binding had a session-consistent, surface-independent
root. That argument expired: every surface mirrors EVERY composed skill, with the
same lifetime, one writer and the same TTL — and the private copy held only the
BOUND skills, so a session started before a binding existed had no root at all
and refused every transition until restart.

What is asserted here: the second materialization is gone, the mirror the plan
writes is what a check resolves against, the two leaf-naming conventions are
one, and the record's ``checks`` rows are read off the plan — never off the disk.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from ai_hats_core import ComponentKind, CompositionResult, ResolvedCheck, ResolvedComponent
from ai_hats_core.layout import ProjectLayout

from ai_hats.check_resolve import CheckResolutionError, resolve_carried_checks
from ai_hats.fs_digest import dir_digest
from ai_hats.materialization import describe_mkdir
from ai_hats.session_artifacts import RunMode, SessionPolicy
from ai_hats.session_plan import preview, probe_host
from ai_hats.surfaces import Surface, apply, checks_record
from ai_hats.surfaces.mirror import mirror_entries
from ai_hats.surfaces.plan import (
    CompositionPlan,
    Executable,
    ExternalHook,
    Hooks,
    Launch,
    MaterializationPlan,
    OnError,
    Prompt,
    PromptBlock,
    PromptMember,
    Skill,
)

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


def _composition(*, skills=(), checks=()) -> CompositionPlan:
    """The composition half the adapter would make of ``_result``: each skill
    a mirrored tree, each binding an external row with the script's bytes named."""

    def executable(path: Path) -> Executable:
        return Executable(
            path=path.resolve(), content_digest=hashlib.sha256(path.read_bytes()).hexdigest()
        )

    return CompositionPlan(
        identity="tester",
        prompt=Prompt((PromptBlock(None, (PromptMember("tester::prompt", "body", None),)),)),
        skills=tuple(
            Skill(
                name=f"skills::{s.name}",
                path=s.source_path.resolve(),
                content_digest=dir_digest(s.source_path),
                document=(s.source_path / "SKILL.md").read_text(),
            )
            for s in skills
        ),
        hooks=Hooks(
            runtime=(),
            external=tuple(
                ExternalHook(
                    app=c.app,
                    object=".".join(c.path) or None,
                    at=at,
                    run=executable(c.script_path),
                    on_error=OnError(c.on_error),
                    declared_by=c.declared_by,
                )
                for c in checks
                for at in c.at
            ),
        ),
        trace=(),
    )


class _MirrorSurface(Surface):
    """A planning surface whose skill mirror is a plain named dir."""

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

    def _mirror(self, composition: CompositionPlan, root: Path) -> list:
        skills_root = root / "stub-skills"
        return [describe_mkdir(skills_root), *mirror_entries(composition, skills_root)]

    def plan(self, composition, *, run_mode, policy, root, layout, host):
        return MaterializationPlan(
            composition=composition,
            prompt=composition.prompt,
            surface=self.name,
            run_mode=RunMode(run_mode),
            policy=policy,
            root=root,
            entries=(describe_mkdir(root), *self._mirror(composition, root)),
            env={},
            launch=Launch(args=(), sdk_options=None),
        )


class _SkilllessSurface(_MirrorSurface):
    """Names a mirror root and delivers no skills into it — so a binding
    resolves against a tree this launch never writes."""

    def _mirror(self, composition, root):
        return []


def _plan(
    project: Path, composition: CompositionPlan, surface=None, sid: str = SID, run_mode=RunMode.HITL
):
    surface = surface or _MirrorSurface()
    layout = ProjectLayout.at(project)
    return surface.plan(
        composition,
        run_mode=run_mode,
        policy=SessionPolicy(),
        root=layout.cache.session(sid),
        layout=layout,
        host=probe_host(surface=surface),
    )


def _mirrored(project: Path, skill: ResolvedComponent, sid: str = SID) -> Path:
    """Apply the session's plan and return the mirror's copy of ``skill``."""
    apply(_plan(project, _composition(skills=[skill]), sid=sid))
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
    """F: ``<sid>/checks/`` is not planned or written under any condition — the tombstone.

    Asserted with a binding present and through both run modes, because that is
    where the retired step used to fire. A literal path, not a helper: the helper
    is gone, and a test that imported one could not fail if the step came back
    under a new name.
    """
    skill = _skill(tmp_path)
    plan = _plan(tmp_path, _composition(skills=[skill], checks=[_check(skill)]), run_mode=run_mode)

    apply(plan)

    assert not any("/checks/" in str(e.target) or e.target.name == "checks" for e in plan.entries)
    assert not (ProjectLayout.at(tmp_path).cache.session(SID) / "checks").exists()


def test_the_snapshot_writer_is_gone_from_the_tree():
    """The step is removed, not merely unwired — an unwired writer re-lands."""
    import importlib.util

    from ai_hats import paths

    assert importlib.util.find_spec("ai_hats.check_snapshot") is None
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

    The mirror copies EVERY composed skill, so a session planned before any
    ``checks:`` row existed already holds the bytes a binding added later names.
    Modelled exactly that way: the session is applied from a composition with
    NO checks, and the binding appears only afterwards.
    """
    project = _project(tmp_path)
    skill = _skill(project)
    apply(_plan(project, _composition(skills=[skill])))  # no checks at session build time

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
    session's lifetime. A session's mirror is a synced tree (ADR-0036 D3): a plan
    made against the changed source re-copies it, so a check runs exactly the
    bytes the session's OWN runtime hooks run — one mirror, everything moves
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


# --- what the launch record says about the bindings (HATS-1548, ADR-0036 D6) ---


def test_a_planned_gate_is_reported_as_armed(tmp_path: Path):
    """The happy case, stated once: resolved into the mirror the plan writes."""
    project = _project(tmp_path)
    skill = _skill(project)
    plan = _plan(project, _composition(skills=[skill], checks=[_check(skill)]))

    (check,) = checks_record(plan)

    assert check["runs_from"] == str(
        _MirrorSurface().session_skills_root(ProjectLayout.at(project), SID)
        / skill.name
        / "check.sh"
    )
    assert check["planned"] is True
    assert (check["skill"], check["script"], check["app"], check["at"]) == (
        skill.name,
        "check.sh",
        "rack",
        EDGE,
    )


def test_a_gate_the_launch_does_not_write_is_reported_unplanned(tmp_path: Path):
    """The skill is composed, so the script has a home — but this launch writes
    no mirror, so nothing holds the bytes the gate would be read out of. That
    gap is exactly what ``planned`` exists to show, and it is read off the
    plan's entries, never off the disk."""
    project = _project(tmp_path)
    skill = _skill(project)
    composition = _composition(skills=[skill], checks=[_check(skill)])

    (check,) = checks_record(_plan(project, composition, surface=_SkilllessSurface()))

    assert check["skill"] == skill.name, "it has a home — the skill IS composed"
    assert check["runs_from"] is None
    assert check["planned"] is False


def test_a_binding_over_an_uncomposed_skill_is_named_not_raised(tmp_path: Path):
    """A record that dies on a broken gate tells the operator less than one
    that names it: the row keeps the script's own path and no mirror."""
    project = _project(tmp_path)
    composed = _skill(project)
    absent = _skill(project, name="ghost-skill")
    plan = _plan(project, _composition(skills=[composed], checks=[_check(absent)]))

    (check,) = checks_record(plan)

    assert check["skill"] is None
    assert check["script"] == str((absent.source_path / "check.sh").resolve())
    assert check["runs_from"] is None
    assert check["planned"] is False


def test_the_dry_run_reports_the_gate_the_launch_cannot_root(tmp_path: Path, monkeypatch):
    """The launch record and the dry-run are one projection (ADR-0036 D5): a
    gate no mirror will hold is unplanned in the report before the session
    starts — the HATS-1538 shape, said one session earlier."""
    from types import SimpleNamespace

    project = _project(tmp_path)
    skill = _skill(project)
    surface = _SkilllessSurface()
    payload = SimpleNamespace(
        provider=surface,
        layout=ProjectLayout.at(project),
        plan=_composition(skills=[skill], checks=[_check(skill)]),
        effective_role="tester",
        diagnostics=(),
    )
    monkeypatch.setattr("ai_hats.composition_seam.build_preview_payload", lambda *a, **kw: payload)
    monkeypatch.setattr("ai_hats.surface_registry.get_surface", lambda name: surface)

    shown = preview(
        ProjectLayout.at(project), role="tester", provider="stub", run_mode=RunMode.HITL
    )

    assert [c["planned"] for c in shown.record["checks"]] == [False]
    assert "UNRESOLVED" in _render(shown.record), "the human rendering says it too"


def _render(record: dict) -> str:
    from ai_hats.session_plan import render_record

    return render_record(record)


def test_runs_from_is_the_plans_spelling_of_the_mirror_not_a_resolved_path(tmp_path: Path):
    """The row is read off the ``copy_tree`` entry whose source is the skill, so
    a cache root spelled through a symlink stays spelled that way: no
    resolution on disk, hence none of the macOS ``/tmp`` → ``/private/tmp``
    asymmetry that once reported every armed gate as unwritten."""
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    project = _project(link)
    skill = _skill(real)
    plan = _plan(project, _composition(skills=[skill], checks=[_check(skill)]))

    (check,) = checks_record(plan)

    assert check["planned"] is True
    assert check["runs_from"].startswith(str(ProjectLayout.at(project).cache.session(SID)))


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
