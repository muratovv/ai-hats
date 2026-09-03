"""e2e (HATS-1878)

flow:   a gate script learning WHICH gate its binding row declared it to be
cmds:
    rack transition --state done --force   (a row carrying `gate:` and `weight:`)
expect: the row's cargo reaches the spawned check as AI_HATS_CARGO_GATE and
        AI_HATS_CARGO_WEIGHT, so one script serves every gate and the usage
        site names the gate
why:    `run:` carries no argv, so a gate per edge used to be a FILE per edge —
        three near-identical scripts differing in two strings
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from _helpers.git import git, init_repo

pytestmark = pytest.mark.integration

TASKS_SUB = Path(".agent") / "ai-hats" / "tracker" / "backlog" / "tasks"
SKILL = "probe-skill"
SCRIPT = "which-gate.sh"
LEDGER = "which-gate.log"

SKILL_MD = """\
---
name: probe-skill
description: Ships the recording probe this test binds.
---

# Probe Skill
"""

#: Records the two cargo variables, verbatim, and passes.
RECORD_SH = (
    'printf "%s|%s\\n" "${AI_HATS_CARGO_GATE-unset}" "${AI_HATS_CARGO_WEIGHT-unset}" '
    f'>> "$AI_HATS_PROJECT_DIR/{LEDGER}"\nexit 0\n'
)

#: The row: what ai-hats owns (`run`, `at`, `on_error`) plus two keys it does
#: not — a string and a number, the two shapes the flattening distinguishes.
_ROLE_YAML = f"""\
name: probe
priorities:
  - Reliability
composition:
  traits: []
  rules: []
  skills:
    - {SKILL}
  apps:
    rack:
      tasks:
        - run: {SKILL}/{SCRIPT}
          at: ['->done']
          on_error: refuse
          gate: probe-gate
          weight: 3
injection: |
  # ROLE: probe
"""

_PROJECT_YAML = """\
schema_version: 4
provider: claude
default_role: probe
task_prefix: SBX
ai_hats_dir: .agent/ai-hats
"""


@pytest.fixture
def probe_project(shared_launcher, tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """A session-less git sandbox whose active role binds the probe with cargo."""
    _launcher, base_env, _venv = shared_launcher
    project = tmp_path / "proj"
    project.mkdir()
    init_repo(project, branch="master", harden=True)
    (project / "ai-hats.yaml").write_text(_PROJECT_YAML, encoding="utf-8")
    (project / ".gitignore").write_text(".agent/\n", encoding="utf-8")
    (project / TASKS_SUB).mkdir(parents=True)

    skill_dir = project / "libraries" / "skills" / SKILL
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
    script = skill_dir / SCRIPT
    script.write_text(f"#!/usr/bin/env bash\n{RECORD_SH}", encoding="utf-8")
    script.chmod(0o755)

    role_dir = project / "libraries" / "roles" / "probe"
    role_dir.mkdir(parents=True)
    (role_dir / "config.yaml").write_text(_ROLE_YAML, encoding="utf-8")

    git(project, "add", "-A")
    git(project, "commit", "-m", "seed")

    home = tmp_path / "home"
    home.mkdir()
    env = {
        **base_env,
        "AI_HATS_USER_HOME": str(home),
        "AI_HATS_CACHE_HOME": str(tmp_path / "cache"),
        # A stale value from "outside" must be scrubbed, not inherited.
        "AI_HATS_CARGO_GATE": "stale-from-the-launching-shell",
    }
    return project, env


def _rack(venv: Path, *args: str, cwd: Path, env: dict[str, str]):
    rack = venv / "bin" / "rack"
    assert rack.is_file(), f"no rack binary at {rack}"
    return subprocess.run(  # noqa: S603 - binary from the shared-launcher fixture
        [str(rack), *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_the_rows_cargo_names_the_gate_to_the_script_it_binds(shared_launcher, probe_project):
    """HATS-1878: one `hooks/gate.sh` for every edge needs the row to say which
    gate it is; before this the only channel for that was the script's filename."""
    project, env = probe_project
    _launcher, _env, venv = shared_launcher

    created = _rack(venv, "create", "card", cwd=project, env=env)
    assert created.returncode == 0, created.stderr
    match = re.search(r"Created: (\S+)", created.stdout)
    assert match, created.stdout
    closed = _rack(
        venv,
        "transition",
        match.group(1),
        "--state",
        "done",
        "--force",
        "--reason",
        "e2e fast-close",
        cwd=project,
        env=env,
    )
    assert closed.returncode == 0, closed.stderr + closed.stdout

    ledger = project / LEDGER
    assert ledger.is_file(), "no check ever spawned — the probe never ran"
    assert ledger.read_text(encoding="utf-8").splitlines() == ["probe-gate|3"]
