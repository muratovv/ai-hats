"""Wire codec tests (HATS-1193 S1)."""

from __future__ import annotations

import json

import pytest

from hats_relay import wire


def test_session_frame_round_trip():
    """A RAW frame survives encode -> parse with its payload intact."""
    parser = wire.SessionFrameParser()
    frames = parser.feed(wire.session_raw(b"hello \x1b[31mworld\x1b[0m"))
    assert frames == [(wire.T_RAW, b"hello \x1b[31mworld\x1b[0m")]


def test_session_parser_reassembles_byte_at_a_time():
    """The stream is a pipe, not a message queue — arrival is arbitrarily fragmented."""
    parser = wire.SessionFrameParser()
    encoded = wire.session_raw(b"abc") + wire.session_resize(100, 30)
    seen = []
    for i in range(len(encoded)):
        seen.extend(parser.feed(encoded[i : i + 1]))
    assert seen[0] == (wire.T_RAW, b"abc")
    assert json.loads(seen[1][1]) == {"resize": {"cols": 100, "rows": 30}}


def test_session_parser_holds_a_truncated_frame():
    """A frame whose payload has not fully arrived yields nothing and is not consumed."""
    parser = wire.SessionFrameParser()
    encoded = wire.session_raw(b"payload")
    assert parser.feed(encoded[:-1]) == []
    assert parser.feed(encoded[-1:]) == [(wire.T_RAW, b"payload")]


def test_session_parser_refuses_a_length_above_the_guard():
    """A desync must be refused, never allocated — the guard is the whole point."""
    parser = wire.SessionFrameParser()
    bogus = bytes([wire.T_RAW]) + (wire.MAX_PAYLOAD + 1).to_bytes(4, "big")
    with pytest.raises(ValueError, match="exceeds guard"):
        parser.feed(bogus)


def test_client_output_carries_its_sequence_number():
    """Resume is only possible if every output frame is addressable."""
    msg = wire.client_output(seq=4207, payload=b"\x1b[2Jscreen")
    assert wire.parse_client_output(msg) == (4207, b"\x1b[2Jscreen")


def test_client_input_round_trip():
    """Keystrokes travel as binary and need no sequence of their own."""
    assert wire.parse_client_input(wire.client_input(b"\x03")) == b"\x03"


def test_client_frames_reject_a_foreign_version():
    """An old peer must fail loudly against a new one, never guess at the payload."""
    stale = bytes([wire.PROTOCOL_VERSION + 1])
    with pytest.raises(ValueError, match="version"):
        wire.parse_client_output(stale + wire.client_output(seq=1, payload=b"x")[1:])
    with pytest.raises(ValueError, match="version"):
        wire.parse_client_input(stale + wire.client_input(b"x")[1:])


def test_client_frames_reject_a_truncated_header():
    """A short frame is a protocol error, not a zero-length payload."""
    with pytest.raises(ValueError):
        wire.parse_client_output(bytes([wire.PROTOCOL_VERSION]) + b"\x00\x00")
    with pytest.raises(ValueError):
        wire.parse_client_input(b"")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
