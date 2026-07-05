"""E2E (HATS-601): skill-declared runtime hooks propagate end-to-end.

A composed skill declaring ``runtime_hooks:`` (PreToolUse + PostToolUse) is,
after a real ``ai-hats self init``:

A. wired into ``.claude/settings.json`` — one managed entry per
   ``(event, skill, matcher)``, tagged ``ai-hats:<skill>:<event>:<matcher>``,
   under the correct event, pointing at the materialized script;
B. materialized to ``<ai_hats_dir>/library/hooks/<skill>-<basename>.sh``,
   executable;
C. functional — piping the exact ``tool_input`` JSON shape Claude Code feeds a
   hook into the materialized script yields the contracted exit code (2 on the
   sentinel, 0 otherwise). This is the "пробрасывается как надо" guarantee up
   to the Claude-Code contract boundary; we do NOT launch a real ``claude``.

Fail-under-revert:
  * drop the provider wiring (slice 2) → settings.json has no managed entry;
  * drop the materialize call (slice 1) → script missing → piping into it
    fails with exit 127 instead of the contracted 0/2.

Per ``dev_rule_e2e_gate``: real ``bash`` + real ``pip install`` + real
``ai-hats`` binary, marked ``@pytest.mark.integration``. The fixture library
lives under ``tests/fixtures/runtime_hook_lib`` and is copied into the
project's auto-searched ``<project>/libraries/`` before init.
"""

from __future__ import annotations

import json
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from ai_hats.paths import CLAUDE_PROJECT_DIR_VAR
from ai_hats.constants import HOOK_POST_TOOL_USE, HOOK_PRE_TOOL_USE


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURE_LIB = REPO_ROOT / "tests" / "fixtures" / "runtime_hook_lib"
MATERIALIZED_BASENAME = "e2e-rthook-probe.sh"
# On-disk location of the materialized script, project-root-relative.
REL_PATH = f".agent/ai-hats/library/hooks/{MATERIALIZED_BASENAME}"
# The command written into settings.json carries the ``$CLAUDE_PROJECT_DIR/``
# prefix (HATS-615) so the hook resolves regardless of the agent's cwd. Derive it
# from the SAME constant the impl uses (providers.py via paths.py) so this test
# can never silently drift from the impl again — the drift that caused HATS-645.
REL_COMMAND = CLAUDE_PROJECT_DIR_VAR + REL_PATH


