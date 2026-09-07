"""The claim-evidence-check link checker against a real local HTTP server.

Exit 1 is reserved for a dead link (404/410); an unreachable host or an empty
file never fails the run — the network is not the text's fault.
"""

from __future__ import annotations

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
    def _answer(self) -> None:
        status = 200 if self.path == "/ok" else 404
        self.send_response(status)
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


def _run(md: Path) -> subprocess.CompletedProcess[str]:
    # argv is the interpreter and two paths this test owns.
    return subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT), str(md)],
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
