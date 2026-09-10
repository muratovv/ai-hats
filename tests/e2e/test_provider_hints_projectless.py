"""e2e (HATS-1901)

flow:   a user requests role-specific help outside an ai-hats project
cmds:
    ai-hats -r architect --help
expect: help includes provider hints and announces projectless resolution
why:    strict project resolution must not remove built-in role help
"""  # comment-length: allow — e2e catalog schema

from __future__ import annotations

import os
import subprocess
import sys

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.surfaces]


@pytest.mark.parametrize(
    "args,hints",
    [
        (["-r", "architect", "--help"], True),
        (["--help", "-r", "architect"], True),
        (["-p", "claude", "--help"], True),
        (["-r", "missing-role", "--help"], True),
        (["--help"], False),
    ],
)
def test_help_outside_project(tmp_path, args, hints):
    result = subprocess.run(
        [sys.executable, "-m", "ai_hats", *args],
        cwd=tmp_path,
        env={**os.environ, "AI_HATS_USER_HOME": str(tmp_path / "user")},
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert ("Provider Hints" in result.stdout) is hints
    assert ("no ai-hats project above" in result.stderr) is ("-r" in args)
    assert not (tmp_path / ".agent").exists()


def test_role_help_with_invalid_config(tmp_path):
    config = tmp_path / "ai-hats.yaml"
    original = (
        "schema_version: 4\nai_hats_dir: .agent/ai-hats\n"
        "provider: claude\nmanage_gitignore: not-a-bool\n"
    )
    config.write_text(original)

    result = subprocess.run(
        [sys.executable, "-m", "ai_hats", "-r", "architect", "--help"],
        cwd=tmp_path,
        env={**os.environ, "AI_HATS_USER_HOME": str(tmp_path / "user")},
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Usage:" in result.stdout
    assert "will not load" in result.stderr
    assert "manage_gitignore" in result.stderr
    assert config.read_text() == original
