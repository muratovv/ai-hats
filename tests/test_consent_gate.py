"""HATS-1735 — the grant: one answer covering a series of moves for a named window.

Behaviour, never a constant asserted against itself: the window is 120 minutes
because a grant issued with no number expires two hours out, and the outcome set
is closed because "I could not look" comes back as its own member rather than as
a refusal.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from ai_hats_library.hooks.consent_gate import (
    DEFAULT_WINDOW_MINUTES,
    JOURNAL_FILENAME,
    IssueError,
    Operation,
    Outcome,
    Radius,
    check,
    grants_dir,
    issue,
    mode_refusal,
    record,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
#: The shipped engine, named by path so a move fails here and not in a hook.
ENGINE = REPO_ROOT / "packages/ai-hats-library/src/ai_hats_library/hooks/consent_gate"

POLICY = ("rack.transition", "wt.merge")
NOW = 1_000_000.0
MOVE = Operation("rack.transition", subject="HATS-1735", params={"to": "execute"})


def test_the_shipped_engine_is_where_the_hook_expects_it():
    assert (ENGINE / "check.py").is_file(), f"the checking half moved: {ENGINE}"
    assert (ENGINE / "issue.py").is_file(), f"the issuing half moved: {ENGINE}"


@pytest.fixture
def store(tmp_path: Path) -> Path:
    return tmp_path / "cache" / "sessions" / "sid-1" / "consent"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    anchor = tmp_path / "proj"
    anchor.mkdir()
    return anchor


def _issue(store: Path, project: Path, *, types=("rack.transition",), **kw):
    return issue(
        Radius(types=types),
        store_root=store,
        session_id=kw.pop("session_id", "sid-1"),
        project_dir=kw.pop("project_dir", project),
        now=kw.pop("now", NOW),
        **kw,
    )


def _check(store: Path, project: Path, *, op=MOVE, now=NOW, **kw):
    return check(
        op,
        session_id=kw.pop("session_id", "sid-1"),
        store_root=kw.pop("store_root", store),
        project_dir=kw.pop("project_dir", project),
        policy=kw.pop("policy", POLICY),
        now=now,
    )


# --- the four outcomes, each reachable and each distinct -----------------------


def test_a_live_grant_covers_a_move_of_the_same_type(store, project):
    _issue(store, project)
    assert _check(store, project).outcome is Outcome.GRANTED


def test_a_second_different_move_inside_the_window_is_covered_too(store, project):
    """The whole point: one answer, a SERIES of moves."""
    _issue(store, project)
    later = Operation("rack.transition", subject="HATS-9999", params={"to": "done"})
    assert _check(store, project, op=later, now=NOW + 600).outcome is Outcome.GRANTED


def test_an_expired_window_stops_covering(store, project):
    _issue(store, project, minutes=30)
    assert _check(store, project, now=NOW + 31 * 60).outcome is Outcome.DENIED


def test_no_envelope_is_not_a_refusal(store, project):
    """`NO_AGENT` must never collapse into `DENIED` (ADR-0029 D6)."""
    assert _check(store, project, session_id="").outcome is Outcome.NO_AGENT
    assert _check(store, project, store_root=None).outcome is Outcome.NO_AGENT
    assert _check(store, project, project_dir=None).outcome is Outcome.NO_AGENT


def test_a_missing_store_is_no_grant_not_an_error(store, project):
    """P5: an unreadable store reads as absence, or the fallback never runs."""
    assert _check(store, project).outcome is Outcome.DENIED


# --- the three axes the grant is bound on -------------------------------------


def test_another_sessions_grant_is_invisible(store, project):
    """Restart IS the revocation (ADR-0029 D9) — held by the binding, not by luck."""
    _issue(store, project, session_id="sid-OTHER")
    assert _check(store, project).outcome is Outcome.DENIED


def test_a_grant_does_not_reach_another_project(store, project, tmp_path):
    elsewhere = tmp_path / "other-repo"
    elsewhere.mkdir()
    _issue(store, project)
    assert _check(store, project, project_dir=elsewhere).outcome is Outcome.DENIED


def test_a_radius_naming_another_type_does_not_cover_this_one(store, project):
    _issue(store, project, types=("wt.merge",))
    verdict = _check(store, project)
    assert verdict.outcome is Outcome.DENIED
    assert "wt.merge" in verdict.reason, "the refusal must say a grant exists, but not this one"


def test_the_grant_covers_every_subject_of_a_declared_type(store, project):
    """No subject axis (HATS-1735): narrowing to one card would read the id from
    a command line the guard sees before the shell expands it."""
    _issue(store, project)
    other = Operation("rack.transition", subject="HATS-0001")
    assert _check(store, project).outcome is Outcome.GRANTED
    assert _check(store, project, op=other).outcome is Outcome.GRANTED


# --- policy: the engine never learns what rack is ------------------------------


def test_an_undeclared_type_is_refused_and_the_declared_set_is_named(store, project):
    _issue(store, project, types=("*",))
    verdict = _check(store, project, policy=("wt.merge",))
    assert verdict.outcome is Outcome.DENIED
    assert "wt.merge" in verdict.reason


# --- the collapse D6 forbids ---------------------------------------------------


def test_a_verdict_cannot_be_truth_tested(store, project):
    verdict = _check(store, project)
    with pytest.raises(TypeError, match="four outcomes"):
        bool(verdict)


# --- issuing -------------------------------------------------------------------


def test_a_grant_issued_with_no_number_lives_the_default_window(store, project):
    grant = _issue(store, project)
    assert grant.expires_at - grant.issued_at == DEFAULT_WINDOW_MINUTES * 60


def test_a_grant_is_written_unreadable_to_anyone_else(store, project):
    grant = _issue(store, project)
    mode = stat.S_IMODE(grant.path.stat().st_mode)
    assert mode == 0o600, f"grant is readable beyond its owner: {oct(mode)}"
    assert stat.S_IMODE(grants_dir(store).stat().st_mode) == 0o700


def test_an_empty_radius_is_refused_rather_than_written(store, project):
    with pytest.raises(IssueError):
        _issue(store, project, types=())
    assert not grants_dir(store).exists()


def test_a_window_already_over_is_refused(store, project):
    with pytest.raises(IssueError):
        _issue(store, project, minutes=0)


def test_the_grant_names_the_project_it_was_issued_for(store, project):
    grant = _issue(store, project)
    written = json.loads(grant.path.read_text(encoding="utf-8"))
    assert written["project_dir"] == str(project)
    assert written["session_id"] == "sid-1"


def test_two_grants_never_collide(store, project):
    first = _issue(store, project)
    second = _issue(store, project, types=("wt.merge",))
    assert first.path != second.path
    assert len(list(grants_dir(store).iterdir())) == 2


def test_a_corrupt_grant_file_is_ignored_not_fatal(store, project):
    _issue(store, project)
    (grants_dir(store) / "junk.json").write_text("{not json", encoding="utf-8")
    assert _check(store, project).outcome is Outcome.GRANTED


def test_a_grant_from_another_format_version_is_ignored(store, project):
    grant = _issue(store, project)
    data = json.loads(grant.path.read_text(encoding="utf-8"))
    data["v"] = 999
    grant.path.write_text(json.dumps(data), encoding="utf-8")
    assert _check(store, project).outcome is Outcome.DENIED


# --- the verb's grammar --------------------------------------------------------


def test_the_grammar_reads_the_trailing_number_as_minutes():
    from ai_hats_library.hooks.consent_gate.cli import parse

    assert parse([]) == ("all", DEFAULT_WINDOW_MINUTES)
    assert parse(["30"]) == ("all", 30)
    assert parse(["rack.transition"]) == ("rack.transition", DEFAULT_WINDOW_MINUTES)
    assert parse(["rack.transition", "30"]) == ("rack.transition", 30)
    assert parse(["wt.merge", "5"]) == ("wt.merge", 5)


def test_the_word_is_either_all_or_one_declared_type():
    from ai_hats_library.hooks.consent_gate.cli import radius_for

    declared = ("rack.transition", "wt.merge")

    assert radius_for("all", declared) == Radius(types=declared)
    assert radius_for("rack.transition", declared) == Radius(types=("rack.transition",))


def test_a_card_id_is_refused_rather_than_read_as_a_subject():
    """Measured (HATS-1735): a loop over `$id` hands the guard the literal token,
    so a card-shaped radius could never be trusted. It is refused loudly instead."""
    from ai_hats_library.hooks.consent_gate.cli import radius_for

    with pytest.raises(IssueError, match="not a declared operation type"):
        radius_for("HATS-1734", ("rack.transition",))


def test_an_undeclared_type_is_refused_rather_than_written_as_a_dead_grant():
    from ai_hats_library.hooks.consent_gate.cli import radius_for

    with pytest.raises(IssueError, match="not a declared operation type"):
        radius_for("cron.run", ("rack.transition",))


def test_a_role_declaring_nothing_gets_a_refusal_that_names_the_block():
    from ai_hats_library.hooks.consent_gate.cli import radius_for

    with pytest.raises(IssueError, match="apps.consent_gate"):
        radius_for("all", ())


def test_too_many_words_is_a_refusal_not_a_guess():
    from ai_hats_library.hooks.consent_gate.cli import parse

    with pytest.raises(IssueError):
        parse(["rack.transition", "HATS-1", "30"])


def test_the_verb_never_prints_the_grant_id(store, project):
    """The output lands in the model's context; the key must not (ADR-0029 §1.4)."""
    from ai_hats_library.hooks.consent_gate.cli import describe

    grant = _issue(store, project)

    printed = describe(Radius(types=("rack.transition",)), grant.expires_at)
    assert grant.id not in printed
    assert "rack.transition" in printed


