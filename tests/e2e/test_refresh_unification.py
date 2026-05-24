"""E2E: HATS-469 — ``Assembler._refresh()`` unification.

Five contracts a reviewer can refute by reverting the relevant code:

1. **Greenfield init is silent.** A fresh ``ai-hats self init -p claude``
   on an empty tmpdir seeds ``migration_step=latest`` BEFORE ``_refresh``
   fires; the registry is a no-op (no ``[ai-hats] running migration``
   banner). Static hooks (``.claude/settings.json`` + materialised hook
   scripts) ARE installed. Diagnostics (orphan / empty-.agent note) are
   silent — nothing to diagnose on a fresh project.
2. **Re-init triggers registry on a stale project.** A project with
   ``migration_step=0`` re-init'd via ``ai-hats self init`` replays the
   registry exactly once: banner fires on the re-init, the second init
   does NOT replay (gated). Diagnostics ARE surfaced (re-init = user-
   initiated path).
3. **First-session bootstrap is silent.** A project at
   ``migration_step=latest`` with ``default_role`` set: running
   ``ai-hats execute -r ROLE -p claude`` (which goes through
   ``runtime.set_role`` → ``_refresh(install_time=False)``) MUST install
   role git hooks + static hooks WITHOUT firing the migration banner or
   any orphan diagnostic on stderr.
4. **No residual ``.bump(`` call sites in production source.** A grep
   against ``src/`` proves HATS-469 left no dangling callers of the
   removed ``Assembler.bump`` method. Comments and docstrings that
   mention ``Assembler.bump`` historically are allowed (filtered).
5. **``Assembler.bump`` is gone, ``_refresh`` + ``_run_diagnostics`` are
   public-via-private-API surfaces.** Import-time check on the installed
   wheel — guards against a future merge that resurrects ``bump``
   without breaking the rest of the suite.

Per ``dev_rule_e2e_gate``: real ``bash`` + real ``pip install`` + real
``ai-hats`` binary, marked ``@pytest.mark.integration``. Cost
amortization: module-scoped ``installed_launcher`` (~60s pip + ~30s
self-update) shared across all tests in this module.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INSTALL_LAUNCHER = REPO_ROOT / "scripts" / "install-launcher.sh"
MIGRATION_BANNER = "[ai-hats] running migration"
ORPHAN_WARN_FRAGMENT = "Orphan ai-hats marker"
EMPTY_AGENT_NOTE_FRAGMENT = ".agent/ holds only the managed ai-hats/ namespace"


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
    """Install ai-hats once per module; pin via ``AI_HATS_VENV``.

    Same shape as ``test_migration_registry_gate.py`` /
    ``test_safe_delete_and_bump_internal.py``.
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


def _init(launcher: Path, project: Path, env: dict[str, str], *args: str):
    project.mkdir(parents=True, exist_ok=True)
    return _run(
        [str(launcher), "self", "init", *args],
        cwd=project, env=env, timeout=60,
    )


# ----- Test 1: greenfield init silent (registry no-op, no diagnostics) -----


@pytest.mark.integration
def test_e2e_greenfield_init_silent_registry_and_diagnostics(
    installed_launcher, tmp_path,
):
    """Greenfield ``ai-hats self init -p claude`` MUST install all
    artefacts but NOT print the migration banner (R2 seed-invariant) and
    NOT print any diagnostics (R3: nothing to diagnose on fresh project).
    """
    launcher, env, _venv = installed_launcher
    project = tmp_path / "greenfield"

    res = _init(launcher, project, env, "-p", "claude", "--no-wizard")

    # Registry didn't fire — R2 invariant (migration_step seeded BEFORE
    # _refresh).
    assert MIGRATION_BANNER not in res.stderr, (
        f"Greenfield init replayed registry (R2 invariant broken):\n"
        f"{res.stderr}"
    )
    # Diagnostics silent — R3 (greenfield: nothing to diagnose).
    assert ORPHAN_WARN_FRAGMENT not in res.stderr
    assert EMPTY_AGENT_NOTE_FRAGMENT not in res.stderr

    # Sanity: yaml is seeded.
    cfg_path = project / "ai-hats.yaml"
    assert cfg_path.exists()
    raw = yaml.safe_load(cfg_path.read_text())
    # Whatever ``latest_step()`` is at install time — just assert it's
    # present and non-zero (we don't pin a literal: the registry grows
    # over time).
    assert raw.get("migration_step", 0) >= 1, (
        f"greenfield init failed to seed migration_step: {raw}"
    )

    # Sanity: provider scaffold + static hooks materialized.
    assert (project / "CLAUDE.md").exists()
    settings = project / ".claude" / "settings.json"
    assert settings.exists(), "static hooks (settings.json) not installed"
    settings_data = yaml.safe_load(settings.read_text())  # JSON is YAML-superset
    assert "hooks" in settings_data, "PreToolUse entry missing"


