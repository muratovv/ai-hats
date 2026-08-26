"""HATS-1663: a SIGTERM'd pytest run must still unwind its session.

The harness kills a run that outlives its 10-minute ceiling. Without a handler
Python does not unwind on SIGTERM, so session-scoped teardown never runs — and
two of those teardowns are the tripwires that catch a test writing to the real
checkout. Drives a real child pytest that loads the repo-root conftest's own
``pytest_configure``, so what is under test is the shipped wiring.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

CHILD_CONFTEST = """
import importlib.util
import pathlib

import pytest

# By path, never by name: pytest imports every conftest.py as the module
# "conftest", so `import conftest` here would rebind to THIS file.
_spec = importlib.util.spec_from_file_location("repo_root_conftest", {repo_conftest!r})
repo_root_conftest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(repo_root_conftest)

pytest_configure = repo_root_conftest.pytest_configure


@pytest.fixture(scope="session", autouse=True)
def _teardown_marker(tmp_path_factory):
    tmp_path_factory.getbasetemp()  # forces the numbered dir + its .lock
    yield
    pathlib.Path({marker!r}).write_text("session teardown ran")
"""

CHILD_TEST = """
import pathlib
import time


def test_hangs_until_killed():
    pathlib.Path({started!r}).write_text("running")
    time.sleep(120)
"""


def _write_child(tmp_path: Path, marker: Path, started: Path, wire_handler: bool) -> Path:
    child = tmp_path / "child"
    child.mkdir()
    conftest = CHILD_CONFTEST.format(
        repo_conftest=str(REPO_ROOT / "conftest.py"), marker=str(marker)
    )
    if not wire_handler:
        conftest = conftest.replace("pytest_configure = repo_root_conftest.pytest_configure", "")
    (child / "conftest.py").write_text(conftest)
    (child / "test_hang.py").write_text(CHILD_TEST.format(started=str(started)))
    return child


def _run_until_started(child: Path, scratch: Path, started: Path) -> subprocess.Popen:
    env = {
        **os.environ,
        "TMPDIR": str(scratch),
        "PYTHONPATH": f"{REPO_ROOT}{os.pathsep}{REPO_ROOT / 'src'}",
    }
    # The subject is a SERIAL run killed at a ceiling. Inheriting the parent's
    # PYTEST_ADDOPTS puts the child under xdist too, and an xdist controller
    # unwinds on SIGTERM by itself — which makes the unhandled half pass its
    # teardown and destroys the premise the pair is built on (HATS-1663).
    for leaked in ("PYTEST_ADDOPTS", "PYTEST_XDIST_WORKER", "PYTEST_XDIST_WORKER_COUNT"):
        env.pop(leaked, None)
    proc = subprocess.Popen(
        [sys.executable, "-m", "pytest", str(child), "-q", "-p", "no:cacheprovider"],
        cwd=str(child),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    deadline = time.time() + 90
    while time.time() < deadline:
        if started.exists():
            return proc
        if proc.poll() is not None:
            raise AssertionError(f"child pytest died before running:\n{proc.communicate()[0]}")
        time.sleep(0.1)
    proc.kill()
    raise AssertionError(f"child pytest never reached the test:\n{proc.communicate()[0]}")


@pytest.mark.parametrize("wire_handler", [True, False], ids=["with-handler", "unhandled"])
def test_sigterm_unwinds_the_session(tmp_path: Path, wire_handler: bool) -> None:
    """With the handler: teardown runs, the lock is released, the exit stays
    non-zero. Without it: none of that happens — the pair pins the delta rather
    than asserting the fixed half alone."""
    scratch = tmp_path / "scratch-tmp"
    scratch.mkdir()
    marker = tmp_path / "teardown-marker"
    started = tmp_path / "started"
    child = _write_child(tmp_path, marker, started, wire_handler=wire_handler)

    proc = _run_until_started(child, scratch, started)
    proc.send_signal(signal.SIGTERM)
    try:
        output = proc.communicate(timeout=60)[0]
    except subprocess.TimeoutExpired:
        proc.kill()
        raise AssertionError("SIGTERM did not end the child within 60s") from None

    locks = list(scratch.rglob(".lock"))
    if not wire_handler:
        assert not marker.exists(), "unhandled SIGTERM ran session teardown — premise gone"
        assert locks, "unhandled SIGTERM released the tmp lock — premise gone"
        return

    assert proc.returncode == pytest.ExitCode.INTERRUPTED, (
        f"expected the interrupt exit code, got {proc.returncode}:\n{output}"
    )
    assert marker.read_text() == "session teardown ran", (
        f"session teardown did not run under SIGTERM:\n{output}"
    )
    assert not locks, f"tmp lock survived the SIGTERM: {locks}"
