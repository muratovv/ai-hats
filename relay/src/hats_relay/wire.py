"""Frame codecs for both sides of the broker.

Session side speaks the FD-seam format ai-hats already implements; client side rides
WebSocket message boundaries and only needs a small header. Neither side is negotiable
here — the session format belongs to ai-hats.
"""

from __future__ import annotations

import json

T_RAW = 0x00  # opaque terminal bytes, both directions
T_CTRL = 0x01  # JSON control, e.g. {"resize": {"cols": C, "rows": R}}

_HEADER = 5
MAX_PAYLOAD = 1 << 20

# Bumped on any incompatible change to the client-facing frames. Peers refuse a
# version they do not know rather than guessing at the payload behind it.
PROTOCOL_VERSION = 1

_OUT_HEADER = 9  # version + u64 seq
_IN_HEADER = 1  # version


def session_encode(ftype: int, payload: bytes) -> bytes:
    """Serialize one session-side frame."""
    return bytes([ftype]) + len(payload).to_bytes(4, "big") + payload


def session_raw(payload: bytes) -> bytes:
    """Encode terminal bytes headed for the session."""
    return session_encode(T_RAW, payload)


def session_ctrl(obj: dict) -> bytes:
    """Encode a JSON control message headed for the session."""
    return session_encode(T_CTRL, json.dumps(obj).encode("utf-8"))


def session_resize(cols: int, rows: int) -> bytes:
    """Encode the resize control the session applies to its child PTY."""
    return session_ctrl({"resize": {"cols": cols, "rows": rows}})


def _check_version(msg: bytes) -> None:
    if not msg:
        raise ValueError("empty client frame")
    if msg[0] != PROTOCOL_VERSION:
        raise ValueError(f"unsupported protocol version {msg[0]} (this peer speaks {PROTOCOL_VERSION})")


def client_output(*, seq: int, payload: bytes) -> bytes:
    """Broker -> client terminal bytes, addressable so a reconnect can resume."""
    return bytes([PROTOCOL_VERSION]) + seq.to_bytes(8, "big") + payload


def parse_client_output(msg: bytes) -> tuple[int, bytes]:
    """Inverse of :func:`client_output`; returns ``(seq, payload)``."""
    _check_version(msg)
    if len(msg) < _OUT_HEADER:
        raise ValueError(f"client output frame shorter than its {_OUT_HEADER}-byte header")
    return int.from_bytes(msg[1:_OUT_HEADER], "big"), msg[_OUT_HEADER:]


def client_input(payload: bytes) -> bytes:
    """Client -> broker keystrokes. No sequence: input is not replayed."""
    return bytes([PROTOCOL_VERSION]) + payload


def parse_client_input(msg: bytes) -> bytes:
    """Inverse of :func:`client_input`."""
    _check_version(msg)
    return msg[_IN_HEADER:]


class SessionFrameParser:
    """Incremental parser for the session side; one instance per session."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[tuple[int, bytes]]:
        """Append ``data``; return every whole frame now available.

        Raises ``ValueError`` when a declared length exceeds ``MAX_PAYLOAD`` — a
        desync must not be allowed to allocate unbounded.
        """
        self._buf.extend(data)
        out: list[tuple[int, bytes]] = []
        while len(self._buf) >= _HEADER:
            ftype = self._buf[0]
            length = int.from_bytes(self._buf[1:5], "big")
            if length > MAX_PAYLOAD:
                raise ValueError(f"frame length {length} exceeds guard {MAX_PAYLOAD}")
            if len(self._buf) < _HEADER + length:
                break
            out.append((ftype, bytes(self._buf[_HEADER : _HEADER + length])))
            del self._buf[: _HEADER + length]
        return out
