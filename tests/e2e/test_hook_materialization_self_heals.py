"""HATS-593 → HATS-833 — e2e: managed-hook drift healing + dispatcher backstop.

Per ``dev_rule_e2e_gate`` (this touches ``src/ai_hats/cli/`` — the removed
``self sync-hooks`` command — composition, and ``scripts``-tier git hooks).

Guarantees, each with a fail-under-revert property:

1. **`self sync-hooks` is GONE** (HATS-833): the standalone command was removed
   when healing was consolidated to session start. The REAL binary must reject
   it. Reverting the removal re-adds the command → it exits 0 → test fails.
2. **Session-start heal** (HATS-833): a drifted/unwired runtime hook is
   re-materialized AND re-wired at launch, with an observable startup note.
   Reverting the generalized ``sync_hooks`` / ``_resync_managed_hooks`` leaves
   the drift in place → test fails.
3. **Fail-closed backstop** (unchanged): delete an expected managed
   ``pre-push.d/*`` script and run the dispatcher — it must BLOCK (exit 1,
   "hooks corrupt"), never silently skip a degraded gate.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from ai_hats.assembler import Assembler
from ai_hats.cli import main
from ai_hats.paths import hooks_dir, managed_runtime_hook_filename


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
# HATS-790: invoke the dev-venv ai-hats as `python -m ai_hats` — the
# bin/ai-hats console-script generator was removed.
AI_HATS_PYTHON = Path(sys.executable)
AI_HATS_CMD = (str(AI_HATS_PYTHON), "-m", "ai_hats")

pytestmark = pytest.mark.integration


def _binary_env() -> dict[str, str]:
    """Env pinning the ``ai-hats`` binary to THIS worktree's code (PYTHONPATH=src
    so the editable install doesn't resolve the main checkout)."""
    from _helpers.env import checkout_pythonpath

    env = dict(os.environ)
    env["PYTHONPATH"] = checkout_pythonpath(REPO_ROOT)
    env["AI_HATS_VENV"] = str(AI_HATS_PYTHON.parent.parent)
    return env


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


# ----- Guarantee 1: `self sync-hooks` removed -------------------------------


def test_self_sync_hooks_command_removed(tmp_path: Path):
    """The standalone ``ai-hats self sync-hooks`` command no longer exists
    (HATS-833 consolidated healing to session start)."""
    cp = subprocess.run(
        [*AI_HATS_CMD, "self", "sync-hooks"],
        cwd=str(tmp_path),
        env=_binary_env(),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert cp.returncode != 0, (
        "`self sync-hooks` should be GONE but exited 0:\n"
        f"{cp.stdout}\n{cp.stderr}"
    )
    combined = (cp.stdout + cp.stderr).lower()
    assert "no such command" in combined or "usage" in combined, (
        f"expected a click 'no such command' error, got:\n{cp.stdout}\n{cp.stderr}"
    )


# ----- Guarantee 2: session-start heal of a drifted runtime hook ------------


def _make_runtime_hook_project(tmp_path: Path) -> tuple[Path, Path]:
    """Real git project + synthetic library whose role ships a PreToolUse
    runtime hook (claude provider → it is both materialized to ``library/hooks/``
    AND wired into ``.claude/settings.json``)."""
    project = tmp_path / "rtproj"
    project.mkdir()
    _git("init", "--quiet", cwd=project)
    _git("config", "user.email", "t@e.com", cwd=project)
    _git("config", "user.name", "t", cwd=project)

    lib = tmp_path / "rtlib"
    skill = lib / "skills" / "rt_skill"
    (skill / "hooks").mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\n"
        "name: rt_skill\n"
        "description: ships a PreToolUse runtime hook\n"
        "ai_hats:\n"
        "  runtime_hooks:\n"
        "    PreToolUse:\n"
        "      - matcher: Bash\n"
        "        script: hooks/rt.sh\n"
        "---\n# rt\n"
    )
    rt_script = skill / "hooks" / "rt.sh"
    rt_script.write_text("#!/usr/bin/env bash\nexit 0\n")
    rt_script.chmod(0o755)

    trait = lib / "traits" / "trait-base"
    trait.mkdir(parents=True)
    (trait / "config.yaml").write_text(
        "name: trait-base\ncomposition:\n  skills:\n    - rt_skill\ninjection: B.\n"
    )
    role = lib / "roles" / "rt-role"
    role.mkdir(parents=True)
    (role / "config.yaml").write_text(
        "name: rt-role\npriorities: [Quality]\n"
        "composition:\n  traits:\n    - trait-base\ninjection: R.\n"
    )
    (project / "ai-hats.yaml").write_text(
        "provider: claude\n"
        "ai_hats_dir: .agent/ai-hats\n"
        "active_role: rt-role\n"
        "default_role: rt-role\n"
        "library_paths:\n  - " + str(lib) + "\n"
    )
    return project, lib


