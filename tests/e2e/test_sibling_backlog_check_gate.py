"""e2e (HATS-1575)

flow:   a role declares a gate on a sibling backlog (apps.rack.hyp) and a
        developer walks a HYP card along the edge it names
cmds:
    rack transition HYP-1 confirmed --json
    rack transition SBX-1 plan
expect: the HYP transition is refused by 'checks' in the sibling's own words,
        the card is left byte-identical, the check log lands under the
        hypotheses catalog — and the same row does NOT disturb a tasks edge
why: the sibling road built its kernel through the portable kit, which had no
     check subscriber at all, so an `on_error: refuse` gate aimed at HYP/PROP
     passed silently by construction — the failure this epic exists to remove
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from _helpers.git import git, init_repo

pytestmark = pytest.mark.integration

TRACKER = Path(".agent") / "ai-hats" / "tracker" / "backlog"
TASKS_SUB = TRACKER / "tasks"
HYP_SUB = TRACKER / "hypotheses"
SKILL = "gate-skill"
HYP_EDGE = "active->confirmed"
REFUSAL = "the hypothesis has no validation_log entry"

SKILL_MD = """\
---
name: gate-skill
description: A skill shipping the gate script this test binds.
---

# Gate Skill
"""

REFUSE_SH = f'printf "{REFUSAL}\\n"\nexit 2\n'

#: A sibling backlog beside the tasks catalog, addressed by its cli_alias.
HYP_BACKLOG = """\
name: hypotheses
prefix: HYP
cli_alias: hyp
fsm:
  initial: active
  states: [{name: active}, {name: confirmed}]
  edges: [{from: active, to: confirmed}]
links:
  kinds:
    - {name: source_task, arity: one}
"""

#: The row sits under `apps.rack.hyp` — the SIBLING's selector, not `tasks`.
ROLE_YAML = f"""\
name: sibling-gating
priorities:
  - Reliability
composition:
  traits: []
  rules: []
  skills:
    - {SKILL}
  apps:
    rack:
      hyp:
        - run: {SKILL}/refuse.sh
          at: [{HYP_EDGE}]
          on_error: refuse
injection: |
  # ROLE: sibling-gating
"""

PROJECT_YAML = """\
schema_version: 4
provider: claude
default_role: sibling-gating
task_prefix: SBX
ai_hats_dir: .agent/ai-hats
"""


@pytest.fixture
def rack_bin(shared_launcher) -> Path:
    _launcher, _env, venv = shared_launcher
    rack = venv / "bin" / "rack"
    assert rack.is_file(), "ai-hats-rack must install the `rack` console script"
    return rack


@pytest.fixture
def sibling_project(shared_launcher, tmp_path: Path):
    """A git sandbox mounting a sibling HYP backlog and a role that gates it."""
    _launcher, base_env, _venv = shared_launcher

    project = tmp_path / "proj"
    project.mkdir()
    # A MAIN checkout, not a linked worktree: the resolver's D9 clause 4 guard
    # refuses to resolve a script from inside one, before any check runs.
    init_repo(project, branch="master", harden=True)
    (project / "ai-hats.yaml").write_text(PROJECT_YAML, encoding="utf-8")
    (project / ".gitignore").write_text(".agent/\n", encoding="utf-8")
    (project / TASKS_SUB).mkdir(parents=True)

    hyp_catalog = project / HYP_SUB
    hyp_catalog.mkdir(parents=True)
    (hyp_catalog / "backlog.yaml").write_text(HYP_BACKLOG, encoding="utf-8")

    skill_dir = project / "libraries" / "skills" / SKILL
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
    script = skill_dir / "refuse.sh"
    script.write_text(f"#!/usr/bin/env bash\n{REFUSE_SH}", encoding="utf-8")
    script.chmod(0o755)

    role_dir = project / "libraries" / "roles" / "sibling-gating"
    role_dir.mkdir(parents=True)
    (role_dir / "config.yaml").write_text(ROLE_YAML, encoding="utf-8")

    git(project, "add", "-A")
    git(project, "commit", "-m", "seed")

    home = tmp_path / "home"
    home.mkdir()
    env = {
        **base_env,
        "AI_HATS_USER_HOME": str(home),
        "AI_HATS_CACHE_HOME": str(tmp_path / "cache"),
    }
    return project, env


def _rack(rack: Path, *args: str, cwd: Path, env: dict[str, str]):
    """One real ``rack`` invocation, resolved live (no AI_HATS_SESSION_ID)."""
    return subprocess.run(  # noqa: S603 - binary from the shared-launcher fixture
        [str(rack), *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _seed_hyp(project: Path, hyp_id: str = "HYP-1") -> Path:
    """A card in the sibling catalog. Written directly: what is under test is the
    transition, and this backlog's ``create`` surface is not part of the claim."""
    card = project / HYP_SUB / hyp_id / "task.yaml"
    card.parent.mkdir(parents=True)
    card.write_text(f"id: {hyp_id}\ntitle: a probe\nstate: active\n", encoding="utf-8")
    return card


