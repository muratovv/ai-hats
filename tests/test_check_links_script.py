"""The claim-evidence-check link checker against a real local HTTP server.

Exit 1 is reserved for a dead link (404/410). Nothing else fails the run — not
an unreachable host, not a bot filter's 403, not a malformed URL — and none of
them may stop the links that follow from being checked.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

_LIB = (
    Path(__file__).resolve().parents[1] / "packages" / "ai-hats-library" / "src" / "ai_hats_library"
)
SCRIPT = _LIB / "usage" / "skills" / "claim-evidence-check" / "scripts" / "check_links.py"


class _Handler(BaseHTTPRequestHandler):
    """Routes by path so one server covers every verdict the script can return."""

    def _status(self) -> int:
        if self.path == "/ok":
            return 200
        if self.path == "/botfilter":
            return 403
        # /head-refused answers 405 to HEAD only, so GET is the retry that works.
        if self.path == "/head-refused":
            return 405 if self.command == "HEAD" else 200
        # /both-refused refuses the method whichever one is asked.
        if self.path == "/both-refused":
            return 405
        return 404

    def _answer(self) -> None:
        self.send_response(self._status())
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_GET = _answer
    do_HEAD = _answer

    def log_message(self, *_args: object) -> None:  # keep pytest output clean
        return


@pytest.fixture
def base_url() -> Iterator[str]:
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()


def _run(*args: str | Path) -> subprocess.CompletedProcess[str]:
    # argv is the interpreter and paths this test owns.
    return subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT), *(str(a) for a in args)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_dead_link_fails_and_names_the_url(tmp_path: Path, base_url: str) -> None:
    md = tmp_path / "draft.md"
    md.write_text(f"Fine: [a]({base_url}/ok). Gone: [b]({base_url}/missing).\n")

    result = _run(md)

    assert result.returncode == 1, result.stdout
    assert f"DEAD 404 {base_url}/missing" in result.stdout
    assert f"OK 200 {base_url}/ok" in result.stdout


def test_live_links_pass_with_trailing_punctuation_stripped(tmp_path: Path, base_url: str) -> None:
    md = tmp_path / "draft.md"
    md.write_text(f"See {base_url}/ok.\n")

    result = _run(md)

    assert result.returncode == 0, result.stdout
    assert f"OK 200 {base_url}/ok\n" in result.stdout
    assert "checked 1 link(s), dead 0" in result.stdout


def test_unreachable_host_is_reported_not_failed(tmp_path: Path) -> None:
    md = tmp_path / "draft.md"
    md.write_text("Offline: http://127.0.0.1:9/anything\n")

    result = _run(md)

    assert result.returncode == 0, result.stdout
    assert result.stdout.startswith("UNREACHABLE ")


def test_no_links_is_a_clean_run(tmp_path: Path) -> None:
    md = tmp_path / "draft.md"
    md.write_text("No links here.\n")

    result = _run(md)

    assert result.returncode == 0
    assert "checked 0 link(s), dead 0" in result.stdout


def test_unreadable_file_is_exit_2_not_a_traceback(tmp_path: Path) -> None:
    missing = tmp_path / "nope.md"

    result = _run(missing)

    assert result.returncode == 2, result.stderr
    assert f"cannot read {missing}" in result.stderr
    assert "Traceback" not in result.stderr


def test_no_argument_is_exit_2() -> None:
    result = _run()

    assert result.returncode == 2, result.stderr
    assert "Usage: check_links.py" in result.stderr


def test_bot_filter_403_warns_and_does_not_fail(tmp_path: Path, base_url: str) -> None:
    """The reason the script exists instead of `curl -I`: a 403 is not a dead link."""
    md = tmp_path / "draft.md"
    md.write_text(f"Guarded: {base_url}/botfilter\n")

    result = _run(md)

    assert result.returncode == 0, result.stdout
    assert f"WARN 403 {base_url}/botfilter" in result.stdout


def test_head_refused_retries_with_get(tmp_path: Path, base_url: str) -> None:
    """405 to HEAD is the server refusing the method, not the URL being gone."""
    md = tmp_path / "draft.md"
    md.write_text(f"Picky: {base_url}/head-refused\n")

    result = _run(md)

    assert result.returncode == 0, result.stdout
    assert f"OK 200 {base_url}/head-refused" in result.stdout


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param("https://[bad/x", id="unclosed-ipv6"),
        pytest.param("https://example.com:abc/", id="nonnumeric-port"),
        pytest.param("http://user@:80/x", id="empty-host"),
    ],
)
def test_a_bad_url_never_hides_the_links_after_it(tmp_path: Path, base_url: str, bad: str) -> None:
    """Two rounds fixed one raising type each while the next still aborted the run.

    Parametrized on purpose: a single form only proves the form was patched.
    """
    md = tmp_path / "draft.md"
    md.write_text(f"Bad: {bad} and good: {base_url}/ok\n")

    result = _run(md)

    assert result.returncode == 0, result.stderr
    assert "Traceback" not in result.stderr
    assert f"OK 200 {base_url}/ok" in result.stdout, "the link after the bad one was skipped"
    assert "checked 2 link(s)" in result.stdout, "the summary line never printed"


def test_a_peer_that_does_not_speak_http_is_unreachable(tmp_path: Path) -> None:
    """http.client.HTTPException descends from neither OSError nor ValueError."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]

    def serve() -> None:
        try:
            conn, _ = server.accept()
            conn.recv(4096)
            conn.sendall(b"NOT-HTTP garbage line\r\n\r\n")
            conn.close()
        except OSError:  # silent-ok: the socket is closed under us at teardown
            pass

    threading.Thread(target=serve, daemon=True).start()
    try:
        md = tmp_path / "draft.md"
        md.write_text(f"Garbage: http://127.0.0.1:{port}/x\n")

        result = _run(md)

        assert result.returncode == 0, result.stderr
        assert "Traceback" not in result.stderr
        assert "UNREACHABLE" in result.stdout
        assert "checked 1 link(s)" in result.stdout
    finally:
        server.close()


def test_both_methods_refused_is_a_warn(tmp_path: Path, base_url: str) -> None:
    """The tail of probe(): reachable only when GET is refused too."""
    md = tmp_path / "draft.md"
    md.write_text(f"Picky: {base_url}/both-refused\n")

    result = _run(md)

    assert result.returncode == 0, result.stdout
    assert "WARN HEAD and GET both refused" in result.stdout
