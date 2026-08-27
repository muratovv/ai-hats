"""The snippet the docs hand a hook author is RUN, not just printed.

HATS-1724: the design introducing this envelope shipped an example reading
``[ "$(jq -r .actor …)" = user ]``. No road ever mints ``user``, so a gate
copying it would have exited zero on every human move — the exact class the
envelope exists to close. Both branches run: with ``jq`` on PATH and with it
hidden, because the fallback is what executes on machines that have no jq.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DOC = REPO_ROOT / "docs" / "how-to-extend.md"
MARKER = "# hook-call-reader"

ENVELOPE = {
    "v": 1,
    "selector": "->execute",
    "event": "plan->execute",
    "from": "plan",
    "to": "execute",
    "task_id": "HATS-1724",
    "actor": "rack:epic-automation",
    "force": False,
    "worktree": None,
    "tasks_dir": None,
    "project_dir": "/tmp/proj",  # noqa: S108 — a literal in fixture data, nothing is opened
}


def _snippet() -> str:
    """The fenced bash block the doc marks as the reader, verbatim."""
    blocks = re.findall(r"```bash\n(.*?)```", DOC.read_text(encoding="utf-8"), re.DOTALL)
    marked = [b for b in blocks if b.lstrip().startswith(MARKER)]
    assert len(marked) == 1, f"expected one {MARKER!r} block in {DOC.name}, found {len(marked)}"
    return marked[0]


def _read_field(field: str, *, hook_env: dict[str, str]) -> str:
    script = f"{_snippet()}\nhook_call_field {field}\n"
    done = subprocess.run(  # noqa: S603,S607 — running the doc's own snippet IS the test
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=hook_env,
        timeout=30,
    )
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


@pytest.fixture
def hook_env() -> dict[str, str]:
    return {**os.environ, "AI_HATS_HOOK_CALL": json.dumps(ENVELOPE)}


def test_the_documented_reader_returns_the_field(hook_env):
    assert _read_field("actor", hook_env=hook_env) == "rack:epic-automation"
    assert _read_field("event", hook_env=hook_env) == "plan->execute"


def test_the_documented_reader_works_without_jq(hook_env, tmp_path):
    """The fallback branch, forced by handing bash a PATH that has no jq."""
    lean = tmp_path / "bin"
    lean.mkdir()
    for tool in ("bash", "python3", "env"):
        found = shutil.which(tool)
        assert found, f"{tool} is not on PATH — the probe cannot run"
        (lean / tool).symlink_to(found)

    assert _read_field("actor", hook_env={**hook_env, "PATH": str(lean)}) == "rack:epic-automation"


def test_a_null_field_reads_as_empty_not_as_the_word_null(hook_env):
    """``null`` must not reach a shell comparison as the four-letter string:
    ``[ -n "$(hook_call_field worktree)" ]`` would then be TRUE for no worktree."""
    assert _read_field("worktree", hook_env=hook_env) == ""


def test_no_envelope_at_all_is_survivable_for_the_reader(hook_env):
    """Absence is the caller's to judge, not the reader's to crash on."""
    bare = {k: v for k, v in hook_env.items() if k != "AI_HATS_HOOK_CALL"}

    assert _read_field("actor", hook_env=bare) == ""
