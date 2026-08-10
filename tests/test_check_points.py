"""Row resolution and validation (HATS-1140, HATS-1545, ADR-0019 D3/D4/D6).

Every case here is a way a declared gate can fail to install. The contract is
that each one is LOUD at composition — ``CheckBindingError``, not an entry in
``CompositionResult.errors``, which ``composition_seam`` tolerates silently on
the implicit-role path.
"""

from __future__ import annotations

import pytest
from ai_hats_core import ComponentKind, ResolvedComponent

from ai_hats.check_points import (
    KNOWN_APPS,
    CheckBindingError,
    owns_app,
    resolve_checks,
    wt_points,
)
from ai_hats.models import AppBinding


@pytest.fixture
def skill(tmp_path):
    """A composed skill whose directory holds one healthy script."""
    skill_dir = tmp_path / "skills" / "gate-skill"
    (skill_dir / "hooks").mkdir(parents=True)
    script = skill_dir / "hooks" / "gate.sh"
    script.write_text("#!/usr/bin/env bash\nexit 0\n")
    script.chmod(0o755)
    return ResolvedComponent(
        name="gate-skill", component_type=ComponentKind.SKILL, source_path=skill_dir
    )


def _row(**overrides) -> AppBinding:
    """One rack row on ``apps.rack.tasks``, the shape a role now declares."""
    fields = {
        "declared_by": "trait-x",
        "app": "rack",
        "path": ("tasks",),
        "run": "gate-skill/hooks/gate.sh",
        "on_error": "refuse",
        "cargo": {"at": ["edge:plan--execute"]},
    }
    fields.update(overrides)
    return AppBinding(**fields)


def _wt(**overrides) -> AppBinding:
    """A row under ai-hats's own app, whose cargo IS validated here."""
    overrides.setdefault("cargo", {"at": ["pre-merge"]})
    return _row(app="wt", path=(), **overrides)


def test_ai_hats_owns_one_app_and_judges_only_its_cargo():
    """HATS-1545: the app is a KEY now, so routing needs no namespace table.

    ``rack`` is in the roster (something collects it) but is not owned — what a
    rack row means is the rack's question, and ai-hats validates none of it.
    """
    assert owns_app("wt")
    assert not owns_app("rack")
    assert {"rack", "wt"} <= KNOWN_APPS
    assert wt_points()["pre-merge"] is False
    assert wt_points()["create"] is True
    assert wt_points()["teardown[merge]"] is False


def test_a_foreign_apps_cargo_is_carried_not_judged(skill):
    """D11: what a rack row's cargo means is the rack's question. Carried
    verbatim, with provenance intact and no grammar check of any kind."""
    (resolved,) = resolve_checks([_row(cargo={"at": ["edge:bogus--state"], "weird": 7})], [skill])

    assert resolved.cargo == {"at": ["edge:bogus--state"], "weird": 7}
    assert resolved.declared_by == "trait-x"
    assert resolved.app == "rack"
    assert resolved.path == ("tasks",)


def test_a_run_naming_no_script_is_loud(skill):
    """The one shape ai-hats keeps: ``run`` is ``<skill>/<script>``, so a bare
    name reaches no script and is a typo by construction."""
    with pytest.raises(CheckBindingError, match="names no script"):
        resolve_checks([_row(run="gate.sh")], [skill])


def test_warn_is_rejected_at_wt_data_protection_points(skill):
    """ADR-0019 D4: ADR-0012 deliberately put the failure-policy lever for
    worktree data protection in the engine; moving it into user YAML would let a
    component opt out of the harvest."""
    for point in ("pre-merge", "teardown[merge]"):
        with pytest.raises(CheckBindingError, match="on_error: warn"):
            resolve_checks([_wt(cargo={"at": [point]}, on_error="warn")], [skill])


def test_warn_is_allowed_at_wt_policy_points(skill):
    (resolved,) = resolve_checks([_wt(cargo={"at": ["create"]}, on_error="warn")], [skill])

    assert resolved.on_error == "warn"


def test_a_wt_point_ai_hats_does_not_fire_is_loud(skill):
    """``wt`` is ai-hats's own app, so an unknown point there is not "someone
    else's grammar" — it is a name nothing will ever fire."""
    with pytest.raises(CheckBindingError, match="not a point of the 'wt' app"):
        resolve_checks([_wt(cargo={"at": ["pre-merj"]})], [skill])


def test_a_wt_row_bound_to_nothing_is_loud(skill):
    with pytest.raises(CheckBindingError, match="at least one point"):
        resolve_checks([_wt(cargo={})], [skill])


def test_binding_to_uncomposed_skill_is_loud(skill):
    """ADR-0019 D2: a row does NOT implicitly pull the skill in — implicit
    composition is how you get a surprise gate. So an unknown name is a typo."""
    with pytest.raises(CheckBindingError, match="composes no skill"):
        resolve_checks([_row(run="ghost-skill/hooks/gate.sh")], [skill])


def test_binding_to_overlay_removed_skill_warns_and_drops(skill, capsys):
    """ADR-0019 D6 rev 7 / D-e: hard-failing would force a user making a
    legitimate --remove-skill to author a new role; silence would drop a gate
    they expected. So: warn, name the row's site, drop, continue."""
    resolved = resolve_checks(
        [_row(run="removed-skill/hooks/gate.sh")],
        [skill],
        removed_skills={"removed-skill"},
    )

    assert resolved == ()
    err = capsys.readouterr().err
    assert "removed-skill" in err
    assert "trait-x" in err


