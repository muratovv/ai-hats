"""e2e (HATS-1263)

flow:   a developer running ai-hats commands when package files are corrupted or
        mismatched
cmds:
    ai-hats list roles
expect: CLI exits with friendly installation error detailing repair steps without
        tracebacks
why:    without CLI exception catching, corrupted subpackages dump raw ImportErrors
        instead of repair guidance
"""  # comment-length: allow

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.smoke, pytest.mark.install]

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Read-only probe that is NOT the retired ``ai-hats task`` surface (HATS-1263).
PROBE = ("list", "roles")


def _broken_rack_pythonpath(tmp_path: Path, body: str) -> dict[str, str]:
    """PYTHONPATH whose first entry shadows ``ai_hats_rack`` with ``body``."""
    broken_dir = tmp_path / "broken_site"
    pkg_dir = broken_dir / "ai_hats_rack"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "__init__.py").write_text(body)
    cur_pythonpath = os.environ.get("PYTHONPATH", "")
    src_dir = REPO_ROOT / "src"
    return {"PYTHONPATH": f"{broken_dir}:{src_dir}:{cur_pythonpath}"}


def test_e2e_broken_install_friendly_error(tmp_project, tmp_path: Path) -> None:
    """``ai-hats`` on a broken install exits 1 with remediation and no traceback."""
    extra_env = _broken_rack_pythonpath(
        tmp_path,
        "raise ImportError(\"cannot import name PROVIDER_GEMINI from 'ai_hats.constants'\")\n",
    )

    # 1. Normal invocation: friendly error, no traceback
    result = tmp_project.run(*PROBE, extra_env=extra_env, timeout=10.0)

    assert result.exit_code == 1, (
        f"expected exit code 1, got {result.exit_code}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )

    assert "Inconsistent or broken ai-hats installation" in result.stderr, result.stderr
    assert "cannot import name PROVIDER_GEMINI" in result.stderr, result.stderr
    assert "Likely cause: package files are out of sync or corrupted." in result.stderr, (
        result.stderr
    )
    # HATS-1368: the repair is rendered for the install shape under test — an
    # editable checkout is re-pointed, since `self update` runs the broken CLI.
    assert "Repair command: uv pip install" in result.stderr, result.stderr
    assert " -e " in result.stderr, result.stderr
    assert (
        "Debug with: AI_HATS_DEBUG=1, AI_HATS_VERBOSE=1, --debug, --verbose, -v" in result.stderr
    ), result.stderr

    combined = result.stdout + result.stderr
    assert "Traceback" not in combined, f"Traceback leaked in normal mode:\n{combined}"

    # 2. Debug mode (AI_HATS_DEBUG=1): full traceback re-raised
    debug_env = {**extra_env, "AI_HATS_DEBUG": "1"}
    debug_result = tmp_project.run(*PROBE, extra_env=debug_env, timeout=10.0)

    assert debug_result.exit_code != 0, (
        f"expected non-zero exit in debug mode, got {debug_result.exit_code}"
    )
    debug_combined = debug_result.stdout + debug_result.stderr
    assert "Traceback" in debug_combined, f"Traceback missing in debug mode:\n{debug_combined}"


def test_e2e_runtime_attribute_error_not_misreported(tmp_project, tmp_path: Path) -> None:
    """Runtime AttributeError on an object is NOT misreported as a broken install (HATS-1132)."""
    extra_env = _broken_rack_pythonpath(
        tmp_path,
        # Object-level AttributeError (not a module-level AttributeError)
        "raise AttributeError(\"'AgySurface' object has no attribute 'get_cli_launch_args'\")\n",
    )

    result = tmp_project.run(*PROBE, extra_env=extra_env, timeout=10.0)

    assert result.exit_code != 0
    combined = result.stdout + result.stderr
    assert "Inconsistent or broken ai-hats installation" not in combined, combined
    assert "python -m ai_hats self update" not in combined, combined
