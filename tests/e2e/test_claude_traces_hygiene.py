"""e2e

flow:   a pytest session in which a test runs a real claude in its tmp dir
cmds:
    claude -p "Reply with the single word OK" --model claude-haiku-4-5
expect: once the session ends, claude's home holds no transcript dir and no config
        key named by the session's tmp paths; the transcript sits in
        <basetemp>/claude-traces/
why:    claude keys its traces by cwd into the person's home, where every live test
        left them for good
"""

from __future__ import annotations

import json
import os
import pwd
from pathlib import Path

import pytest

from tests._claude_traces import ClaudeHome

pytestmark = [pytest.mark.integration]

INNER = """
import os
import subprocess

from ai_hats.paths import claude_transcripts_dir
from tests._claude_traces import ClaudeHome, update_claude_config


def test_a_live_claude(tmp_path):
    subprocess.run(
        ["claude", "-p", "Reply with the single word OK", "--model", "claude-haiku-4-5"],
        cwd=tmp_path, check=True, capture_output=True, timeout=120,
    )
    config = ClaudeHome.of_environ(os.environ).config
    update_claude_config(config, lambda data: data.setdefault("projects", {}).setdefault(str(tmp_path), {}) == {})
    assert claude_transcripts_dir(tmp_path).is_dir()
    assert str(tmp_path) in json.loads(config.read_text())["projects"]
"""


def _key(path: Path) -> str:
    return "".join(c if c.isalnum() else "-" for c in str(path))


def test_a_live_claude_leaves_nothing_in_claudes_home(
    requires_claude_auth: None, pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The auth probe first: pytester points HOME at its tmp dir, where claude is logged out.
    monkeypatch.setenv("HOME", pwd.getpwuid(os.getuid()).pw_dir)
    pytester.makepyfile("import json\n" + INNER)

    result = pytester.runpytest("-p", "tests._claude_traces")

    result.assert_outcomes(passed=1)
    basetemp = pytester.path.parent / "basetemp"
    home = ClaudeHome.of_environ(os.environ)
    assert [d.name for d in home.projects.iterdir() if d.name.startswith(_key(basetemp))] == []
    projects = json.loads(home.config.read_text())["projects"]
    assert [k for k in projects if k.startswith(str(basetemp))] == []
    moved = list((basetemp / "claude-traces").iterdir())
    assert [d.name.startswith(_key(basetemp)) for d in moved] == [True]
    assert list(moved[0].glob("*.jsonl"))
