"""The session-identity envelope contract (HATS-1594).

Every test here pins a clause a consumer relies on: absence means "no session"
and nothing else, presence that cannot be trusted refuses, and the scalars can
never disagree with the envelope because one serializer writes both.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_hats.constants import ENV_ROLE
from ai_hats.session_identity import (
    ENV_SESSION_IDENTITY,
    IDENTITY_VERSION,
    SessionIdentity,
    SessionIdentityError,
)
from ai_hats_observe.trace import ENV_SESSION_ID


def _identity(**overrides) -> SessionIdentity:
    base = {
        "id": "20260812-085418-1-39993",
        "role": "maintainer",
        "provider": "claude",
        "project_dir": Path("/proj"),
        "session_dir": Path("/proj/.agent/ai-hats/sessions/runs/session_x"),
        "skills_root": "/cache/sessions/x/plugin/skills",
    }
    return SessionIdentity(**{**base, **overrides})


def test_round_trips_through_the_environment():
    identity = _identity()

    assert SessionIdentity.from_env(identity.to_env()) == identity


def test_role_carries_the_whole_expression_not_the_base_name():
    """A runtime overlay declares checks too; the base name would drop them."""
    identity = _identity(role="judge + audit-reviewer")

    assert SessionIdentity.from_env(identity.to_env()).role == "judge + audit-reviewer"


def test_scalars_are_projections_of_the_envelope():
    """One writer, so a consumer reading either surface reads the same session."""
    env = _identity(role="judge + audit-reviewer").to_env()

    envelope = json.loads(env[ENV_SESSION_IDENTITY])
    assert env[ENV_SESSION_ID] == envelope["id"]
    assert env[ENV_ROLE] == envelope["role"]


def test_absent_envelope_and_no_session_is_not_a_session():
    """The live-resolution mode: composing the active role IS the right answer."""
    assert SessionIdentity.from_env({}) is None


def test_absent_envelope_inside_a_session_refuses():
    """The upgrade window: silently falling back to the config is today's bug."""
    with pytest.raises(SessionIdentityError, match="too old to say what it is"):
        SessionIdentity.from_env({ENV_SESSION_ID: "sid-from-an-older-build"})


def test_unknown_keys_are_ignored_so_a_key_can_be_added_without_a_bump():
    env = _identity().to_env()
    payload = json.loads(env[ENV_SESSION_IDENTITY])
    payload["run_mode"] = "hitl"
    env[ENV_SESSION_IDENTITY] = json.dumps(payload)

    assert SessionIdentity.from_env(env).id == "20260812-085418-1-39993"


@pytest.mark.parametrize("version", [IDENTITY_VERSION + 1, IDENTITY_VERSION - 1])
def test_version_mismatch_refuses_and_names_the_direction(version):
    env = _identity().to_env()
    payload = json.loads(env[ENV_SESSION_IDENTITY])
    payload["v"] = version
    env[ENV_SESSION_IDENTITY] = json.dumps(payload)

    with pytest.raises(SessionIdentityError, match="self update"):
        SessionIdentity.from_env(env)


def test_missing_version_refuses_rather_than_guessing():
    env = _identity().to_env()
    payload = json.loads(env[ENV_SESSION_IDENTITY])
    del payload["v"]
    env[ENV_SESSION_IDENTITY] = json.dumps(payload)

    with pytest.raises(SessionIdentityError, match="no integer 'v'"):
        SessionIdentity.from_env(env)


@pytest.mark.parametrize("key", ["id", "role", "provider", "project_dir", "session_dir"])
def test_a_missing_required_field_refuses(key):
    env = _identity().to_env()
    payload = json.loads(env[ENV_SESSION_IDENTITY])
    del payload[key]
    env[ENV_SESSION_IDENTITY] = json.dumps(payload)

    with pytest.raises(SessionIdentityError, match=key):
        SessionIdentity.from_env(env)


def test_unreadable_envelope_refuses_rather_than_reading_as_absence():
    with pytest.raises(SessionIdentityError, match="not readable JSON"):
        SessionIdentity.from_env({ENV_SESSION_IDENTITY: "{not json"})


def test_a_surface_that_mirrors_nothing_is_carried_as_empty_not_missing():
    """The consumer refuses on it; the launch is where that is decided."""
    identity = _identity(skills_root="")

    assert SessionIdentity.from_env(identity.to_env()).skills_root == ""
