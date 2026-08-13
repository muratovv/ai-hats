"""The HATS-1473 cache-home tripwire keeps its evidence, whenever the leak lands.

A leaked key is reported only when its source dir under ``basetemp`` can be
named — a bare key could be a concurrent session's and is dropped SILENTLY. Two
ways that evidence goes missing (HATS-1624), both cured by attributing before
teardown: ``tmp_path_retention_policy=failed`` deletes the source dir, and a
leak in the session's LAST test is attributed after the tripwire has read.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_CONFTEST = Path(__file__).resolve().parent.parent / "conftest.py"

_LEAK = '''
import hashlib
from pathlib import Path

CACHE = Path(r{cache!r})


def _project_key(path):
    """Verbatim ``ai_hats.paths.project_key`` — the inner run has no import path."""
    resolved = path.expanduser().resolve()
    digest = hashlib.sha256(str(resolved).encode()).hexdigest()[:8]
    slug = "".join(c if (c.isalnum() or c in "._-") else "-" for c in resolved.name)
    return f"{{slug.strip('-.') or 'project'}}-{{digest}}"


def test_leaks_a_project_key_then_passes(tmp_path):
    """Passing is the point: that is the case ``policy=failed`` reaps."""
    source = tmp_path / "leaky-project"
    source.mkdir()
    (CACHE / _project_key(source)).mkdir(parents=True)
{trailer}'''

_TRAILER = '''

def test_zz_runs_after_the_leak():
    """Keeps the leaking test off the end of the session."""
'''


def _run_inner(pytester, tmp_path, monkeypatch, *, policy: str, trailer: str):
    cache = tmp_path / "cache-home"
    cache.mkdir()
    # Read by the copied conftest at import, before its own sandbox pin lands —
    # this is what the inner run treats as the developer's real cache home.
    monkeypatch.setenv("AI_HATS_CACHE_HOME", str(cache))
    pytester.makeconftest(_CONFTEST.read_text())
    pytester.makepyfile(_LEAK.format(cache=str(cache), trailer=trailer))
    return pytester.runpytest_subprocess(
        "-p", "no:cacheprovider", "-o", f"tmp_path_retention_policy={policy}"
    )


def _assert_reported(result, context: str) -> None:
    out = result.stdout.str()
    assert result.ret != 0, f"the tripwire stayed silent on a real leak ({context})"
    assert "[cache-home]" in out, f"no tripwire report in the inner run ({context})"
    assert "leaky-project" in out, (
        f"the leak was reported without its source dir ({context}) — an "
        "unattributed key is dropped, so this is the silent-skip path"
    )


@pytest.mark.integration
@pytest.mark.parametrize("policy", ["all", "failed"])
def test_leak_is_attributed_under_any_retention_policy(pytester, tmp_path, monkeypatch, policy):
    """Attribution must not depend on whether the test's tmp_path survives."""
    result = _run_inner(pytester, tmp_path, monkeypatch, policy=policy, trailer=_TRAILER)
    _assert_reported(result, f"policy={policy}")


@pytest.mark.integration
def test_leak_in_the_final_test_is_attributed(pytester, tmp_path, monkeypatch):
    """No trailing test: the leak is last, so nothing runs after its protocol.

    Session-scoped finalizers — the tripwire among them — run inside the LAST
    test's teardown, so an attribution that waits for the protocol to end is
    written after the only reader has already gone.
    """
    result = _run_inner(pytester, tmp_path, monkeypatch, policy="all", trailer="")
    _assert_reported(result, "leak in the final test")
