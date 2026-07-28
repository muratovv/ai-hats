"""E2E: a real cline session leaves the project root clean (HATS-1171).

Fail-under-revert: restore the old ``ClineProvider`` and a headless cline run
materializes ``.cline/skills`` (+ ``.cline/plugins``) into the project root and
appends ``.cline/*`` to ``.gitignore``; both assertions below then fail.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from _helpers.env import checkout_pythonpath
from _helpers.project import Project

pytestmark = pytest.mark.integration

_CLINE_PKG = "packages/surfaces/cline"


def _has_cline_plugin() -> bool:
    import importlib

    try:
        importlib.import_module("ai_hats_cline")
        return True
    except ImportError:
        return False


def test_cline_session_leaves_project_root_clean(
    tmp_project: Project,
    requires_cline_auth,
    repo_root: Path,
) -> None:
    """Real headless cline session → no ``.cline/`` and no ``.gitignore`` mutation."""

    if not _has_cline_plugin():
        pytest.skip("ai-hats-cline plugin not installed in this venv")

    checkout_env = {
        "PYTHONPATH": os.pathsep.join(
            [checkout_pythonpath(repo_root), str(repo_root / _CLINE_PKG / "src")]
        )
    }

    tmp_project.run(
        "self",
        "init",
        "-r",
        "assistant",
        "-p",
        "cline",
        "--no-update",
        timeout=120,
        extra_env=checkout_env,
    ).expect_ok()

    result = tmp_project.run(
        "execute",
        "--batch",
        "-r",
        "assistant",
        "-p",
        "cline",
        "--prompt",
        "Reply with exactly: OK. No other text.",
        "--json",
        timeout=120,
        extra_env=checkout_env,
    ).expect_ok()

    data = json.loads(result.stdout.strip().splitlines()[-1])
    assert data["exit_code"] == 0, data

    proj = tmp_project.path

    # Clean-root invariant: ai-hats materializes cline artifacts into the
    # per-session cache (delivered via --config), never the project root.
    assert not (proj / ".cline").exists(), (
        f".cline/ leaked into the project root: {sorted(p.name for p in proj.iterdir())}"
    )

    # The old surface appended `.cline/skills/` + `.cline/plugins/` here.
    gitignore = proj / ".gitignore"
    if gitignore.exists():
        assert ".cline/" not in gitignore.read_text(), (
            "ai-hats mutated the project .gitignore with .cline/ entries"
        )