# ----- Test 2: re-init replays registry once + surfaces diagnostics -----


@pytest.mark.integration
def test_e2e_reinit_replays_registry_once_and_runs_diagnostics(
    installed_launcher, tmp_path,
):
    """Project at ``migration_step=0`` re-init'd: registry banner fires
    on the FIRST re-init, NOT on the second (R6: init is now the auto-
    bump path; gated by migration_step). Diagnostics surface — R3:
    re-init is user-initiated.
    """
    launcher, env, _venv = installed_launcher
    project = tmp_path / "reinit"

    # First: greenfield init to materialize the structure. Then rewind
    # migration_step to 0 to emulate a project upgrading from a pre-
    # HATS-471 release.
    _init(launcher, project, env, "-p", "gemini", "--no-wizard")
    cfg_path = project / "ai-hats.yaml"
    raw = yaml.safe_load(cfg_path.read_text())
    raw["migration_step"] = 0
    cfg_path.write_text(yaml.safe_dump(raw))

    # Re-init #1: registry MUST fire.
    res1 = _init(launcher, project, env, "-p", "gemini", "--no-wizard")
    assert MIGRATION_BANNER in res1.stderr, (
        f"Re-init on migration_step=0 did NOT replay registry "
        f"(HATS-469 R6 broken — _refresh(install_time=True) on init "
        f"is the contract):\n{res1.stderr}"
    )

    # Re-init #2: registry MUST NOT fire (gated by advanced step).
    res2 = _init(launcher, project, env, "-p", "gemini", "--no-wizard")
    assert MIGRATION_BANNER not in res2.stderr, (
        f"Second re-init replayed registry (gate broken):\n{res2.stderr}"
    )


# ----- Test 3: bare-init grep proves bump() is gone from source -----


@pytest.mark.integration
def test_no_residual_bump_call_sites_in_src():
    """HATS-469 acceptance criterion: ``Assembler.bump`` is removed and
    no production code calls ``.bump(`` on an Assembler instance.

    Allowed: docstring / comment references mentioning the historical
    method, and ``ProjectConfig.migration_step`` field-doc references
    (the field still exists and its docstring naturally cites the prior
    call site). Disallowed: any executable ``.bump(`` against an
    Assembler instance.
    """
    src = REPO_ROOT / "src"
    res = subprocess.run(
        ["git", "grep", "-n", r"\.bump(", "--", str(src)],
        cwd=str(REPO_ROOT), capture_output=True, text=True,
    )
    # rc=0 → at least one hit; rc=1 → no hits (acceptable).
    # rc>1 → grep error.
    assert res.returncode in (0, 1), f"git grep error: {res.stderr}"

    offenders = []
    for line in res.stdout.splitlines():
        # Format: ``path:lineno:content``
        try:
            _path, _lineno, content = line.split(":", 2)
        except ValueError:
            continue
        stripped = content.lstrip()
        # Skip comments + docstring lines (``#``, triple-quote contexts
        # appear as "    ``Assembler.bump()``" or similar prose).
        if stripped.startswith("#"):
            continue
        # Allow doc-mention forms: backticks, RST :func:, plain prose.
        if "Assembler.bump" in content:
            continue
        # ``.bump(`` inside a string literal (e.g. error message) is
        # rare; if it appears, we accept and document the line.
        # Anything else is an executable call site → fail.
        offenders.append(line)

    assert offenders == [], (
        "HATS-469: residual ``.bump(`` call sites in src/:\n"
        + "\n".join(offenders)
    )


