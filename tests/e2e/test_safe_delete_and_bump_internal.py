"""E2E (HATS-470): bump CLI removal + `_bump_internal` entry-point + trash bin.

Three contracts a reviewer can refute by reverting the relevant code:

1. ``ai-hats self bump`` is no longer a registered CLI command → exit
   ≠ 0 with "No such command 'bump'" on stderr/stdout.
2. The hidden ``python -m ai_hats._bump_internal`` works as a
   stand-alone entry-point: idempotent, exit 0, prints the trash
   summary banner when destructive ops fire.
3. A destructive bump path (legacy-ref healing inside ``self init``)
   creates a real ``$TMPDIR/ai-hats/trash-<ts>-<pid>-XXXXXX/`` session
   with a populated ``MANIFEST.md``.

Per ``dev_rule_e2e_gate``: real ``bash`` + real ``pip install`` + real
``ai-hats`` binary, marked ``@pytest.mark.integration``.

Cost amortization: shared ``installed_launcher`` (module-scoped) —
~60s pip install + ~30s self-update once per module; per-test ~1-3s.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INSTALL_LAUNCHER = REPO_ROOT / "scripts" / "install-launcher.sh"


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


@pytest.fixture(scope="module")
def installed_launcher(tmp_path_factory):
    """Install ai-hats once per module; pin via AI_HATS_VENV.

    Mirrors the fixture in ``test_self_bump_v07_heal.py`` /
    ``test_self_update_heals_legacy_refs.py`` — heavy setup amortized
    across all tests in this module.
    """
    tmp = tmp_path_factory.mktemp("launcher")
    launcher_dest = tmp / "bin" / "ai-hats"
    launcher_dest.parent.mkdir(parents=True)

    env = os.environ.copy()
    env["AI_HATS_LAUNCHER_DEST"] = str(launcher_dest)
    env["AI_HATS_REPO_URL"] = str(REPO_ROOT)
    env.pop("AI_HATS_VENV", None)

    _run(["bash", str(INSTALL_LAUNCHER)], cwd=tmp, env=env, timeout=30)
    bootstrap_proj = tmp / "_bootstrap_proj"
    bootstrap_proj.mkdir()
    _run(
        [str(launcher_dest), "self", "update"],
        cwd=bootstrap_proj, env=env, timeout=180,
    )
    shared_venv = bootstrap_proj / ".agent" / "ai-hats" / ".venv"
    assert shared_venv.is_dir(), "bootstrap did not create shared venv"
    env["AI_HATS_VENV"] = str(shared_venv)
    return launcher_dest, env, shared_venv


def _init_minimal_project(launcher: Path, env: dict, project: Path) -> None:
    """Wire ai-hats into ``project`` with assistant role + Claude provider."""
    project.mkdir(exist_ok=True)
    _run(
        [str(launcher), "self", "init", "-p", "claude",
         "-r", "assistant", "--no-wizard"],
        cwd=project, env=env, timeout=120,
    )


# ---------------------- Test 1: bump CLI removed ----------------------


@pytest.mark.integration
def test_e2e_self_bump_cli_removed(installed_launcher, tmp_path):
    """`ai-hats self bump` must not be a registered click command.

    Fail-under-revert: re-registering ``assembly.bump`` in
    ``cli/__init__.py`` flips this back to exit 0 with "Bumped" output.
    """
    launcher, env, _venv = installed_launcher
    project = tmp_path / "proj_cli_removed"
    _init_minimal_project(launcher, env, project)

    res = _run(
        [str(launcher), "self", "bump"],
        cwd=project, env=env, timeout=30, expect_exit=None,
    )
    assert res.returncode != 0, (
        f"`self bump` must fail post-HATS-470, got exit 0\n"
        f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    )
    combined = (res.stdout + res.stderr).lower()
    assert "no such command" in combined, (
        f"expected 'No such command' in output, got:\n{res.stdout}\n{res.stderr}"
    )
    assert "bump" in combined


# ---------------------- Test 2: _bump_internal entry-point ----------------------


@pytest.mark.integration
def test_e2e_bump_internal_invokable(installed_launcher, tmp_path):
    """`python -m ai_hats._bump_internal` works in the shared venv.

    This is the stable subprocess hook `self update` uses (HATS-400
    fresh-interpreter contract). Fail-under-revert: rename / remove
    the module.
    """
    launcher, env, venv = installed_launcher
    venv_python = venv / "bin" / "python"
    project = tmp_path / "proj_bump_internal"
    _init_minimal_project(launcher, env, project)

    res = _run(
        [str(venv_python), "-m", "ai_hats._bump_internal"],
        cwd=project, env=env, timeout=60, expect_exit=0,
    )
    # First run should mention bump artefacts; idempotent rerun must
    # also succeed.
    res2 = _run(
        [str(venv_python), "-m", "ai_hats._bump_internal"],
        cwd=project, env=env, timeout=60, expect_exit=0,
    )
    # Sanity: both runs report on the same project consistently.
    assert "Bumped" in res.stdout or "refreshed" in res.stdout or "hooks" in res.stdout, (
        f"first bump should print a status line; got:\n{res.stdout}"
    )
    assert res2.returncode == 0, f"idempotent rerun failed: {res2.stderr}"


@pytest.mark.integration
def test_e2e_bump_internal_rejects_unknown_args(installed_launcher, tmp_path):
    """Unknown flag → exit 2. Guards against silent typo no-ops."""
    launcher, env, venv = installed_launcher
    venv_python = venv / "bin" / "python"
    project = tmp_path / "proj_bump_internal_args"
    _init_minimal_project(launcher, env, project)

    res = _run(
        [str(venv_python), "-m", "ai_hats._bump_internal", "--bogus-flag"],
        cwd=project, env=env, timeout=30, expect_exit=2,
    )
    assert "unknown args" in res.stderr.lower()
    assert "--bogus-flag" in res.stderr


# ---------------------- Test 3: trash bin creation ----------------------


@pytest.mark.integration
def test_e2e_init_creates_trash_session_on_legacy_ref_heal(
    installed_launcher, tmp_path, monkeypatch
):
    """Seed a legacy path ref in `.claude/settings.json`, run `self init`,
    assert the trash bin captured the old content.

    Trigger: `heal_json_file` rewrites the settings.json. Per HATS-470
    this now snapshots the old content via safe_delete.replace BEFORE
    the new bytes land — so an isolated trash dir under
    `$TMPDIR/ai-hats/` exists with the original content.

    Fail-under-revert: re-introduce a raw `path.write_text(new)` in
    `migration_healer.heal_json_file` and this test flips red (no
    trash session, MANIFEST absent).
    """
    launcher, env, _venv = installed_launcher
    project = tmp_path / "proj_trash_heal"
    _init_minimal_project(launcher, env, project)

    # Point the trash bin at an isolated dir we can verify, so a
    # noisy host `/tmp/ai-hats/` doesn't confuse the assertion.
    isolated_trash = tmp_path / "trash-isolated"
    isolated_trash.mkdir()
    env_with_trash = {**env, "AI_HATS_TRASH_DIR": str(isolated_trash)}

    # Seed a legacy `.agent/hooks/...` reference in the user-owned
    # settings.json — heal_external_refs Stage A1 (JSON allowlist)
    # always auto-rewrites this regardless of git-clean state.
    settings_path = project / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_settings = {
        "hooks": {
            "PreToolUse": [{
                "matcher": "Bash",
                "hooks": [{
                    "type": "command",
                    "command": ".agent/hooks/pre_bash_shared_state_guard.sh",
                }],
            }],
        },
    }
    settings_path.write_text(json.dumps(legacy_settings, indent=2) + "\n")
    original_bytes = settings_path.read_bytes()

    # Re-run init: idempotent on yaml, triggers bump (HATS-470 ergonomics
    # fix) which runs heal_external_refs.
    _run(
        [str(launcher), "self", "init", "-p", "claude",
         "-r", "assistant", "--no-wizard"],
        cwd=project, env=env_with_trash, timeout=60,
    )

    # Verify the settings.json was actually healed (legacy substring gone).
    new_text = settings_path.read_text()
    assert ".agent/hooks/" not in new_text, (
        f"heal must rewrite the legacy path; settings.json still contains it:\n{new_text}"
    )

    # Find the trash session dir (single child of the isolated base).
    sessions = [p for p in isolated_trash.iterdir() if p.is_dir()
                and p.name.startswith("trash-")]
    assert sessions, (
        f"safe_delete must create a trash session under {isolated_trash}, "
        f"contents: {[p.name for p in isolated_trash.iterdir()]}"
    )
    session = sessions[0]

    # MANIFEST.md exists and records the heal op.
    manifest = session / "MANIFEST.md"
    assert manifest.is_file(), (
        f"trash session must carry MANIFEST.md; tree:\n"
        f"{[str(p.relative_to(session)) for p in session.rglob('*')]}"
    )
    manifest_text = manifest.read_text()
    assert "heal-json" in manifest_text, (
        f"MANIFEST must tag the heal op; got:\n{manifest_text}"
    )

    # The snapshot copy preserves the original (pre-heal) bytes —
    # recovery is `cp -r <session>/.claude/settings.json <project>/...`.
    snapshot = session / ".claude" / "settings.json"
    assert snapshot.is_file(), (
        f"snapshot of original settings.json must land under session; tree:\n"
        f"{[str(p.relative_to(session)) for p in session.rglob('*')]}"
    )
    assert snapshot.read_bytes() == original_bytes, (
        "trash snapshot must preserve the exact pre-heal bytes"
    )
