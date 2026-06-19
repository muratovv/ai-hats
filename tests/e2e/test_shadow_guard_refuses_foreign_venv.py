"""E2E: the self-location guard refuses a FOREIGN-venv invocation (HATS-791).

Value under test: a stale ai-hats installed (non-editable, by wheel) into a
*foreign* venv — the "shadow" of a project app-venv — must refuse-and-instruct
rather than run mis-resolved, while a managed / sanctioned invocation runs
normally. This is the runtime backstop after HATS-790 removed the
``bin/ai-hats`` console-script shadow generator.

Setup (real ``uv build`` + real non-editable wheel install + real
``python -m ai_hats``, per ``dev_rule_e2e_gate`` — no stubs):

  - Build the ai-hats wheel from the repo and install it into a throwaway
    ``<tmp>/foreign/venv`` (NOT editable, NOT under any ``.agent/ai-hats/``
    tree → a genuine foreign venv).
  - Create a project dir whose resolved venv DIFFERS from the foreign venv
    (default resolution → ``<project>/.agent/ai-hats/.venv``). Crucially do
    NOT set ``AI_HATS_VENV`` (that would pin the resolved venv to the foreign
    one and legitimately sanction it).
  - Run ``<foreign>/bin/python -m ai_hats config status`` from the project.

Assertions:
  - refuse-and-instruct on stderr + nonzero exit;
  - with ``AI_HATS_SKIP_SELF_LOCATION_GUARD=1`` it does NOT refuse.

Fail-under-revert: remove the guard (``_guard_self_location`` no-op) and the
foreign invocation silently proceeds (exit 0, no refusal text) → both the
refusal-text and nonzero-exit assertions fail.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.install_heavy  # real wheel build + install at call time → capped via conftest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _have(cmd: str) -> bool:
    import shutil

    return shutil.which(cmd) is not None


def _build_wheel(out_dir: Path, env: dict) -> Path:
    """Build the ai-hats wheel into ``out_dir``; return its path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    if _have("uv"):
        cmd = ["uv", "build", "--wheel", "--out-dir", str(out_dir), str(REPO_ROOT)]
    else:
        cmd = [sys.executable, "-m", "build", "--wheel", "--outdir", str(out_dir), str(REPO_ROOT)]
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=300)
    if proc.returncode != 0:
        pytest.skip(f"wheel build unavailable / failed:\n{proc.stdout}\n{proc.stderr}")
    wheels = sorted(out_dir.glob("ai_hats-*.whl"))
    if not wheels:
        pytest.skip(f"no wheel produced in {out_dir}:\n{proc.stdout}\n{proc.stderr}")
    return wheels[-1]


def _foreign_venv(tmp_path: Path, env: dict) -> Path:
    """Build a throwaway venv with a NON-editable wheel install of ai-hats."""
    if not _have("uv"):
        pytest.skip("uv is required to provision the foreign venv")
    wheel = _build_wheel(tmp_path / "dist", env)
    venv = tmp_path / "foreign" / "venv"
    subprocess.run(
        ["uv", "venv", "--python", "3.11", str(venv)],
        check=True, capture_output=True, text=True, env=env, timeout=120,
    )
    install = subprocess.run(
        ["uv", "pip", "install", "--python", str(venv / "bin" / "python"), str(wheel)],
        capture_output=True, text=True, env=env, timeout=300,
    )
    assert install.returncode == 0, f"foreign install failed:\n{install.stdout}\n{install.stderr}"
    return venv


@pytest.mark.integration
def test_foreign_venv_invocation_is_refused(tmp_path: Path) -> None:
    """`<foreign>/bin/python -m ai_hats config status` from a project whose
    resolved venv differs → refuse-and-instruct + nonzero; skip env disables it."""
    env = os.environ.copy()
    env.pop("AI_HATS_VENV", None)  # MUST NOT pin — pinning sanctions the foreign venv
    env.pop("PYTHONPATH", None)  # do not leak the editable src onto the foreign venv
    env.pop("AI_HATS_SKIP_SELF_LOCATION_GUARD", None)

    foreign_venv = _foreign_venv(tmp_path, env)
    foreign_python = foreign_venv / "bin" / "python"

    # A project whose default-resolved venv (<project>/.agent/ai-hats/.venv)
    # differs from the foreign venv. A minimal yaml is enough for _project_dir
    # to anchor here and venv_path to resolve the managed default.
    project = tmp_path / "project"
    project.mkdir()
    (project / "ai-hats.yaml").write_text(
        "schema_version: 4\nai_hats_dir: .agent/ai-hats\nprovider: claude\n"
    )
    # HATS-791 refinement: the guard only refuses when a managed venv ACTUALLY
    # EXISTS to be shadowed (else it's a standalone invocation, not a shadow).
    # Build the project's resolved managed venv so this models the REAL bug — a
    # configured project whose managed venv is shadowed by the foreign one.
    managed = project / ".agent" / "ai-hats" / ".venv"
    subprocess.run(
        ["uv", "venv", "--python", "3.11", str(managed)],
        check=True, capture_output=True, text=True, env=env, timeout=120,
    )

    # --- 1. guard ON → refuse-and-instruct + nonzero ---
    refused = subprocess.run(
        [str(foreign_python), "-m", "ai_hats", "config", "status"],
        cwd=str(project), env=env, capture_output=True, text=True, timeout=60,
    )
    combined = refused.stdout + refused.stderr
    assert refused.returncode != 0, (
        f"foreign invocation must be refused (nonzero), got 0:\n{combined}"
    )
    assert "refusing to run from a foreign" in refused.stderr, (
        f"remediation text missing from stderr:\n{combined}"
    )
    # Remediation names the three recovery paths.
    assert "/.local/bin/ai-hats" in refused.stderr, combined
    assert "bootstrap.sh" in refused.stderr, combined
    assert "uninstall" in refused.stderr, combined

    # --- 2. AI_HATS_SKIP_SELF_LOCATION_GUARD=1 → guard OFF, no refusal ---
    skip_env = dict(env)
    skip_env["AI_HATS_SKIP_SELF_LOCATION_GUARD"] = "1"
    skipped = subprocess.run(
        [str(foreign_python), "-m", "ai_hats", "config", "status"],
        cwd=str(project), env=skip_env, capture_output=True, text=True, timeout=60,
    )
    skipped_combined = skipped.stdout + skipped.stderr
    assert "refusing to run from a foreign" not in skipped_combined, (
        f"skip env must disable the guard, but it still refused:\n{skipped_combined}"
    )
