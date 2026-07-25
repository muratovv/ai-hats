"""A stand-in for `ai-hats` that speaks the FD seam (HATS-1193 S2 tests).

Deterministic and free: the real child would spawn a provider CLI and spend quota.
It reports what it observed about its execution environment so the tests can assert
the broker set it up correctly, then echoes input back so both directions are covered.

Protocol: `u8 type | u32 big-endian length | payload`, type 0 raw, type 1 JSON control.
"""

from __future__ import annotations

import json
import os
import sys


def frame(ftype: int, payload: bytes) -> bytes:
    return bytes([ftype]) + len(payload).to_bytes(4, "big") + payload


def main() -> int:
    in_fd = int(os.environ["AI_HATS_PTY_IN_FD"])
    out_fd = int(os.environ["AI_HATS_PTY_OUT_FD"])

    try:
        size = os.get_terminal_size()
        term = f"{size.columns}x{size.lines}"
    except OSError as exc:
        term = f"no-tty:{exc.errno}"

    try:
        fd = os.open(os.ctermid(), os.O_RDWR)
        os.close(fd)
        ctty = "yes"
    except OSError as exc:
        ctty = f"no:{exc.errno}"

    hello = json.dumps(
        {
            "hello": True,
            "pid": os.getpid(),
            "pgid": os.getpgid(0),
            "term_size": term,
            "ctty": ctty,
            "term": os.environ.get("TERM", ""),
            "argv": sys.argv[1:],
        }
    )
    os.write(out_fd, frame(0, hello.encode("utf-8")))

    buf = bytearray()
    while True:
        try:
            chunk = os.read(in_fd, 65536)
        except OSError:
            break
        if not chunk:
            break
        buf.extend(chunk)
        while len(buf) >= 5:
            ftype = buf[0]
            length = int.from_bytes(buf[1:5], "big")
            if len(buf) < 5 + length:
                break
            payload = bytes(buf[5 : 5 + length])
            del buf[: 5 + length]
            if ftype == 1:
                os.write(out_fd, frame(0, b"CTRL:" + payload))
                continue
            if payload == b"quit\n":
                return 0
            os.write(out_fd, frame(0, payload.upper()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
