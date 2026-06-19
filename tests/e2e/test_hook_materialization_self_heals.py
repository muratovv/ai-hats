"""HATS-593 — e2e: ai-hats-managed git hooks self-heal after drift, and the
dispatcher fails CLOSED when a managed hook goes missing.

Per ``dev_rule_e2e_gate`` (this touches ``src/ai_hats/cli/``, composition,
and ``scripts``-tier git hooks). Each case runs the REAL ``ai-hats`` binary
against a REAL temp git project, then drives the actual ``.githooks/``
dispatcher / self-heal scripts as subprocesses.

Two guarantees, each with a fail-under-revert property:

1. **Self-heal** (Phase 1.4 / sync-hooks): plant drift in a managed
   ``pre-push.d/*`` script, fire the post-merge hook (which calls
   ``ai-hats self sync-hooks``), and assert the hook is restored
   byte-for-byte and the manifest stays consistent. Reverting the
   sync-hooks / post-merge wiring leaves the drift in place → red.
2. **Fail-closed backstop** (Phase 2): delete an expected managed
   ``pre-push.d/*`` script and run the dispatcher — it must BLOCK (exit 1,
   "hooks corrupt"), never silently skip a degraded gate. Reverting the
   dispatcher backstop makes the missing hook silently skipped → red.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
# HATS-790: invoke the dev-venv ai-hats as `python -m ai_hats` — the
# bin/ai-hats console-script generator was removed.
AI_HATS_PYTHON = Path(sys.executable)
AI_HATS_CMD = (str(AI_HATS_PYTHON), "-m", "ai_hats")

pytestmark = pytest.mark.integration


def _binary_env() -> dict[str, str]:
    """Env that pins the ``ai-hats`` binary (and any hook it spawns) to THIS
    worktree's code.

    Two knobs, both inherited by hook-spawned children:

    * ``PYTHONPATH=<worktree>/src`` — the editable install otherwise resolves
      the main checkout's ``ai_hats`` package, which lacks the worktree's
      changes (matches the ``ai-hats wt exec`` PYTHONPATH=src convention).
    * ``AI_HATS_VENV=<dev venv>`` — a hook resolves ``ai-hats`` via PATH,
      which is the host launcher (``~/.local/bin/ai-hats``). The launcher
      requires a project venv; the synthetic test project has none, so we
      point it at the dev venv it should exec into.

    The bundled ``ai_hats.library`` still resolves to the main checkout, but
    this test supplies its own synthetic library via ``library_paths``, so
    only the Python code + dispatcher template (both under ``src/``) matter.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    env["AI_HATS_VENV"] = str(AI_HATS_PYTHON.parent.parent)
    return env


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


def _make_project_with_hooks(tmp_path: Path) -> tuple[Path, Path]:
    """Real git project + synthetic library whose role ships a pre-push hook
    (the gate-carrier) AND the post-merge/post-checkout self-heal hooks.

    Returns ``(project, lib)``.
    """
    project = tmp_path / "project"
    project.mkdir()
    _git("init", "--quiet", cwd=project)
    _git("config", "user.email", "test@example.com", cwd=project)
    _git("config", "user.name", "test", cwd=project)

    lib = tmp_path / "lib"

    # Gate-carrier skill: a pre-push hook (the protected gate) plus the
    # self-heal hooks for post-merge / post-checkout.
    skill = lib / "skills" / "gate_skill"
    (skill / "git_hooks").mkdir(parents=True)
    (skill / "SKILL.md").write_text("# Gate")
    (skill / "metadata.yaml").write_text(
        "name: gate_skill\n"
        "description: ships a pre-push gate + self-heal hooks\n"
        "git_hooks:\n"
        "  pre-push:\n"
        "    - git_hooks/gate.sh\n"
        "  post-merge:\n"
        "    - git_hooks/self-heal.sh\n"
        "  post-checkout:\n"
        "    - git_hooks/self-heal.sh\n"
    )
    gate = skill / "git_hooks" / "gate.sh"
    gate.write_text("#!/usr/bin/env bash\necho 'GATE v1'\nexit 0\n")
    gate.chmod(0o755)
    # Real-ish self-heal: resolve the binary and call sync-hooks (fail-open).
    heal = skill / "git_hooks" / "self-heal.sh"
    heal.write_text(
        "#!/usr/bin/env bash\n"
        "set -uo pipefail\n"
        'EVENT="${AI_HATS_HOOK_EVENT:-$(basename "$0")}"\n'
        'if [[ "$EVENT" == "post-checkout" ]]; then\n'
        '    [[ "${3:-0}" != "1" ]] && exit 0\n'
        "fi\n"
        'AH="$(command -v ai-hats 2>/dev/null || true)"\n'
        '[[ -z "$AH" ]] && exit 0\n'
        '"$AH" self sync-hooks || true\n'
        "exit 0\n"
    )
    heal.chmod(0o755)

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

    # Pre-write ai-hats.yaml pointing at the synthetic lib so `self init -r`
    # composes the gate-role from it.
    (project / "ai-hats.yaml").write_text(
        "provider: gemini\nlibrary_paths:\n  - " + str(lib) + "\n"
    )
    return project, lib


