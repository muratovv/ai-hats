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
    AI_HATS_APP,
    KNOWN_APPS,
    CheckBindingError,
    ai_hats_points,
    check_log_token,
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
        "at": ("plan->execute",),
        "on_error": "refuse",
        "cargo": {},
    }
    fields.update(overrides)
    return AppBinding(**fields)


def _wt(**overrides) -> AppBinding:
    """A row under ai-hats's own app, whose cargo IS validated here."""
    overrides.setdefault("at", ("pre-merge",))
    return _row(app="wt", path=(), **overrides)


def _startup(**overrides) -> AppBinding:
    """A row under the ``ai-hats`` app — the session-start point (HATS-1581)."""
    overrides.setdefault("at", ("startup",))
    return _row(app=AI_HATS_APP, path=(), **overrides)


def test_the_session_start_is_ai_hats_own_point():
    """HATS-1581. The session start is fired by ai-hats itself, so its app key is
    ai-hats's own — owned, rostered and point-validated exactly like ``wt``.

    ``startup`` permits ``on_error: warn``: unlike the wt teardown points, no data
    is protected here, so whether a stale gate blocks the launch is the declaring
    role's call (``role-curator`` warns where ``maintainer`` refuses).
    """
    assert owns_app(AI_HATS_APP)
    assert AI_HATS_APP in KNOWN_APPS
    assert ai_hats_points()["startup"] is True


def test_ai_hats_owns_one_app_and_judges_only_its_cargo():
    """HATS-1545: the app is a KEY now, so routing needs no namespace table.

    ``rack`` is in the roster (something collects it) but is not owned — what a
    rack row means is the rack's question, and ai-hats validates none of it.
    """
    assert owns_app("wt")
    assert not owns_app("rack")
    assert {"rack", "wt"} <= KNOWN_APPS
    assert wt_points() == {"pre-merge": False}


def test_a_foreign_apps_cargo_is_carried_not_judged(skill):
    """D11: what a rack row's cargo means is the rack's question. Carried
    verbatim, with provenance intact and no grammar check of any kind."""
    (resolved,) = resolve_checks([_row(at=("bogus->state",), cargo={"weird": 7})], [skill])

    assert resolved.cargo == {"weird": 7}
    assert resolved.at == ("bogus->state",)
    assert resolved.declared_by == "trait-x"
    assert resolved.app == "rack"
    assert resolved.path == ("tasks",)


def test_a_run_naming_no_script_is_loud(skill):
    """The one shape ai-hats keeps: ``run`` is ``<skill>/<script>``, so a bare
    name reaches no script and is a typo by construction."""
    with pytest.raises(CheckBindingError, match="names no script"):
        resolve_checks([_row(run="gate.sh")], [skill])


def test_warn_is_rejected_at_the_wt_data_protection_point(skill):
    """ADR-0019 D4: ADR-0012 deliberately put the failure-policy lever for
    worktree data protection in the engine; moving it into user YAML would let a
    component opt out of it. ``pre-merge`` is the whole catalog since HATS-1577,
    so this is the only wt name the lever applies to; the legal-``warn`` half of
    D4 is covered on the other owned app, at ``startup``."""
    with pytest.raises(CheckBindingError, match="on_error: warn"):
        resolve_checks([_wt(at=("pre-merge",), on_error="warn")], [skill])


def test_a_wt_point_with_no_call_site_is_not_in_the_catalog(skill):
    """HATS-1577. A name ai-hats validates and then never fires is the silent
    no-op ADR-0019 exists to remove, so ``create`` and ``teardown[*]`` left the
    catalog rather than staying armable: binding to one is now refused at
    composition instead of composing clean and never running. HATS-1146 returns
    them together with the call site, not before it.
    """
    for point in ("create", "teardown[merge]", "teardown[discard]", "teardown[cleanup]"):
        with pytest.raises(CheckBindingError, match="not a point of the 'wt' app"):
            resolve_checks([_wt(at=(point,))], [skill])


def test_a_wt_point_ai_hats_does_not_fire_is_loud(skill):
    """``wt`` is ai-hats's own app, so an unknown point there is not "someone
    else's grammar" — it is a name nothing will ever fire."""
    with pytest.raises(CheckBindingError, match="not a point of the 'wt' app"):
        resolve_checks([_wt(at=("pre-merj",))], [skill])


