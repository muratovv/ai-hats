"""The resident dispatcher's own seams — the socket path and the stream proxy.

Both are things e2e cannot see: a path that binds anyway on a short checkout,
and a terminal that stops looking like one only in someone else's session.
"""

from __future__ import annotations

import io
import socket
from pathlib import Path

import pytest

from ai_hats.surfaces.claude.hook_server import _Fanout, socket_path


class _Terminal:
    """What sys.stdout is in a wrapped session, reduced to what gets asked."""

    encoding = "utf-8"

    def __init__(self) -> None:
        self.written = ""

    def isatty(self) -> bool:
        return True

    def fileno(self) -> int:
        return 1

    def write(self, text: str) -> int:
        self.written += text
        return len(text)

    def flush(self) -> None:
        pass


def test_the_proxy_still_looks_like_the_terminal_behind_it() -> None:
    """An `io.TextIOBase` subclass answers these itself — False, and a raise —
    so the session's own stdout would stop being a tty the moment a hook server
    started."""
    real = _Terminal()
    fanout = _Fanout(real)

    assert fanout.isatty() is True
    assert fanout.fileno() == 1
    assert fanout.encoding == "utf-8"


def test_a_claimed_buffer_takes_this_threads_writes_and_nothing_else() -> None:
    real = _Terminal()
    fanout = _Fanout(real)
    buf = io.StringIO()

    fanout.write("before")
    fanout.claim(buf)
    fanout.write("during")
    fanout.release()
    fanout.write("after")

    assert buf.getvalue() == "during"
    assert real.written == "beforeafter"


@pytest.mark.parametrize("cache_len", [87, 106])  # a plain checkout, and a worktree
def test_the_socket_path_fits_what_bind_accepts(cache_len: int) -> None:
    """Measured session cache paths run to 106 bytes against AF_UNIX's 104, so
    the socket cannot live beside the manifest. Asserted against the kernel's
    own limit rather than a number copied into a test.
    """
    cache = Path("/" + "c" * (cache_len - 1))
    path = socket_path(cache)

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(str(path))
        except OSError as exc:  # the failure this path shape exists to avoid
            pytest.fail(f"bind refused {len(str(path))} bytes: {exc}")
        finally:
            path.unlink(missing_ok=True)  # safe-delete: ok ephemeral socket
