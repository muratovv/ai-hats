"""e2e (HATS-1836)

flow:   a developer running list rules against a rule that still ships a metadata.yaml
cmds:
    ai-hats list rules
expect: the rule is listed by name and nothing from the sidecar reaches stdout
why: rules are catalogued by name alone since HATS-1836 — a sidecar description was a
     second copy of the rule's meaning that drifted (5 of 14 had), and an external
     library's leftover sidecar must now be inert rather than half-read"""

from __future__ import annotations

import subprocess
from pathlib import Path
import pytest

pytestmark = pytest.mark.library

#: Distinctive enough that finding it in stdout can only mean the sidecar was read.
SENTINEL = "SENTINEL-HATS-1836-DESCRIPTION"


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
def test_e2e_list_rules_ignores_metadata(shared_launcher, tmp_path: Path) -> None:
    launcher_dest, base_env, _venv = shared_launcher

    # 1. A user-layer rule that still ships a sidecar carrying a description.
    user_home_dir = tmp_path / "user_home"
    user_home_dir.mkdir()

    rule_dir = user_home_dir / ".ai-hats" / "rules" / "leftover-user-rule"
    rule_dir.mkdir(parents=True)
    (rule_dir / "rule.md").write_text("# Leftover user rule\n")
    (rule_dir / "metadata.yaml").write_text(f"name: leftover-user-rule\ndescription: {SENTINEL}\n")

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
    # The inventory still works — the rule is discovered by its rule.md marker.
    assert "leftover-user-rule" in res.stdout
    # The discriminating assertion: before HATS-1836 this description was printed.
    assert SENTINEL not in res.stdout, (
        f"the sidecar description reached stdout — metadata.yaml is being read again: {res.stdout!r}"
    )
