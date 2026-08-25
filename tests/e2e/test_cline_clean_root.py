"""e2e (HATS-1171)

flow:   a developer running a batch session under cline provider
cmds:
    ai-hats execute --batch -r assistant -p cline --prompt "Reply OK" --json
expect: cline artifacts are written to session cache without leaking .cline/ into
        project root
why:    without isolated session caching, surface providers pollute project roots with
        ephemeral config folders
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from _helpers.env import checkout_pythonpath
from _helpers.project import Project

pytestmark = pytest.mark.integration


def _has_cline_plugin() -> bool:
    import importlib

    try:
        importlib.import_module("ai_hats.surfaces.cline")
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

    checkout_env = {"PYTHONPATH": checkout_pythonpath(repo_root)}

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
