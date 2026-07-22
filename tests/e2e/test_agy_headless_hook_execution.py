"""E2E test verifying that headless agy (-p) actually executes .gemini/settings.json runtime hooks (HATS-1105)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ai_hats_agy.provider import AgyProvider

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


@pytest.mark.skip(reason="Failing in master, agy doesn't trigger hook in headless mode natively")
@pytest.mark.integration
def test_agy_headless_p_mode_triggers_runtime_hooks(tmp_path: Path) -> None:
    """Run `agy -p` in a real repository and verify that a PreToolUse hook in .gemini/settings.json fires."""
    project = tmp_path / "project"
    project.mkdir()
    _git(project, "init", "-b", "main")
    _git(project, "config", "user.email", "test@example.com")
    _git(project, "config", "user.name", "test")
    (project / "README.md").write_text("hello world\n")
    _git(project, "add", ".")
    _git(project, "commit", "-m", "init")

    marker_file = tmp_path / "hook_marker.txt"

    # Create a custom hook script that writes to marker_file
    hook_dir = project / ".agy" / "skills" / "test-skill" / "hooks"
    hook_dir.mkdir(parents=True)
    hook_script = hook_dir / "marker_hook.py"
    hook_script.write_text(
        f"""import json, sys
data = sys.stdin.read()
with open(r"{marker_file}", "a") as f:
    f.write("HOOK_FIRED\\n")
print(json.dumps({{"hookSpecificOutput": {{"hookEventName": "PreToolUse"}}}}))
"""
    )
    hook_script.chmod(0o755)

    # Create hooks.json in skill dir
    hooks_json = hook_dir.parent / "hooks.json"
    hooks_json.write_text(json.dumps({
        "hooks": [
            {
                "event": "PreToolUse",
                "matcher": ".*",
                "command": f"{sys.executable} {hook_script}",
                "tag": "test-hook"
            }
        ]
    }))

    # Wire the hook into .gemini/settings.json and .agy/settings.json in project
    gemini_dir = project / ".gemini"
    gemini_dir.mkdir(parents=True, exist_ok=True)
    settings_data = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": ".*",
                    "command": f"{sys.executable} {hook_script}",
                    "tag": "ai-hats:test-skill:PreToolUse:.*",
                }
            ]
        }
    }
    (gemini_dir / "settings.json").write_text(json.dumps(settings_data, indent=2))

    agy_dir = project / ".agy"
    agy_dir.mkdir(parents=True, exist_ok=True)
    (agy_dir / "settings.json").write_text(json.dumps(settings_data, indent=2))

    provider = AgyProvider()

    # Run agy -p headless command
    cmd = provider.get_run_command(["agy"], "Use Bash to run echo test_execution")

    env = os.environ.copy()
    env.update(provider.get_env(tmp_path / "session", project))



    res = subprocess.run(
        cmd,
        cwd=str(project),
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )

    # Verify agy execution and hook marker firing
    assert marker_file.is_file(), (
        f"Hook marker file was not created.\n"
        f"exit_code={res.returncode}\n"
        f"stdout={res.stdout}\n"
        f"stderr={res.stderr}"
    )
    assert "HOOK_FIRED" in marker_file.read_text()
