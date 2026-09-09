"""The launch half of the identity contract (HATS-1594).

``test_session_identity.py`` pins the envelope's own shape; this pins what the
launch PUTS in it. The pair matters because HATS-1594 moved where the surface is
asked for its skill mirror — from every gate firing to once, here. Without this
file that question would be asked by nobody and asserted by nobody.
"""

from __future__ import annotations

from ai_hats_core.layout import ProjectLayout

import json
from pathlib import Path

from ai_hats.constants import ENV_ROLE, BYPASS_FLAGS_NOT_INHERITED
from ai_hats.session_artifacts import RunMode, assemble_launch_env
from ai_hats.session_identity import ENV_SESSION_IDENTITY, SessionIdentity


class _Surface:
    """Only what ``assemble_launch_env`` touches — no registry, no real provider."""

    name = "stub"

    def __init__(self, skills_root: Path | None) -> None:
        self._skills_root = skills_root

    def session_skills_root(self, layout, session_id: str) -> Path | None:
        del layout, session_id
        return self._skills_root

    def get_env(self, session_dir: Path, layout) -> dict[str, str]:
        del session_dir, layout
        return {}

    def claim_launch_env(self, session_dir: Path, layout) -> dict[str, str]:
        del session_dir, layout
        return {}


def _env(
    tmp_path: Path,
    skills_root: Path | None,
    role: str = "judge",
    run_mode: RunMode = RunMode.HITL,
) -> dict[str, str]:
    return assemble_launch_env(
        _Surface(skills_root),
        ProjectLayout.at(tmp_path),
        tmp_path / "session",
        session_id="sess-a",
        trace_path=str(tmp_path / "trace.log"),
        role=role,
        root_pid="4242",
        extra_env={},
        run_mode=run_mode,
    )


def _envelope(env: dict[str, str]) -> dict:
    return json.loads(env[ENV_SESSION_IDENTITY])


def test_the_surface_is_asked_for_the_mirror_root_and_answered_verbatim(tmp_path: Path):
    """The other half of ``test_the_mirror_root_is_followed_verbatim_never_guessed``.

    Each surface scans a different relative path, so the launch must record what
    the surface says rather than a layout of its own.
    """
    root = tmp_path / "cache" / "sess-a" / "rules" / ".agents" / "skills"

    assert _envelope(_env(tmp_path, root))["skills_root"] == str(root)


def test_a_surface_that_mirrors_nothing_records_an_empty_root(tmp_path: Path):
    """``None`` is the ABC default; the channel turns the empty string into its
    refusal, so the launch must not drop the key or invent a path."""
    assert _envelope(_env(tmp_path, None))["skills_root"] == ""


def test_the_launch_records_the_provider_that_actually_runs(tmp_path: Path):
    """The defect: the channel re-read ``ProjectConfig.provider``, so a session
    started with ``-p`` resolved against a surface it was not running."""
    assert _envelope(_env(tmp_path, tmp_path / "m"))["provider"] == "stub"


def test_the_role_expression_reaches_the_envelope_whole(tmp_path: Path):
    """A check bound by a runtime-added trait must resolve for the gate too."""
    env = _env(tmp_path, tmp_path / "m", role="judge + audit-reviewer")

    assert _envelope(env)["role"] == "judge + audit-reviewer"


def test_the_scalars_the_launch_writes_are_the_envelope_it_writes(tmp_path: Path):
    """One writer: a child reading either surface reads the same session."""
    env = _env(tmp_path, tmp_path / "m", role="judge + audit-reviewer")
    envelope = _envelope(env)

    assert env[ENV_ROLE] == envelope["role"]
    assert SessionIdentity.from_env(env).id == envelope["id"]


