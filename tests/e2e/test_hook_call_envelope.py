"""e2e (HATS-1724)

flow:   a gate script asking WHO moved the card and WHICH declaration called it
cmds:
    rack create --parent / rack transition --state done --force
expect: every spawned check receives AI_HATS_HOOK_CALL, so a script tells a
        person's forced fast-close from the epic automation's own hop
why: the automation hop is an in-process nested transition, so the session
     identity and every ambient signal around it are byte-identical to the
     human move — a gate on a wide selector otherwise runs blind on both
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from _helpers.git import git, init_repo

pytestmark = [pytest.mark.integration, pytest.mark.guards]

TASKS_SUB = Path(".agent") / "ai-hats" / "tracker" / "backlog" / "tasks"
SKILL = "probe-skill"
SCRIPT = "record.sh"
#: Where the script writes one envelope per line. Under the project dir because
#: that is the one root the child is TOLD about on every channel.
LEDGER = "hook-calls.jsonl"

SKILL_MD = """\
---
name: probe-skill
description: Ships the recording probe these tests bind.
---

# Probe Skill
"""

#: Writes the envelope verbatim and passes. Verbatim on purpose: a probe that
#: parsed here would prove its own jq line, not the contract.
RECORD_SH = f'printf "%s\\n" "$AI_HATS_HOOK_CALL" >> "$AI_HATS_PROJECT_DIR/{LEDGER}"\nexit 0\n'

#: Both WIDE inputs — every road into the state (HATS-1719). `->execute` is the
#: road the epic automation takes, `->done` the one a person forces.
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
          at: ['->done', '->execute']
          on_error: refuse
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
    """A git sandbox whose active role binds the recording probe on two wide inputs.

    Deliberately session-LESS: no ``AI_HATS_SESSION_ID``, so the row resolves
    from the live composition instead of a session's skill mirror, and the human
    actor arrives as ``human:<login>`` — the shape a person at a terminal mints.
    """
    _launcher, base_env, _venv = shared_launcher
    project = tmp_path / "proj"
    project.mkdir()
    # A MAIN checkout, not a linked worktree: the resolver refuses otherwise.
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
    }
    return project, env


@pytest.fixture
def rack_bin(shared_launcher) -> Path:
    _launcher, _env, venv = shared_launcher
    rack = venv / "bin" / "rack"
    assert rack.is_file(), f"no rack binary at {rack}"
    return rack


def _rack(rack: Path, *args: str, cwd: Path, env: dict[str, str]):
    return subprocess.run(  # noqa: S603 - binary from the shared-launcher fixture
        [str(rack), *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _create(rack: Path, project: Path, env: dict[str, str], title: str, *extra: str) -> str:
    created = _rack(rack, "create", title, *extra, cwd=project, env=env)
    assert created.returncode == 0, created.stderr
    match = re.search(r"Created: (\S+)", created.stdout)
    assert match, created.stdout
    return match.group(1)


def _calls(project: Path) -> list[dict]:
    ledger = project / LEDGER
    assert ledger.is_file(), "no check ever spawned — the probe never ran"
    lines = [line for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert lines, "the probe ran but AI_HATS_HOOK_CALL was empty in its environment"
    return [json.loads(line) for line in lines]


def _one(calls: list[dict], event: str) -> dict:
    matching = [c for c in calls if c["event"] == event]
    assert len(matching) == 1, f"expected one {event}, got {[c['event'] for c in calls]}"
    return matching[0]


@pytest.fixture
def two_moves(probe_project, rack_bin) -> list[dict]:
    """One command, both moves: a person force-closes the child, and the epic
    automation advances the parent in the same breath.

    The forced fast-close is what makes the child ``done`` in one hop, which is
    what makes the parent's whole child-set resolved, which is what makes the
    automation cascade `brainstorm -> … -> review`. Two actors, one invocation.
    """
    project, env = probe_project
    epic = _create(rack_bin, project, env, "epic")
    child = _create(rack_bin, project, env, "child", "--parent", epic)

    closed = _rack(
        rack_bin,
        "transition",
        child,
        "--state",
        "done",
        "--force",
        "--reason",
        "e2e fast-close",
        cwd=project,
        env=env,
    )
    assert closed.returncode == 0, closed.stderr + closed.stdout
    return _calls(project)


def test_the_automation_hop_names_itself(two_moves):
    """The value of the card: the epic's own hop is legible as automation.

    `rack:` is the framework's reserved space — the CLI cannot mint it (`actor()`
    spells only `session:` and `human:`, and `--actor` is a read filter), so the
    prefix is a fact about the mover, not a claim a caller made.
    """
    hop = _one(two_moves, "plan->execute")

    assert hop["actor"] == "rack:epic-automation"
    assert hop["selector"] == "->execute"
    assert hop["from"] == "plan"
    assert hop["to"] == "execute"
    assert hop["force"] is False


def test_the_forced_fast_close_names_the_person_and_the_force(two_moves):
    """The other half of the card's title: review told apart from a force-close."""
    close = _one(two_moves, "brainstorm->done")

    assert close["actor"].startswith("human:")
    assert not close["actor"].startswith("rack:")
    assert close["force"] is True
    assert close["selector"] == "->done"


def test_the_two_moves_are_distinguishable_at_all(two_moves):
    """The fail-under-revert assertion, stated as its own claim: strip the
    envelope and this is what a script can no longer tell."""
    actors = {c["event"]: c["actor"] for c in two_moves}

    assert actors["plan->execute"] != actors["brainstorm->done"]


def test_an_inapplicable_field_is_null_not_absent(two_moves):
    """The lever the envelope buys. A gate reading an ABSENT
    ``AI_HATS_WORKTREE_PATH`` takes it for "this card brings no commits" and
    waves the transition through — the shipped done-gate has that exact branch.
    Present-and-null says "resolved, none"; an absent envelope says "no
    contract", and only the second deserves a refusal.
    """
    for call in two_moves:
        assert "worktree" in call, call
        assert call["worktree"] is None, call
        assert call["v"] == 1, call
        assert call["project_dir"], call
