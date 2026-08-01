"""Check-binding catalog and validation (HATS-1140, ADR-0019 D3/D4/D6).

Every case here is a way a declared gate can fail to install. The contract is
that each one is LOUD at composition — ``CheckBindingError``, not an entry in
``CompositionResult.errors``, which ``composition_seam`` tolerates silently on
the implicit-role path.
"""

from __future__ import annotations

import pytest
from ai_hats_core import ComponentKind, ResolvedComponent

from ai_hats.check_points import CheckBindingError, known_points, resolve_checks
from ai_hats.models import CheckBinding


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


def _row(**overrides) -> CheckBinding:
    return CheckBinding.model_validate(
        {
            "skill": "gate-skill",
            "script": "hooks/gate.sh",
            "on": ("edge:plan--execute",),
            **overrides,
        }
    )


def test_catalog_carries_every_namespace_of_d3():
    """R7: the full catalog ships now, including points whose executors land in
    HATS-1141 / HATS-1143 — D4's warn-rejection is composition-time validation,
    so the attributes must exist before the runners do."""
    catalog = known_points()

    assert catalog["edge:plan--execute"].namespace == "edge"
    assert catalog["edge:execute--execute"].namespace == "edge"  # reclaim self-loop
    assert catalog["card:pre-create"].namespace == "card"
    assert catalog["wt:pre-merge"].allow_warn is False
    assert catalog["wt:teardown[merge]"].allow_warn is False
    assert catalog["wt:create"].allow_warn is True


def test_unknown_point_is_loud(skill):
    with pytest.raises(CheckBindingError, match="unknown point"):
        resolve_checks([("trait-x", _row(on=("edge:bogus--state",)))], [skill])


def test_unknown_namespace_is_loud(skill):
    with pytest.raises(CheckBindingError, match="unknown point"):
        resolve_checks([("trait-x", _row(on=("gh:pre-push",)))], [skill])


def test_warn_is_rejected_at_data_protection_points(skill):
    """ADR-0019 D4: ADR-0012 deliberately put the failure-policy lever for
    worktree data protection in the engine; moving it into user YAML would let a
    component opt out of the harvest."""
    for point in ("wt:pre-merge", "wt:teardown[merge]"):
        with pytest.raises(CheckBindingError, match="on_error: warn"):
            resolve_checks([("trait-x", _row(on=(point,), on_error="warn"))], [skill])


def test_warn_is_allowed_at_policy_points(skill):
    resolved = resolve_checks(
        [("trait-x", _row(on=("card:pre-create", "wt:create"), on_error="warn"))], [skill]
    )

    assert [c.point for c in resolved] == ["card:pre-create", "wt:create"]
    assert {c.on_error for c in resolved} == {"warn"}


def test_refuse_is_accepted_at_data_protection_points(skill):
    (resolved,) = resolve_checks([("trait-x", _row(on=("wt:pre-merge",)))], [skill])

    assert resolved.on_error == "refuse"


def test_binding_to_uncomposed_skill_is_loud(skill):
    """ADR-0019 D2: a binding does NOT implicitly pull the skill in — implicit
    composition is how you get a surprise gate. So an unknown name is a typo."""
    with pytest.raises(CheckBindingError, match="composes no skill"):
        resolve_checks([("trait-x", _row(skill="ghost-skill"))], [skill])


def test_binding_to_overlay_removed_skill_warns_and_drops(skill, capsys):
    """ADR-0019 D6 rev 7 / D-e: hard-failing would force a user making a
    legitimate --remove-skill to author a new role; silence would drop a gate
    they expected. So: warn, name the binding site, drop, continue."""
    resolved = resolve_checks(
        [("trait-x", _row(skill="removed-skill"))],
        [skill],
        removed_skills={"removed-skill"},
    )

    assert resolved == ()
    err = capsys.readouterr().err
    assert "removed-skill" in err
    assert "trait-x" in err


def test_script_escaping_the_skill_dir_is_loud(skill, tmp_path):
    """ADR-0019 D6. The live hole this closes: ``collect_lifecycle_hooks`` joins
    ``skill_dir / script`` unresolved, so ``../../../x.sh`` escapes, is read, and
    is materialized 0755 — with no test anywhere in the repo. Under D2 the
    binding is authored in a TRAIT that need not own the skill, which is worse.
    """
    outside = tmp_path / "outside.sh"
    outside.write_text("#!/usr/bin/env bash\n")
    outside.chmod(0o755)

    with pytest.raises(CheckBindingError, match="outside the skill"):
        resolve_checks([("trait-x", _row(script="../../outside.sh"))], [skill])


