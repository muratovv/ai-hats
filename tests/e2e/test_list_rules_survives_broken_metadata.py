"""E2E test: `ai-hats list rules` survives a broken metadata.yaml file (HATS-1510)."""

from __future__ import annotations

import subprocess
from pathlib import Path
import pytest


def _run(cmd: list[str], cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return result


@pytest.mark.integration
def test_e2e_list_rules_survives_broken_metadata(shared_launcher, tmp_path: Path) -> None:
    launcher_dest, base_env, _venv = shared_launcher

    # 1. Setup isolated user home directory with a rule containing broken metadata.yaml
    user_home_dir = tmp_path / "user_home"
    user_home_dir.mkdir()

    broken_rule_dir = user_home_dir / ".ai-hats" / "rules" / "broken-user-rule"
    broken_rule_dir.mkdir(parents=True)
    (broken_rule_dir / "rule.md").write_text("# Broken user rule\n")
    broken_meta = broken_rule_dir / "metadata.yaml"
    broken_meta.write_text("description: unquoted: colon string\n")

    # 2. Setup project directory
    project = tmp_path / "project"
    project.mkdir()

    env = dict(base_env)
    env.pop("PYTHONPATH", None)
    env["AI_HATS_USER_HOME"] = str(user_home_dir)

    _run(
        [str(launcher_dest), "self", "init", "-p", "claude", "-r", "assistant"],
        cwd=project,
        env=env,
    )

    # 3. Run list rules via real launcher
    res = _run([str(launcher_dest), "list", "rules"], cwd=project, env=env)

    assert res.returncode == 0, (
        f"list rules failed (rc={res.returncode}): stdout={res.stdout!r}, stderr={res.stderr!r}"
    )
    assert "broken-user-rule" in res.stdout
    assert (
        str(broken_meta) in res.stderr
        or "broken-user-rule" in res.stderr
        or "failed to load metadata" in res.stderr
    )