def test_outside_a_session_the_verb_says_so_with_its_own_code(monkeypatch):
    from ai_hats_library.hooks.consent_gate.cli import EXIT_NO_SESSION, main

    monkeypatch.delenv("AI_HATS_SESSION_IDENTITY", raising=False)

    assert main([]) == EXIT_NO_SESSION


# --- HATS-1736 S2: bad permissions are a REFUSAL, never a warning --------------
#
# Prior art rule 4: `ssh-add` ignores identity files others can read, and sudoers
# refuses a group-writable timestamp dir. Until now `0600` was set on write and
# never checked on read, so a grant planted by anyone could be spent by us.


@pytest.mark.parametrize(
    "mode, who",
    [
        pytest.param(0o640, "the group", id="group-readable"),
        pytest.param(0o604, "everyone", id="world-readable"),
        pytest.param(0o660, "the group", id="group-writable"),
    ],
)
def test_a_grant_others_can_read_is_ignored_and_the_reason_is_named(store, project, mode, who):
    grant = _issue(store, project)
    grant.path.chmod(mode)

    verdict = _check(store, project)

    assert verdict.outcome is Outcome.DENIED, f"a loose grant was honoured: {verdict}"
    assert "permission" in verdict.reason.lower(), (
        f"the refusal did not say the grant was ignored for its mode: {verdict.reason!r}"
    )
    assert oct(mode)[-3:] in verdict.reason, (
        f"the refusal did not name the offending mode: {verdict.reason!r}"
    )


