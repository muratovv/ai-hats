"""E2E (HATS-615): managed PreToolUse hook command resolves from any cwd.

Claude Code resolves a relative PreToolUse ``command`` against the agent's
**cwd**, not the project root. The HATS-437 guard was wired with a bare
relative path (``.agent/ai-hats/library/hooks/pre_bash_shared_state_guard.sh``),
so a session / sub-agent starting in a subdirectory invoked a path that did
not exist → ``/bin/sh`` exited 127 and the safety net was silently dead.

HATS-615 prefixes the emitted command with ``$CLAUDE_PROJECT_DIR/`` (Claude
Code expands the var at hook-execution time), so it resolves regardless of cwd.

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


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SETTINGS = Path(".claude") / "settings.json"
GUARD_TAG = "ai-hats:hats-437"


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
    """Read-only launcher on the session-shared venv (HATS-582).

    Mirrors ``test_pretooluse_hook_materialization``: this test only
    ``self init``s into a fresh ``tmp_path`` project and reads settings.json
    back — it never mutates the venv. Two hygiene knobs on a COPY of the
    shared env:

    * pop ``PYTHONPATH`` — ``ai-hats wt exec`` sets ``PYTHONPATH=src`` which
      shadows the installed package with the source tree (no ``library``
      subpackage) → "no roles found".
    * isolate ``HOME`` — keep the dev user's ``~/.ai-hats/`` customizations
      out of composition.
    """
    launcher, base_env, shared_venv = shared_launcher
    env = dict(base_env)
    env.pop("PYTHONPATH", None)
    env["HOME"] = str(tmp_path_factory.mktemp("cwd-resolution-home"))
    return launcher, env, shared_venv


def _init_minimal_project(launcher: Path, env: dict, project: Path) -> None:
    project.mkdir(exist_ok=True)
    _run(
        [str(launcher), "self", "init", "-p", "claude",
         "-r", "assistant", "--no-wizard"],
        cwd=project, env=env, timeout=120,
    )


def _managed_guard_command(project: Path) -> str:
    data = json.loads((project / SETTINGS).read_text())
    pre = data["hooks"]["PreToolUse"]
    guard = [e for e in pre if e.get("_ai_hats_managed") == GUARD_TAG]
    assert len(guard) == 1, f"expected one managed guard entry, got: {pre}"
    return guard[0]["hooks"][0]["command"]


@pytest.mark.integration
def test_e2e_guard_command_resolves_from_subdirectory(installed_launcher, tmp_path):
    """Emitted guard command resolves + fires from a subdirectory cwd.

    The settings command carries the ``$CLAUDE_PROJECT_DIR/`` placeholder, and
    invoking it the way Claude Code does — ``/bin/sh -c`` with cwd in a
    subdirectory and ``CLAUDE_PROJECT_DIR`` exported — denies an irreversible
    command with exit 2. Under the bare-relative revert the path is unresolved
    from the subdir → exit 127.
    """
    launcher, env, _venv = installed_launcher
    project = tmp_path / "proj_cwd_resolution"
    _init_minimal_project(launcher, env, project)

    command = _managed_guard_command(project)
    # Settings-level contract: the placeholder is present.
    assert command.startswith("$CLAUDE_PROJECT_DIR/"), (
        f"managed guard command must be $CLAUDE_PROJECT_DIR-prefixed; got: {command!r}"
    )

    subdir = project / "nested" / "deep"
    subdir.mkdir(parents=True)

    # Replicate Claude Code's hook invocation: /bin/sh -c "<command>", cwd in a
    # subdirectory, $CLAUDE_PROJECT_DIR exported to the project root, no ack.
    sh_env = {k: v for k, v in env.items() if k != "AI_HATS_SHARED_STATE_ACK"}
    sh_env["CLAUDE_PROJECT_DIR"] = str(project)

    payload = json.dumps({
        "tool_input": {"command": "gh pr merge 42 --merge --delete-branch"},
    })
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