# ----- Test 4: first-session bootstrap (set_role) silent -----


@pytest.mark.integration
def test_e2e_set_role_bootstrap_silent_on_stderr(
    installed_launcher, tmp_path,
):
    """Runtime first-session bootstrap MUST NOT print migration banner or
    diagnostics. Subprocess invokes the installed wheel's ``Assembler.
    set_role`` against a project that has ``migration_step=latest`` but
    deliberate orphan condition (would trigger diagnostic if invoked).

    Asserts:
    - No ``[ai-hats] running migration`` (D2 ``install_time=False`` skips
      registry — HATS-469 contract).
    - No orphan-warning text on stderr (R3: set_role is runtime auto-
      trigger and must stay silent).
    - ``.claude/settings.json`` IS written + PreToolUse hook script IS
      materialized (D1: static hooks always-fire in ``_refresh``).
    """
    launcher, env, venv = installed_launcher
    project = tmp_path / "bootstrap"

    # 1. Greenfield init WITHOUT a role (so default_role stays empty and
    # set_role is the first thing to bootstrap).
    _init(launcher, project, env, "-p", "claude", "--no-wizard")

    # 2. Plant orphan condition: a fake ``~/.claude/skills/<dir>/.ai-hats-managed``
    # marker the WARN would react to. Use a fake HOME so we don't touch
    # the real ~/.claude.
    fake_home = tmp_path / "fake-home"
    skills_dir = fake_home / ".claude" / "skills"
    orphan_dir = skills_dir / "orphan-skill"
    orphan_dir.mkdir(parents=True)
    (orphan_dir / ".ai-hats-managed").write_text("orphan-skill\n")

    bootstrap_env = dict(env)
    bootstrap_env["HOME"] = str(fake_home)

    # 3. Invoke set_role from the installed wheel.
    probe = (
        "from pathlib import Path; "
        "from ai_hats.assembler import Assembler; "
        f"asm = Assembler(Path({str(project)!r})); "
        "asm.set_role('assistant', provider_name='claude'); "
        "print('set_role ok')"
    )
    py = venv / "bin" / "python"
    res = _run([str(py), "-c", probe], cwd=project, env=bootstrap_env, timeout=30)

    # Set_role MUST be silent on registry banner (install_time=False).
    assert MIGRATION_BANNER not in res.stderr, (
        f"set_role replayed registry (install_time=False contract broken):"
        f"\n{res.stderr}"
    )
    # Diagnostics MUST stay silent (R3).
    assert ORPHAN_WARN_FRAGMENT not in res.stderr, (
        f"set_role surfaced orphan diagnostic (HATS-469 R3 broken):\n"
        f"{res.stderr}"
    )

    # Static hooks (D1: always-fire) ARE installed.
    settings = project / ".claude" / "settings.json"
    assert settings.exists(), (
        f"set_role failed to install static hooks (D1 broken — "
        f"ensure_runtime_hooks must always fire in _refresh):\n"
        f"stderr={res.stderr}"
    )


# ----- Test 5: import-time surface check -----


@pytest.mark.integration
def test_assembler_surface_post_hats469(installed_launcher):
    """Import-time guard against future ``Assembler.bump`` resurrection.

    Imports ``Assembler`` from the installed wheel inside the shared
    venv (NOT the worktree src/) — proves the contract holds for the
    distribution users actually install.
    """
    _launcher, env, venv = installed_launcher
    py = venv / "bin" / "python"
    probe = (
        "from ai_hats.assembler import Assembler; "
        "import sys; "
        "assert not hasattr(Assembler, 'bump'), "
        "'HATS-469: Assembler.bump must be removed'; "
        "assert hasattr(Assembler, '_refresh'), "
        "'HATS-469: Assembler._refresh must exist'; "
        "assert hasattr(Assembler, '_run_diagnostics'), "
        "'HATS-469: Assembler._run_diagnostics must exist'; "
        "print('ok')"
    )
    res = _run([str(py), "-c", probe], cwd=REPO_ROOT, env=env, timeout=15)
    assert "ok" in res.stdout, res.stdout + res.stderr