def test_a_gate_declared_on_a_sibling_backlog_refuses_its_edge(sibling_project, rack_bin):
    """The HATS-1575 defect itself: this transition passed silently on every road.

    `_builder` returned None for a non-tasks instance, and the portable kit it
    fell through to composed only what `backlog.yaml` declares — so no subscriber
    of any road answered to a row addressed at a sibling.
    """
    project, env = sibling_project
    card = _seed_hyp(project)
    before = card.read_bytes()

    refused = _rack(rack_bin, "transition", "HYP-1", "confirmed", "--json", cwd=project, env=env)

    assert refused.returncode == 1, refused.stdout + refused.stderr
    payload = json.loads(refused.stdout)
    assert payload["error"]["code"] == "aborted", payload
    assert payload["error"]["subscriber"] == "checks", payload
    assert payload["error"]["reason"] == REFUSAL, payload
    # Refused before the single persist — not the state, not the `updated` stamp.
    assert card.read_bytes() == before


def test_the_refusal_reaches_the_human_channel_typed(sibling_project, rack_bin):
    project, env = sibling_project
    _seed_hyp(project)

    refused = _rack(rack_bin, "transition", "HYP-1", "confirmed", cwd=project, env=env)

    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert f"{HYP_EDGE} aborted by 'checks'" in refused.stderr
    assert REFUSAL in refused.stderr
    assert "Traceback" not in refused.stderr, "a refusal must be typed, not a stack"


def test_the_check_log_lands_under_the_gated_catalog(sibling_project, rack_bin):
    """One executor per catalog: a sibling's transcript belongs to the sibling,
    never to the project's tasks dir."""
    project, env = sibling_project
    _seed_hyp(project)

    _rack(rack_bin, "transition", "HYP-1", "confirmed", cwd=project, env=env)

    logs = sorted((project / HYP_SUB / "HYP-1" / ".checks").glob("*.log"))
    assert len(logs) == 1, [p.name for p in logs]
    # The event, escaped for a filename (HATS-1719): the arrow carries `>`,
    # a shell redirect, and this path is quoted back to an operator.
    assert logs[0].name.startswith("active-%3Econfirmed~rack~hyp~")
    assert not (project / TASKS_SUB / "HYP-1").exists()
    assert REFUSAL in logs[0].read_text(encoding="utf-8", errors="replace")


def test_the_same_row_does_not_disturb_a_tasks_edge(sibling_project, rack_bin):
    """Addressed to a sibling is a SKIP, not a refusal: one misdirected row must
    not brick the backlog it does not name (ADR-0017 §3)."""
    project, env = sibling_project
    created = _rack(rack_bin, "create", "unrelated probe", cwd=project, env=env)
    assert created.returncode == 0, created.stderr
    match = re.search(r"Created: (\S+)", created.stdout)
    assert match, created.stdout
    task_id = match.group(1)

    moved = _rack(rack_bin, "transition", task_id, "plan", cwd=project, env=env)

    assert moved.returncode == 0, moved.stdout + moved.stderr
    card = (project / TASKS_SUB / task_id / "task.yaml").read_text(encoding="utf-8")
    assert "state: plan" in card