def test_a_store_directory_others_can_reach_disarms_every_grant_in_it(store, project):
    _issue(store, project)
    grants_dir(store).chmod(0o750)

    verdict = _check(store, project)

    assert verdict.outcome is Outcome.DENIED, f"a loose store was honoured: {verdict}"
    assert "permission" in verdict.reason.lower(), verdict.reason


def test_a_private_grant_is_still_honoured(store, project):
    """The guard rail must not fire on the mode the verb actually writes."""
    grant = _issue(store, project)

    assert stat.S_IMODE(grant.path.stat().st_mode) == 0o600
    assert _check(store, project).outcome is Outcome.GRANTED


def test_issuing_into_a_store_others_can_reach_is_refused(store, project):
    _issue(store, project)  # creates the tree
    grants_dir(store).chmod(0o750)

    with pytest.raises(IssueError) as exc:
        _issue(store, project)

    assert "permission" in str(exc.value).lower(), str(exc.value)


# --- HATS-1736 S3/S4: the record, and who is allowed to write it --------------


def test_the_engine_writes_its_own_record_when_no_host_supplies_a_sink(store, project):
    """HATS-1740 leans on this: shipped alone, the engine still journals."""
    _issue(store, project)
    granted = _check(store, project)

    assert record(granted, MOVE, store_root=store, now=NOW) is True

    path = store / JOURNAL_FILENAME
    assert stat.S_IMODE(path.stat().st_mode) == 0o600, "the engine's own journal is not private"
    entry = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert entry["op"] == "rack.transition"
    assert entry["radius"] == ["rack.transition"], entry
    assert entry["outcome"] == "granted"
    assert entry["grant_id"] == granted.grant_id


