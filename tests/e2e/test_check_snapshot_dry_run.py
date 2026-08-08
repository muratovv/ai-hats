"""E2E: ``--dry-run --json`` names the checks a role brings (HATS-1241).

Real ``ai-hats`` binary, real subprocess, real composition of a project-local
role that binds a check — the acceptance criterion of the card is a CLI
observable, so it is asserted on the CLI's own payload rather than on an
in-process object. A role with no bindings reports none: the same run, the
same assertion, the opposite answer.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration]

ROLE_WITH_CHECK = """\
name: checked
priorities:
  - Reliability
composition:
  traits: []
  rules: []
  skills:
    - gate-skill
  checks:
    - skill: gate-skill
      script: check.sh
      "on": [wt:pre-merge]
      on_error: refuse
injection: |
  # ROLE: CHECKED
  A role that binds one check.
"""

ROLE_WITHOUT_CHECK = """\
name: plain
priorities:
  - Reliability
composition:
  traits: []
  rules: []
  skills:
    - gate-skill
injection: |
  # ROLE: PLAIN
  A role that binds nothing.
"""

SKILL_MD = """\
---
name: gate-skill
description: A skill shipping one gate script.
---

# Gate Skill
"""


def _seed_library(project_path: Path) -> None:
    """A project-local library layer: one skill with a script, two roles."""
    lib = project_path / "libraries"
    skill_dir = lib / "skills" / "gate-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(SKILL_MD)
    (skill_dir / "data.json").write_text('{"threshold": 3}\n')
    script = skill_dir / "check.sh"
    script.write_text("#!/usr/bin/env bash\nexit 0\n")
    script.chmod(0o755)

    for name, body in (("checked", ROLE_WITH_CHECK), ("plain", ROLE_WITHOUT_CHECK)):
        role_dir = lib / "roles" / name
        role_dir.mkdir(parents=True)
        (role_dir / "config.yaml").write_text(body)


def _snapshot_targets(payload: dict) -> list[str]:
    return [
        entry["target"]
        for entry in payload["materialized"]
        if entry["kind"] == "copy_tree" and "/checks/" in entry["target"]
    ]


@pytest.fixture
def project_with_library(tmp_project):
    _seed_library(tmp_project.path)
    return tmp_project


def _dry_run(project, role: str) -> dict:
    result = project.run("--dry-run-json", "-r", role).expect_ok()
    return json.loads(result.stdout)


def test_dry_run_lists_the_snapshot_for_a_role_that_binds_one(project_with_library):
    payload = _dry_run(project_with_library, "checked")

    targets = _snapshot_targets(payload)
    assert len(targets) == 1, payload["materialized"]
    assert targets[0].endswith("/sessions/dry-run/checks/gate-skill")
    assert payload["escapes"] == [], "the snapshot must go through the port"


def test_dry_run_lists_nothing_for_a_role_that_binds_none(project_with_library):
    payload = _dry_run(project_with_library, "plain")

    assert _snapshot_targets(payload) == []