def test_a_sub_agent_does_not_inherit_the_supervisors_kill_switches(tmp_path: Path) -> None:
    """HATS-1743 — an approval is scoped to the session it was given in.

    `rule_pause_before_shared_state_write` calls the export "pre-approving the whole
    session" and ADR-0023 "the shell that launched the session"; a sub-agent is a
    different session, so the launch withholds what the supervisor granted theirs.
    Blanked, not dropped: on the SDK road the child's environment is the transport's
    to build and an overlay can only overwrite a key, never remove it.
    """
    child = _env(tmp_path, None, run_mode=RunMode.AUTOMATE)

    withheld = {flag: child.get(flag) for flag in BYPASS_FLAGS_NOT_INHERITED}
    assert all(value == "" for value in withheld.values()), withheld
    # Empty rather than "0": every reader compares against a literal "1", and ""
    # additionally reads falsy, so a future truthiness check cannot resurrect it.
    assert child[ENV_ROLE], "the identity must survive the withholding"


def test_the_supervisors_own_session_is_left_alone(tmp_path: Path) -> None:
    """The other half: withholding aims at delegation, not at the human.

    An export in the launching shell is the documented channel for a surface that
    cannot ask — ADR-0023 §386-387. Blanking it in HITL would break the very road
    the docs prescribe, so the launch adds no bypass key at all here.
    """
    supervisor = _env(tmp_path, None, run_mode=RunMode.HITL)

    assert not (set(supervisor) & BYPASS_FLAGS_NOT_INHERITED)


def test_a_flag_no_roster_knows_about_is_withheld_all_the_same(tmp_path: Path, monkeypatch) -> None:
    """The point of the shape test: the seam does not depend on being kept up to date.

    A roster is fail-open — the day someone adds a gate flag and forgets this file,
    the child inherits it and nothing goes red. That already happened once while this
    card was in review (`AI_HATS_E2E_CATALOG_ACK`, read by a repo script the
    shipped-hook vocabulary never covered), which is why the line is held by shape.
    A flag from a project that merely consumes ai-hats can never be on our roster at
    all, and is withheld just the same.
    """
    stranger = "AI_HATS_SOME_FUTURE_GATE_OFF"
    assert stranger not in BYPASS_FLAGS_NOT_INHERITED, "pick a name no roster knows"
    monkeypatch.setenv(stranger, "1")

    child = _env(tmp_path, None, run_mode=RunMode.AUTOMATE)

    assert child[stranger] == "", "an undeclared gate flag rode into the sub-agent"


def test_a_knob_is_not_an_approval_and_keeps_travelling(tmp_path: Path, monkeypatch) -> None:
    """The counterweight to the shape test: withholding is aimed at "may I", not "how much".

    Blanking a knob would change the child's BEHAVIOUR rather than withhold consent —
    a tuning value the parent set is not an approval, so nothing takes it away.
    """
    monkeypatch.setenv("AI_HATS_COMMENT_MAX_LINES", "12")

    child = _env(tmp_path, None, run_mode=RunMode.AUTOMATE)

    assert "AI_HATS_COMMENT_MAX_LINES" not in child


def test_the_launch_publishes_the_session_cache_dir(tmp_path: Path):
    """HATS-1735 J1: the consent store's home, or the gate can never find it.

    Asserted against ``paths.session_cache_dir`` rather than a literal — a hook
    re-deriving the hashed path is exactly what the field exists to prevent, and
    a test spelling it out by hand would be that copy.
    """

    envelope = _envelope(_env(tmp_path, tmp_path / "m"))

    assert envelope["session_cache_dir"] == str(ProjectLayout.at(tmp_path).cache.session("sess-a"))


def test_an_envelope_written_before_the_field_existed_still_reads(tmp_path: Path):
    """An added key must not turn an older session's envelope into a refusal."""
    env = _env(tmp_path, tmp_path / "m")
    envelope = _envelope(env)
    del envelope["session_cache_dir"]
    env[ENV_SESSION_IDENTITY] = json.dumps(envelope)

    assert SessionIdentity.from_env(env).session_cache_dir == ""
