"""e2e (HATS-1241, HATS-1540, HATS-1548)

flow:   a developer inspecting the dry-run of a role that binds a check script
cmds:
    ai-hats --dry-run-json -r checked
    ai-hats --dry-run-json -r plain
expect: the skill materialized once at the mirror; the checks section names the
        binding the plan cannot show
why:    the mirror is per SKILL, so no part of the plan depends on a checks row
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.gates]

ROLE_WITH_CHECK = """\
name: checked
priorities:
  - Reliability
composition:
  traits: []
  rules: []
  skills:
    - gate-skill
  apps:
    wt:
      - run: gate-skill/check.sh
        at: [pre-merge]
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


#: The claude plugin's skills root inside a dry-run session — the surface's own
#: mirror, and since HATS-1540 the root a bound check resolves its script from
#: (``ClaudeSurface.session_skills_root``). ``tmp_project`` pins the provider.
MIRROR_SUFFIX = "/sessions/dry-run/plugin/skills/gate-skill"


def _skill_copies(payload: dict) -> list[str]:
    """Every whole-directory copy the plan makes of the bound skill."""
    return [
        entry["target"]
        for entry in payload["materialized"]
        if entry["kind"] == "copy_tree" and entry["target"].endswith("/gate-skill")
    ]


def _plan_targets(payload: dict) -> list[tuple[str, str]]:
    """``(kind, target)`` for the whole plan — content is role-dependent, paths
    are not, so this is the shape two roles can be compared on."""
    return [(entry["kind"], entry["target"]) for entry in payload["materialized"]]


@pytest.fixture
def project_with_library(tmp_project):
    _seed_library(tmp_project.path)
    return tmp_project


def _dry_run(project, role: str) -> dict:
    result = project.run("--dry-run-json", "-r", role).expect_ok()
    return json.loads(result.stdout)


def test_dry_run_plans_the_one_mirror_copy_the_binding_will_run_from(project_with_library):
    """The bound skill is materialized EXACTLY once, at the surface's mirror.

    ``len == 1`` is the load-bearing half: a second copy_tree of the same skill
    would be the retired checks materialization coming back under another name.
    """
    payload = _dry_run(project_with_library, "checked")

    copies = _skill_copies(payload)
    assert len(copies) == 1, payload["materialized"]
    assert copies[0].endswith(MIRROR_SUFFIX)
    assert payload["escapes"] == [], "the mirror must go through the port"


def test_binding_a_check_adds_nothing_to_the_plan(project_with_library):
    """Tombstone, on the CLI: the channel materializes nothing of its own.

    Two roles over the same skill — one binding it to a point, one not — plan the
    same targets, because the mirror the check resolves from is written for the
    skill, not for the binding. That is also why a session older than the binding
    resolves now (HATS-1538). Content still differs (role name in the prompt and
    the plugin manifest), so only kind+target are compared.
    """
    bound = _dry_run(project_with_library, "checked")
    unbound = _dry_run(project_with_library, "plain")

    assert _plan_targets(bound) == _plan_targets(unbound)
    assert [t for _kind, t in _plan_targets(bound) if "/checks/" in t] == []


def test_the_report_names_the_binding_the_plan_cannot_show(project_with_library):
    """HATS-1548, the inversion: what the plan lost, the report says outright.

    The pair above is exactly why this one is needed — two roles that plan the
    same targets must still be told apart, or an operator cannot see the gate
    that will refuse their transition before they start the session.
    """
    bound = _dry_run(project_with_library, "checked")
    unbound = _dry_run(project_with_library, "plain")

    assert unbound["checks"] == []
    assert [
        (c["skill"], c["script"], c["app"], tuple(c["at"]), c["on_error"], c["declared_by"])
        for c in bound["checks"]
    ] == [("gate-skill", "check.sh", "wt", ("pre-merge",), "refuse", "checked")]


def test_the_report_says_where_the_gate_runs_from_and_that_the_plan_covers_it(
    project_with_library,
):
    """A list of bindings is the weak half — the binding is already in the role.

    What an operator cannot read anywhere is whether the resolution SETTLES under
    this surface: same mirror the plan writes, same leaf spelling. So the report
    resolves it the way the session will and says whether the plan covers those
    bytes. Cross-checked against the plan entry rather than asserted twice.
    """
    payload = _dry_run(project_with_library, "checked")

    (check,) = payload["checks"]
    assert check["runs_from"] == _skill_copies(payload)[0] + "/check.sh"
    assert check["runs_from"].endswith(MIRROR_SUFFIX + "/check.sh")
    assert check["planned"] is True