def _run(cmd, *, cwd, env, timeout, expect_exit=0):
    result = subprocess.run(
        cmd, cwd=str(cwd), env=env,
        capture_output=True, text=True, timeout=timeout,
    )
    if expect_exit is not None and result.returncode != expect_exit:
        raise AssertionError(
            f"{cmd} expected exit {expect_exit}, got {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


@pytest.fixture
def installed_launcher(shared_launcher, tmp_path_factory):
    """Read-only test on the session-scoped shared venv (HATS-582 pattern).

    Mirrors ``test_pretooluse_hook_materialization.installed_launcher``: the
    test only ``self init``s a fresh project and reads it back, so it reuses
    the shared venv. Layer two hygiene knobs on a COPY of the neutral env:
    pop ``PYTHONPATH`` (``wt exec`` sets ``PYTHONPATH=src`` which shadows the
    installed package that carries ``library``) and isolate ``HOME`` (so the
    dev user's ``~/.ai-hats/`` customizations do not bleed into composition).
    """
    launcher, base_env, shared_venv = shared_launcher
    env = dict(base_env)
    env.pop("PYTHONPATH", None)
    env["HOME"] = str(tmp_path_factory.mktemp("rthook-home"))
    return launcher, env, shared_venv


def _init_with_fixture_role(launcher: Path, env: dict, project: Path) -> None:
    """Copy the fixture library into ``<project>/libraries`` and init the role.

    ``<project>/libraries`` is auto-appended to the resolver's library paths
    (Assembler._build_library_paths), so ``-r e2e-rthook-role`` resolves the
    fixture role + its ``e2e-rthook`` skill without touching ai-hats.yaml.
    """
    project.mkdir(parents=True, exist_ok=True)
    shutil.copytree(FIXTURE_LIB, project / "libraries")
    _run(
        [str(launcher), "self", "init", "-p", "claude",
         "-r", "e2e-rthook-role", "--no-wizard"],
        cwd=project, env=env, timeout=120,
    )


def _managed_entries(settings: dict) -> dict[str, list[dict]]:
    hooks = settings.get("hooks", {})
    return {event: entries for event, entries in hooks.items() if isinstance(entries, list)}


# ---------------------- A + B: wiring + materialization ----------------------


@pytest.mark.integration
def test_e2e_skill_runtime_hook_wired_and_materialized(installed_launcher, tmp_path):
    launcher, env, _venv = installed_launcher
    project = tmp_path / "proj_rthook_wire"
    _init_with_fixture_role(launcher, env, project)

    settings = json.loads((project / ".claude" / "settings.json").read_text())
    by_event = _managed_entries(settings)

    # A. PreToolUse managed entry for the skill, tagged with the matcher.
    pre = by_event.get(HOOK_PRE_TOOL_USE, [])
    sp = [e for e in pre if e.get("_ai_hats_managed") == "ai-hats:e2e-rthook:PreToolUse:Bash"]
    assert len(sp) == 1, f"missing PreToolUse skill entry in {pre}"
    assert sp[0]["matcher"] == "Bash"
    assert sp[0]["hooks"] == [{"type": "command", "command": REL_COMMAND}]
    # HATS-437 guard coexists.
    assert any(e.get("_ai_hats_managed") == "ai-hats:hats-437" for e in pre)

    # A. PostToolUse managed entry under its own event.
    post = by_event.get(HOOK_POST_TOOL_USE, [])
    pe = [
        e for e in post
        if e.get("_ai_hats_managed") == "ai-hats:e2e-rthook:PostToolUse:Edit|Write"
    ]
    assert len(pe) == 1, f"missing PostToolUse skill entry in {post}"
    assert pe[0]["matcher"] == "Edit|Write"
    assert pe[0]["hooks"] == [{"type": "command", "command": REL_COMMAND}]

    # B. The script settings.json points at exists and is executable. The
    # command carries the $CLAUDE_PROJECT_DIR/ prefix; the on-disk path is
    # REL_PATH (the prefix is a Claude-Code runtime placeholder, not a real dir).
    materialized = project / REL_PATH
    assert materialized.is_file(), f"materialized script missing: {materialized}"
    assert stat.S_IMODE(materialized.stat().st_mode) == 0o755


# ---------------------- C: live propagation ----------------------


@pytest.mark.integration
def test_e2e_materialized_runtime_hook_is_live(installed_launcher, tmp_path):
    """Pipe Claude's tool_input JSON shape into the materialized script →
    contracted exit code. Proves the chain is live (vs a dangling pointer)."""
    launcher, env, _venv = installed_launcher
    project = tmp_path / "proj_rthook_live"
    _init_with_fixture_role(launcher, env, project)

    script = project / REL_PATH
    assert script.is_file(), "precondition: materialize must have run"

    deny = subprocess.run(
        ["bash", str(script)],
        input=json.dumps({"tool_input": {"command": "RTHOOK_DENY"}}),
        cwd=str(project), env=env, capture_output=True, text=True, timeout=10,
    )
    assert deny.returncode == 2, (
        f"sentinel payload must exit 2; got {deny.returncode}\n"
        f"stdout:\n{deny.stdout}\nstderr:\n{deny.stderr}"
    )

    allow = subprocess.run(
        ["bash", str(script)],
        input=json.dumps({"tool_input": {"command": "ls -la"}}),
        cwd=str(project), env=env, capture_output=True, text=True, timeout=10,
    )
    assert allow.returncode == 0, (
        f"benign payload must exit 0; got {allow.returncode}\n"
        f"stdout:\n{allow.stdout}\nstderr:\n{allow.stderr}"
    )
