"""E2E (HATS-607): a skill's runtime hook BODY actually executes.

HATS-601's e2e (`test_runtime_hook_propagation.py`) proves the script is
materialized, wired into `.claude/settings.json`, and returns the contracted
*exit code* when fed a payload. This test adds the missing dimension — proof
that the hook **body runs**, via an observable **side-effect**:

The fixture hook (`e2e-rthook/hooks/probe.sh`) appends the payload's
`hook_event_name` to the file named by `RTHOOK_MARKER`. After a real
`ai-hats self init` composes the role, we feed the materialized script the
exact JSON shape Claude Code's hook channel sends — once as `PreToolUse`,
once as `PostToolUse` — and assert the marker records BOTH events. A dangling
settings.json pointer (no materialize) would exit 127 and never write the
marker; a hook whose body never ran would leave the marker absent.

Fidelity: simulated call + side-effect (supervisor-chosen). We do NOT launch a
live `claude` — that tier is auth-gated, flaky, and tests third-party
behaviour (same boundary HATS-601 accepted).

Per `dev_rule_e2e_gate`: real `bash` + real `pip install` + real `ai-hats`
binary, `@pytest.mark.integration`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURE_LIB = REPO_ROOT / "tests" / "fixtures" / "runtime_hook_lib"


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

    Mirrors ``test_runtime_hook_propagation.installed_launcher``: pop
    ``PYTHONPATH`` (``wt exec`` sets ``PYTHONPATH=src`` which shadows the
    installed package carrying ``library``) and isolate ``HOME`` (so the dev
    user's ``~/.ai-hats/`` customizations do not bleed into composition).
    """
    launcher, base_env, shared_venv = shared_launcher
    env = dict(base_env)
    env.pop("PYTHONPATH", None)
    env["HOME"] = str(tmp_path_factory.mktemp("rthook-fires-home"))
    return launcher, env, shared_venv


def _init_with_fixture_role(launcher: Path, env: dict, project: Path) -> None:
    project.mkdir(parents=True, exist_ok=True)
    shutil.copytree(FIXTURE_LIB, project / "libraries")
    _run(
        [str(launcher), "self", "init", "-p", "claude",
         "-r", "e2e-rthook-role", "--no-wizard"],
        cwd=project, env=env, timeout=120,
    )


def _managed_command(settings: dict, tag: str) -> str:
    """The materialized-script command of the managed entry carrying ``tag``."""
    for entries in settings.get("hooks", {}).values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict) and entry.get("_ai_hats_managed") == tag:
                return entry["hooks"][0]["command"]
    raise AssertionError(f"no managed entry tagged {tag} in {settings.get('hooks')}")


@pytest.mark.integration
def test_e2e_runtime_hook_body_runs_for_both_events(installed_launcher, tmp_path):
    launcher, env, _venv = installed_launcher
    project = tmp_path / "proj_rthook_fires"
    _init_with_fixture_role(launcher, env, project)

    settings = json.loads((project / ".claude" / "settings.json").read_text())
    pre_cmd = _managed_command(settings, "ai-hats:e2e-rthook:PreToolUse:Bash")
    post_cmd = _managed_command(settings, "ai-hats:e2e-rthook:PostToolUse:Edit|Write")
    # Both events route to the same materialized script (one declared script).
    assert pre_cmd == post_cmd
    script = project / pre_cmd
    assert script.is_file(), f"materialized script missing: {script}"

    marker = tmp_path / "marker.log"
    hook_env = {**env, "RTHOOK_MARKER": str(marker)}

    # Feed the script the exact payload shape Claude's hook channel sends,
    # once per event. The hook appends hook_event_name to the marker.
    for event, command in (("PreToolUse", pre_cmd), ("PostToolUse", post_cmd)):
        payload = json.dumps({
            "hook_event_name": event,
            "tool_name": "Bash",
            "tool_input": {"command": "ls -la"},
        })
        result = subprocess.run(
            ["bash", str(project / command)],
            input=payload, cwd=str(project), env=hook_env,
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0, (
            f"{event}: benign payload must exit 0; got {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    # Side-effect proves both hook bodies executed end-to-end.
    assert marker.is_file(), "hook never wrote its marker — body did not run"
    recorded = marker.read_text().split()
    assert "PreToolUse" in recorded, f"PreToolUse hook body did not run: {recorded}"
    assert "PostToolUse" in recorded, f"PostToolUse hook body did not run: {recorded}"