def _self_init(project: Path) -> None:
    """Run the REAL binary: init + apply the gate-role (installs .githooks)."""
    cp = subprocess.run(
        [*AI_HATS_CMD, "self", "init", "-p", "gemini", "-r", "gate-role",
         "--no-wizard"],
        cwd=str(project),
        env=_binary_env(),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert cp.returncode == 0, f"self init failed:\n{cp.stdout}\n{cp.stderr}"


@pytest.fixture
def initialised_project(tmp_path: Path):
    project, lib = _make_project_with_hooks(tmp_path)
    _self_init(project)
    githooks = project / ".githooks"
    assert (githooks / "pre-push").is_file(), "pre-push dispatcher not installed"
    assert (githooks / "pre-push.d" / "gate_skill-gate.sh").is_file()
    assert (githooks / "post-merge").is_file(), "post-merge dispatcher not installed"
    return project, lib


# ----- Guarantee 1: self-heal restores drifted hooks -----------------------


def test_post_merge_self_heals_drifted_pre_push_hook(initialised_project):
    project, _lib = initialised_project
    githooks = project / ".githooks"
    gate = githooks / "pre-push.d" / "gate_skill-gate.sh"
    pristine = gate.read_bytes()

    # Plant drift: mimic the HATS-589 stale-serial state by rewriting the hook.
    gate.write_text("#!/usr/bin/env bash\n# STALE DRIFTED VERSION\nexit 0\n")
    assert gate.read_bytes() != pristine

    # Fire the REAL post-merge hook the way git would (dispatcher → self-heal
    # → `ai-hats self sync-hooks`). Run from the project dir so the binary
    # resolves the right repo.
    cp = subprocess.run(
        [str(githooks / "post-merge")],
        cwd=str(project),
        env=_binary_env(),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert cp.returncode == 0, f"post-merge hook errored:\n{cp.stdout}\n{cp.stderr}"

    # Hook restored byte-for-byte to the composed source.
    assert gate.read_bytes() == pristine, "drifted hook was not healed"
    # Manifest still lists the managed entries.
    manifest = (githooks / ".ai-hats-manifest").read_text()
    assert "pre-push.d/gate_skill-gate.sh" in manifest
    assert "pre-push" in manifest


def test_sync_hooks_restores_deleted_pre_push_hook(initialised_project):
    project, _lib = initialised_project
    githooks = project / ".githooks"
    gate = githooks / "pre-push.d" / "gate_skill-gate.sh"
    pristine = gate.read_bytes()
    gate.unlink()  # the hook is entirely gone

    cp = subprocess.run(
        [*AI_HATS_CMD, "self", "sync-hooks"],
        cwd=str(project),
        env=_binary_env(),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert cp.returncode == 0, f"sync-hooks errored:\n{cp.stdout}\n{cp.stderr}"
    assert gate.is_file(), "deleted hook was not restored"
    assert gate.read_bytes() == pristine


def test_sync_hooks_is_noop_when_in_sync(initialised_project):
    project, _lib = initialised_project
    githooks = project / ".githooks"
    gate = githooks / "pre-push.d" / "gate_skill-gate.sh"
    before = gate.stat().st_mtime_ns

    cp = subprocess.run(
        [*AI_HATS_CMD, "self", "sync-hooks"],
        cwd=str(project),
        env=_binary_env(),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert cp.returncode == 0
    # Idempotent: no rewrite when already consistent.
    assert gate.stat().st_mtime_ns == before
    assert "in sync" in cp.stdout.lower()


# ----- Guarantee 2: fail-closed backstop blocks a degraded gate ------------


def test_dispatcher_blocks_when_managed_hook_missing(initialised_project):
    project, _lib = initialised_project
    githooks = project / ".githooks"
    gate = githooks / "pre-push.d" / "gate_skill-gate.sh"
    gate.unlink()  # worst case: self-heal failed AND the hook is gone

    # Run the dispatcher directly (no self-heal in the loop) — the backstop
    # must refuse to run a degraded gate.
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
