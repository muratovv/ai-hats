"""E2E (HATS-615): managed PreToolUse hook command resolves from any cwd.

Claude Code resolves a relative PreToolUse ``command`` against the agent's
**cwd**, not the project root. The HATS-437 guard was wired with a bare
relative path (``.agent/ai-hats/library/hooks/pre_bash_shared_state_guard.sh``),
so a session / sub-agent starting in a subdirectory invoked a path that did
not exist → ``/bin/sh`` exited 127 and the safety net was silently dead.

HATS-615 made the emitted command cwd-independent. Since HATS-1268 it is
absolute into the session skill mirror, which satisfies that the same way the
earlier ``$CLAUDE_PROJECT_DIR/`` prefix did — the invariant under test is that
the command resolves from any cwd, not the spelling that achieves it.

Contract under test — exactly how Claude Code invokes a hook:
``/bin/sh -c "<emitted command>"`` with ``cwd != project root`` and
``$CLAUDE_PROJECT_DIR`` in the environment, fed an irreversible tool-input
payload. A resolved + live guard denies with **exit 2**. Under the
bare-relative revert the same invocation cannot find the script → **exit 127**,
so this test is decisively fail-under-revert.

Scope note: we verify ai-hats's *emitted command string* resolves the way
Claude Code invokes it. We do NOT exercise Claude Code's own hook resolver.

Per ``dev_rule_e2e_gate``: real ``bash`` + real ``pip install`` + real
``ai-hats`` binary, marked ``@pytest.mark.integration``.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from ai_hats.constants import HOOK_PRE_TOOL_USE


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SETTINGS = Path(".claude") / "settings.json"
GUARD_TAG = "ai-hats:safety-guard:PreToolUse:Bash"


def _run(cmd, *, cwd, env, timeout, expect_exit=0):
    result = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if expect_exit is not None and result.returncode != expect_exit:
        raise AssertionError(
            f"{cmd} expected exit {expect_exit}, got {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def _init_minimal_project(launcher: Path, env: dict, project: Path) -> None:
    project.mkdir(exist_ok=True)
    _run(
        [str(launcher), "self", "init", "-p", "claude", "-r", "assistant", "--no-wizard"],
        cwd=project,
        env=env,
        timeout=120,
    )


def _managed_guard_command(project: Path) -> str:
    from ai_hats.assembler import Assembler
    from ai_hats.paths import session_cache_dir
    from ai_hats.session_artifacts import BuiltArtifacts, RunMode
    from ai_hats.surfaces.claude.provider import ClaudeProvider

    provider = ClaudeProvider()
    asm = Assembler(project)
    result = asm.composer.compose("assistant")
    provider.build_session_artifacts(
        project, result, "sid-cwd-res", run_mode=RunMode.HITL, artifacts=BuiltArtifacts()
    )
    cache_settings = session_cache_dir(project, "sid-cwd-res") / "settings.json"
    data = json.loads(cache_settings.read_text())
    pre = data["hooks"][HOOK_PRE_TOOL_USE]
    guard = [e for e in pre if e.get("_ai_hats_managed") == GUARD_TAG]
    assert len(guard) == 1, f"expected one managed guard entry, got: {pre}"
    return guard[0]["hooks"][0]["command"]


@pytest.mark.integration
def test_e2e_guard_command_resolves_from_subdirectory(installed_launcher, tmp_path):
    """Emitted guard command resolves + fires from a subdirectory cwd.

    Invoking it the way Claude Code does — ``/bin/sh -c`` with cwd in a
    subdirectory — denies an irreversible command with exit 2. Under a
    bare-relative revert the path is unresolved from the subdir → exit 127.
    """
    launcher, env, _venv = installed_launcher
    project = tmp_path / "proj_cwd_resolution"
    _init_minimal_project(launcher, env, project)

    command = _managed_guard_command(project)
    # Settings-level contract: absolute, so no cwd can change what it means.
    assert Path(command.split()[0]).is_absolute(), (
        f"managed guard command must be absolute (HATS-1268); got: {command!r}"
    )

    subdir = project / "nested" / "deep"
    subdir.mkdir(parents=True)

    # Replicate Claude Code's hook invocation: /bin/sh -c "<command>", cwd in a
    # subdirectory, $CLAUDE_PROJECT_DIR exported to the project root, no ack.
    sh_env = {k: v for k, v in env.items() if k != "AI_HATS_SHARED_STATE_ACK"}
    sh_env["CLAUDE_PROJECT_DIR"] = str(project)

    payload = json.dumps(
        {
            "tool_input": {"command": "gh pr merge 42 --merge --delete-branch"},
        }
    )
    result = subprocess.run(
        ["/bin/sh", "-c", command],
        input=payload,
        cwd=str(subdir),
        env=sh_env,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode == 2, (
        "guard command must resolve from a subdir cwd and deny the "
        f"irreversible command with exit 2; got {result.returncode} "
        "(127 = unresolved path → bare-relative revert)\n"
        f"command: {command}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
