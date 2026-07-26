"""Control-message parsing and spec validation (HATS-1193 S3)."""

from __future__ import annotations

import json

import pytest

from hats_relay import protocol
from hats_relay.protocol import ProtocolError


def test_create_carries_a_spec_and_a_size():
    msg = protocol.parse_control('{"op":"create","spec":{"role":"maintainer"},"cols":100,"rows":30}')
    assert msg.op == "create"
    assert msg.spec == {"role": "maintainer"}
    assert (msg.cols, msg.rows) == (100, 30)


def test_unknown_spec_key_is_refused_not_ignored():
    """A new client against an old broker must fail loudly, never silently lose the
    field it asked for — the reason Kubernetes versions its exec subprotocols."""
    with pytest.raises(ProtocolError, match="unknown"):
        protocol.parse_control('{"op":"create","spec":{"role":"x","prompt":"y"},"cols":80,"rows":24}')


@pytest.mark.parametrize(
    "role",
    ["--dangerously-skip-permissions", "../../etc/passwd", "a b", "", "x" * 200, "role;rm", "-r"],
)
def test_role_must_be_a_plain_token(role):
    """The broker builds argv from this. It never becomes a shell string, but a value
    that looks like a flag would still be read as one by the CLI it is handed to."""
    with pytest.raises(ProtocolError, match="role"):
        protocol.parse_control(
            json.dumps({"op": "create", "spec": {"role": role}, "cols": 80, "rows": 24})
        )


def test_a_normal_role_survives_that_filter():
    """Guard the guard: a filter that rejects everything would pass the test above."""
    msg = protocol.parse_control(
        json.dumps({"op": "create", "spec": {"role": "ai-hats-maintainer"}, "cols": 80, "rows": 24})
    )
    assert msg.spec["role"] == "ai-hats-maintainer"


def test_attach_requires_a_session_id():
    with pytest.raises(ProtocolError, match="sid"):
        protocol.parse_control('{"op":"attach","cols":80,"rows":24}')


def test_unknown_op_is_refused():
    with pytest.raises(ProtocolError, match="op"):
        protocol.parse_control('{"op":"exec","cmd":"whoami"}')


def test_malformed_json_is_a_protocol_error_not_a_crash():
    with pytest.raises(ProtocolError):
        protocol.parse_control("{not json")


def test_size_must_be_sane():
    with pytest.raises(ProtocolError, match="cols|rows"):
        protocol.parse_control('{"op":"attach","sid":"a","cols":0,"rows":24}')