def test_a_row_bound_to_nothing_is_loud_for_EVERY_app(skill, tmp_path):
    """HATS-1545 F3. `at:` is owned by ai-hats precisely so this is loud without
    knowing any app's grammar: a row that names no point is a gate that never
    fires, which is the silent absence the epic exists to remove. Asserted on
    `rack` — a FOREIGN app — because the `wt` case would pass even if the check
    lived in the wt-only branch, which is where the hole was."""
    from ai_hats.models import parse_app_bindings

    for cargo in ({}, {"ats": ["plan->execute"]}):
        with pytest.raises(CheckBindingError, match="at least one point"):
            parse_app_bindings(
                {"rack": {"tasks": [{"run": "gate-skill/hooks/gate.sh", **cargo}]}},
                declared_by="trait-x",
            )


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
    """R6: identity spans `at` and cargo whole, so one script at two different
    points is two rows."""
    other = skill.source_path / "hooks" / "other.sh"
    other.write_text("#!/usr/bin/env bash\n")
    other.chmod(0o755)

    resolved = resolve_checks(
        [
            _row(at=("plan->execute",)),
            _row(at=("execute->review",)),
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


def test_two_surviving_rows_never_share_a_log_name(skill):
    """HATS-1545 F5 / HATS-1137 / HATS-1540 — the same defect, a third time.

    The log token must not be COARSER than the identity `resolve_checks` keys on.
    Two rows differing only in `at:` both survive dedup, both fire on the shared
    edge, and a token built from (app, path, skill, script) alone gave them ONE
    file — the second run truncating the first one's transcript while the first
    one's reason still pointed at it.
    """
    rows = resolve_checks(
        [
            _row(at=("a->b",)),
            _row(at=("a->b", "c->d"), declared_by="role-y"),
        ],
        [skill],
    )

    assert len(rows) == 2, "different cargo means different rows — that half already held"
    tokens = [check_log_token(row) for row in rows]
    assert len(set(tokens)) == 2, f"two rows, one log file: {tokens}"


def test_the_log_name_stays_readable_and_still_discriminates(skill):
    """The readable components survive; the digest rides along as a suffix so the
    token is never coarser than the identity, in EVERY case rather than in the
    ones a condition happened to foresee."""
    (row,) = resolve_checks([_row()], [skill])

    token = check_log_token(row)
    assert token.startswith("rack~tasks~gate-skill~hooks+gate.sh~")
    assert len(token.rsplit("~", 1)[1]) == 8


def test_the_app_roster_matches_the_integrations_that_claim_the_keys():
    """HATS-1545 F11. `KNOWN_APPS` exists only to NAME a block nobody collects,
    so it has to agree with the integrations that actually collect one. Spelled
    in two places (here and at each integration's call site); rename one and
    `_warn_unclaimed_apps` goes quiet while every row of that app goes
    uncollected — the silence R9 exists to prevent, inside R9's own guard.

    HATS-1581 gave ai-hats a SECOND app of its own, so the roster is stated as
    ``{rack's key} | {apps ai-hats owns}`` rather than a hand-listed pair — which
    keeps the teeth: an entry neither side answers for still fails.

    HATS-1735 added a THIRD kind: a key ai-hats neither fires nor runs, whose
    rows are READ as policy by the consent seam. Collected, not owned — the same
    standing the rack's key has — so it is named from its own integration.
    """
    from ai_hats.check_points import WT_APP
    from ai_hats.consent_port import APP as CONSENT_GATE
    from ai_hats.rack_consumers import AiHatsCheckPort

    owned = {WT_APP, AI_HATS_APP}
    collected = {AiHatsCheckPort.APP, CONSENT_GATE}

    assert all(owns_app(app) for app in owned), "each of ai-hats's own apps must be owned"
    assert not any(owns_app(app) for app in collected), (
        "a collected key is read by its integration, never fired here"
    )
    assert KNOWN_APPS == collected | owned, (
        "the roster must list exactly the apps some integration claims — an extra "
        "entry silences the warning for an app nobody collects"
    )


def test_resolution_carries_the_declaring_file_onto_the_resolved_row(skill, tmp_path):
    """HATS-1753: a resolved row keeps its address, so a duplicate warning can
    name the file to edit and not just the component that declared it."""
    source = tmp_path / "trait-x" / "config.yaml"

    (resolved,) = resolve_checks([_row(declared_in=source)], [skill])

    assert resolved.declared_in == source


def test_a_sink_takes_the_diagnostic_instead_of_stderr(skill, capsys, tmp_path):
    """HATS-1753: when a caller collects, nothing is printed — and the collected
    value carries the level and the file, which a bare stderr line could not."""
    from ai_hats.diagnostics import Level

    source = tmp_path / "trait-x" / "config.yaml"
    sink: list = []

    resolve_checks([_row(app="rak", declared_in=source)], [skill], diagnostics=sink)

    assert capsys.readouterr().err == "", "a collected diagnostic must not also print"
    (diag,) = sink
    assert diag.level is Level.WARN
    assert diag.where == source
    assert "apps.rak" in diag.text