def test_absolute_script_is_loud(skill):
    """The same escape with a shorter payload: ``Path('/a') / '/etc/passwd'`` is
    ``/etc/passwd``, so the join alone never leaves the skill dir."""
    with pytest.raises(CheckBindingError, match="outside the skill"):
        resolve_checks([("trait-x", _row(script="/etc/passwd"))], [skill])


def test_missing_script_is_loud(skill):
    with pytest.raises(CheckBindingError, match="not found"):
        resolve_checks([("trait-x", _row(script="hooks/absent.sh"))], [skill])


def test_empty_script_is_loud(skill):
    (skill.source_path / "hooks" / "empty.sh").write_text("   \n")
    (skill.source_path / "hooks" / "empty.sh").chmod(0o755)

    with pytest.raises(CheckBindingError, match="empty"):
        resolve_checks([("trait-x", _row(script="hooks/empty.sh"))], [skill])


def test_script_without_shebang_is_loud(skill):
    script = skill.source_path / "hooks" / "noshebang.sh"
    script.write_text("echo hi\n")
    script.chmod(0o755)

    with pytest.raises(CheckBindingError, match="shebang"):
        resolve_checks([("trait-x", _row(script="hooks/noshebang.sh"))], [skill])


def test_non_executable_script_is_loud(skill):
    """ADR-0019 D6 rev 7: the session snapshot copies through ``copytree``, which
    preserves mode and never chmods — so a 644 script is dead on arrival at every
    point it is bound to. Checking it beside the shebang turns a runtime *broke*
    into an authoring-time error."""
    script = skill.source_path / "hooks" / "notexec.sh"
    script.write_text("#!/usr/bin/env bash\n")
    script.chmod(0o644)

    with pytest.raises(CheckBindingError, match="not executable"):
        resolve_checks([("trait-x", _row(script="hooks/notexec.sh"))], [skill])


def test_duplicate_binding_collapses_with_strictest_on_error(skill):
    """R5 / ADR-0019: dedup by (skill, script, point), strictest ``on_error``
    winning — the ``collect_plan_sections`` OR-on-required precedent. A trait
    that relaxes a gate the role hardened must not win by declaration order."""
    trait_first = resolve_checks(
        [
            ("trait-x", _row(on_error="warn")),
            ("role-y", _row(on_error="refuse")),
        ],
        [skill],
    )
    role_first = resolve_checks(
        [
            ("role-y", _row(on_error="refuse")),
            ("trait-x", _row(on_error="warn")),
        ],
        [skill],
    )

    assert [(c.point, c.on_error) for c in trait_first] == [("edge:plan--execute", "refuse")]
    assert [(c.point, c.on_error) for c in role_first] == [("edge:plan--execute", "refuse")]


def test_dedup_keeps_distinct_points_and_scripts(skill):
    """Only an exact (skill, script, point) triple collapses — the same script on
    two points stays two bindings."""
    other = skill.source_path / "hooks" / "other.sh"
    other.write_text("#!/usr/bin/env bash\n")
    other.chmod(0o755)

    resolved = resolve_checks(
        [
            ("trait-x", _row(on=("edge:plan--execute", "edge:execute--review"))),
            ("trait-x", _row(script="hooks/other.sh")),
        ],
        [skill],
    )

    assert len(resolved) == 3


def test_namespaced_skill_name_matches_either_spelling(tmp_path):
    """``dev::python`` and ``dev/python`` are one skill — the binding normalises
    through the same ``resolve_namespace`` the composer uses."""
    skill_dir = tmp_path / "dev" / "python"
    skill_dir.mkdir(parents=True)
    script = skill_dir / "gate.sh"
    script.write_text("#!/usr/bin/env bash\n")
    script.chmod(0o755)
    composed = ResolvedComponent(
        name="dev/python", component_type=ComponentKind.SKILL, source_path=skill_dir
    )

    (resolved,) = resolve_checks(
        [("trait-x", _row(skill="dev::python", script="gate.sh"))], [composed]
    )

    assert resolved.script_path == script.resolve()