def test_session_start_heals_drifted_runtime_hook(tmp_path: Path, monkeypatch):
    project, lib = _make_runtime_hook_project(tmp_path)
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    asm.set_role("rt-role", provider_name="claude")

    script = hooks_dir(project) / managed_runtime_hook_filename("rt_skill", "hooks/rt.sh")
    settings = project / ".claude" / "settings.json"
    assert script.is_file(), "baseline: runtime hook should be materialized"
    assert "rt_skill" in settings.read_text(), "baseline: runtime hook should be wired"

    # Plant drift: delete the materialized script AND unwire its managed entry —
    # exactly the silent stale state HATS-833 targets (composed but not on disk).
    script.unlink()
    data = json.loads(settings.read_text())
    data["hooks"]["PreToolUse"] = [
        e for e in data["hooks"]["PreToolUse"] if "rt_skill" not in json.dumps(e)
    ]
    settings.write_text(json.dumps(data))
    assert "rt_skill" not in settings.read_text()

    # Launch a real wrapped HITL session (composition + materializers run for
    # real; only the PTY spawn is stubbed). Force a brief hold so the heal note
    # is emitted to stdout even in the non-tty CliRunner.
    from ai_hats import runtime as rt

    monkeypatch.setattr(rt.WrapRunner, "_pty_spawn", lambda self, cmd, env, tracer: 0)
    monkeypatch.setenv("AI_HATS_STARTUP_HOLD", "0.05")
    monkeypatch.chdir(project)

    result = CliRunner().invoke(main, [])
    assert result.exit_code == 0, (
        f"launch exited {result.exit_code}\n{result.output}\nexc={result.exception!r}"
    )

    # Healed end-to-end: script re-materialized AND managed entry re-wired ...
    assert script.is_file(), "session start did not re-materialize the runtime hook"
    assert "rt_skill" in settings.read_text(), "session start did not re-wire the hook"
    # ... and observable (req-5): the startup note names the healed surface.
    assert "managed hooks healed at start" in result.output, (
        f"expected heal note in output:\n{result.output}"
    )


# ----- Guarantee 3: fail-closed dispatcher backstop (unchanged) -------------


def _make_gate_project(tmp_path: Path) -> tuple[Path, Path]:
    """Real git project + synthetic library whose role ships a pre-push gate
    (the protected hook). No self-heal hooks — HATS-833 removed that surface."""
    project = tmp_path / "project"
    project.mkdir()
    _git("init", "--quiet", cwd=project)
    _git("config", "user.email", "test@example.com", cwd=project)
    _git("config", "user.name", "test", cwd=project)

    lib = tmp_path / "lib"
    skill = lib / "skills" / "gate_skill"
    (skill / "git_hooks").mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\n"
        "name: gate_skill\n"
        "description: ships a pre-push gate\n"
        "ai_hats:\n"
        "  git_hooks:\n"
        "    pre-push:\n"
        "      - git_hooks/gate.sh\n"
        "---\n\n# Gate\n"
    )
    gate = skill / "git_hooks" / "gate.sh"
    gate.write_text("#!/usr/bin/env bash\necho 'GATE v1'\nexit 0\n")
    gate.chmod(0o755)

    trait = lib / "traits" / "trait-base"
    trait.mkdir(parents=True)
    (trait / "config.yaml").write_text(
        "name: trait-base\ncomposition:\n  skills:\n    - gate_skill\ninjection: B.\n"
    )
    role = lib / "roles" / "gate-role"
    role.mkdir(parents=True)
    (role / "config.yaml").write_text(
        "name: gate-role\npriorities: [Quality]\n"
        "composition:\n  traits:\n    - trait-base\ninjection: R.\n"
    )
    (project / "ai-hats.yaml").write_text(
        "provider: gemini\nlibrary_paths:\n  - " + str(lib) + "\n"
    )
    return project, lib


def _self_init(project: Path) -> None:
    cp = subprocess.run(
        [*AI_HATS_CMD, "self", "init", "-p", "gemini", "-r", "gate-role", "--no-wizard"],
        cwd=str(project),
        env=_binary_env(),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert cp.returncode == 0, f"self init failed:\n{cp.stdout}\n{cp.stderr}"


@pytest.fixture
def initialised_project(tmp_path: Path):
    project, lib = _make_gate_project(tmp_path)
    _self_init(project)
    githooks = project / ".githooks"
    assert (githooks / "pre-push").is_file(), "pre-push dispatcher not installed"
    assert (githooks / "pre-push.d" / "gate_skill-gate.sh").is_file()
    return project, lib


def test_dispatcher_blocks_when_managed_hook_missing(initialised_project):
    project, _lib = initialised_project
    githooks = project / ".githooks"
    gate = githooks / "pre-push.d" / "gate_skill-gate.sh"
    gate.unlink()  # worst case: the managed hook is gone

    cp = subprocess.run(
        [str(githooks / "pre-push")],
        cwd=str(project),
        input="refs/heads/master " + "1" * 40 + " refs/heads/master " + "2" * 40 + "\n",
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert cp.returncode == 1, (
        "dispatcher must FAIL CLOSED on a missing managed hook, "
        f"got exit {cp.returncode}\nstdout:{cp.stdout}\nstderr:{cp.stderr}"
    )
    assert "corrupt" in cp.stderr
    assert "ai-hats self init" in cp.stderr


def test_dispatcher_runs_clean_when_intact(initialised_project):
    """Counter-test: an intact gate runs normally (exit 0), no false block."""
    project, _lib = initialised_project
    githooks = project / ".githooks"

    cp = subprocess.run(
        [str(githooks / "pre-push")],
        cwd=str(project),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert cp.returncode == 0, f"intact gate must not block:\n{cp.stderr}"
    assert "GATE v1" in cp.stdout
    assert "corrupt" not in cp.stderr