def test_the_record_carries_the_window_it_was_spent_against(store, project):
    _issue(store, project, minutes=30)
    granted = _check(store, project, now=NOW + 600)

    written: list[dict] = []
    record(granted, MOVE, journal=lambda e: written.append(dict(e)) or True, now=NOW + 600)

    assert written[0]["window_s"] == 30 * 60, written
    assert written[0]["left_s"] == 20 * 60, written


def test_a_refusal_is_not_a_use_and_writes_nothing(store, project):
    """P6: only a grant that ANSWERED is a use; a refusal sends the caller
    to another channel, which writes its own record."""
    refused = _check(store, project)
    assert refused.outcome is Outcome.DENIED

    written: list[dict] = []

    assert record(refused, MOVE, journal=lambda e: written.append(dict(e)) or True) is False
    assert written == [], f"a refusal was journalled as a use: {written}"


def test_a_check_outside_the_declared_policy_writes_nothing(store, project):
    """Otherwise a PreToolUse on every Bash makes the journal unreadable."""
    _issue(store, project)
    undeclared = Operation("fs.rm")
    refused = _check(store, project, op=undeclared)
    assert refused.outcome is Outcome.DENIED

    written: list[dict] = []

    assert record(refused, undeclared, journal=lambda e: written.append(dict(e)) or True) is False
    assert written == []


def test_a_use_with_nowhere_to_record_says_so_rather_than_going_quiet(store, project, capsys):
    _issue(store, project)
    granted = _check(store, project)

    assert record(granted, MOVE) is False
    assert "NOT RECORDED" in capsys.readouterr().err


def test_a_symlink_in_the_store_is_not_a_grant(store, project):
    """`stat` would have judged the mode of whatever the link points AT, so a
    link to a file we never inspected could carry a grant."""
    real = _issue(store, project)
    planted = grants_dir(store) / "planted.json"
    planted.symlink_to(real.path)
    real.path.unlink()

    verdict = _check(store, project)

    assert verdict.outcome is Outcome.DENIED, f"a symlinked grant was honoured: {verdict}"
    assert "symlink" in verdict.reason, verdict.reason


def test_a_grant_that_vanished_mid_scan_is_reported_not_swallowed(store, project, tmp_path):
    """`iterdir` then `lstat` is two syscalls; the file can go between them."""
    gone = tmp_path / "never-existed.json"

    assert "permissions unreadable" in mode_refusal(gone)


def test_a_journal_that_cannot_be_written_says_so_rather_than_going_quiet(store, project, capsys):
    """dev_rule_silent_fallback: the sink's own failure must reach a human."""
    _issue(store, project)
    granted = _check(store, project)
    (store / JOURNAL_FILENAME).mkdir(parents=True)  # a directory where the file goes

    assert record(granted, MOVE, store_root=store, now=NOW) is False
    assert "NOT RECORDED" in capsys.readouterr().err