def test_script_escaping_the_skill_dir_is_loud(skill, tmp_path):
    """ADR-0019 D6. The live hole this closes: an unresolved ``skill_dir /
    script`` join lets ``../../../x.sh`` escape, be read, and be materialized
    0755. Under D2 the row is authored in a TRAIT that need not own the skill,
    which is worse."""
    outside = tmp_path / "outside.sh"
    outside.write_text("#!/usr/bin/env bash\n")
    outside.chmod(0o755)

    with pytest.raises(CheckBindingError, match="outside the skill"):
        resolve_checks([_row(run="gate-skill/../../outside.sh")], [skill])


def test_absolute_script_is_loud(skill):
    """The same escape with a shorter payload: ``Path('/a') / '/etc/passwd'`` is
    ``/etc/passwd``, so the join alone never leaves the skill dir."""
    with pytest.raises(CheckBindingError, match="outside the skill"):
        resolve_checks([_row(run="gate-skill//etc/passwd")], [skill])


def test_missing_script_is_loud(skill):
    with pytest.raises(CheckBindingError, match="not found"):
        resolve_checks([_row(run="gate-skill/hooks/absent.sh")], [skill])


def test_empty_script_is_loud(skill):
    (skill.source_path / "hooks" / "empty.sh").write_text("   \n")
    (skill.source_path / "hooks" / "empty.sh").chmod(0o755)

    with pytest.raises(CheckBindingError, match="empty"):
        resolve_checks([_row(run="gate-skill/hooks/empty.sh")], [skill])


def test_script_without_shebang_is_loud(skill):
    script = skill.source_path / "hooks" / "noshebang.sh"
    script.write_text("echo hi\n")
    script.chmod(0o755)

    with pytest.raises(CheckBindingError, match="shebang"):
        resolve_checks([_row(run="gate-skill/hooks/noshebang.sh")], [skill])


def test_non_executable_script_is_loud(skill):
    """ADR-0019 D6 rev 7: the session skill mirror copies mode verbatim and never
    chmods — so a 644 script is dead on arrival at every point it is bound to.
    Checking it beside the shebang turns a runtime *broke* into an authoring
    error."""
    script = skill.source_path / "hooks" / "notexec.sh"
    script.write_text("#!/usr/bin/env bash\n")
    script.chmod(0o644)

    with pytest.raises(CheckBindingError, match="not executable"):
        resolve_checks([_row(run="gate-skill/hooks/notexec.sh")], [skill])


def test_duplicate_row_collapses_with_strictest_on_error_and_warns(skill, capsys):
    """R5/R7: dedup by identity, strictest ``on_error`` winning, and a WARN that
    names the second declarer. A trait that relaxes a gate the role hardened must
    not win by declaration order — and declaring it twice must not silently
    install two gates either (R5 is a WARN, deliberately not a refusal)."""
    trait_first = resolve_checks(
        [_row(declared_by="trait-x", on_error="warn"), _row(declared_by="role-y")],
        [skill],
    )
    role_first = resolve_checks(
        [_row(declared_by="role-y"), _row(declared_by="trait-x", on_error="warn")],
        [skill],
    )

    assert [(c.declared_by, c.on_error) for c in trait_first] == [("trait-x", "refuse")]
    assert [(c.declared_by, c.on_error) for c in role_first] == [("role-y", "refuse")]
    err = capsys.readouterr().err
    assert "declared again" in err
    assert "role-y" in err and "trait-x" in err


def test_dedup_keeps_rows_whose_cargo_differs(skill):
    """R6: cargo is compared whole and opaquely, so one script at two different
    points is two rows — ai-hats cannot know that ``at`` is what separates
    them, and does not need to."""
    other = skill.source_path / "hooks" / "other.sh"
    other.write_text("#!/usr/bin/env bash\n")
    other.chmod(0o755)

    resolved = resolve_checks(
        [
            _row(cargo={"at": ["edge:plan--execute"]}),
            _row(cargo={"at": ["edge:execute--review"]}),
            _row(run="gate-skill/hooks/other.sh"),
        ],
        [skill],
    )

    assert len(resolved) == 3


def test_dedup_separates_rows_that_differ_only_by_backlog(skill):
    """The path is part of the identity: the same script gating two backlogs is
    two gates, and collapsing them would silently disarm one."""
    resolved = resolve_checks([_row(path=("tasks",)), _row(path=("hyp",))], [skill])

    assert {c.path for c in resolved} == {("tasks",), ("hyp",)}


def test_an_app_nobody_collects_warns_rather_than_installing_silence(skill, capsys):
    """R9: a block no integration collects is a gate that can never fire, which
    is precisely the silence this channel exists to remove."""
    resolve_checks([_row(app="rak")], [skill])

    err = capsys.readouterr().err
    assert "apps.rak" in err
    assert "will never fire" in err
    assert "trait-x" in err


def test_namespaced_skill_name_matches_either_spelling(tmp_path):
    """``dev::python`` and ``dev/python`` are one skill — the row normalises
    through the same ``resolve_namespace`` the composer uses."""
    skill_dir = tmp_path / "dev" / "python"
    skill_dir.mkdir(parents=True)
    script = skill_dir / "gate.sh"
    script.write_text("#!/usr/bin/env bash\n")
    script.chmod(0o755)
    composed = ResolvedComponent(
        name="dev/python", component_type=ComponentKind.SKILL, source_path=skill_dir
    )

    (resolved,) = resolve_checks([_row(run="dev::python/gate.sh")], [composed])

    assert resolved.script_path == script.resolve()
