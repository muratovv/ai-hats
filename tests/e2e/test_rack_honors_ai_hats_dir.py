"""e2e (HATS-1471)

flow:   a developer running rack commands with an explicit AI_HATS_DIR environment
        variable pointing to a sandbox
cmds:
    rack hyp create "sandbox hyp" --hypothesis "h" --expected-outcome "e" --json
expect: card artifacts are created inside the directory specified by AI_HATS_DIR and
        the current project directory remains unmodified
why:    rack must respect explicit AI_HATS_DIR overrides to allow sandboxed operation
        without polluting project repositories
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.rack]

REPO_ROOT = Path(__file__).resolve().parents[2]


def _snapshot(root: Path) -> dict[str, str]:
    """Capture file paths AND sha256 content hashes under root to verify byte-identity."""
    snapshot: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root))
        if p.is_file():
            snapshot[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
        else:
            snapshot[rel] = "dir"
    return snapshot


def _rack(
    *args: str, cwd: Path, extra_env: dict[str, str | None] | None = None
) -> subprocess.CompletedProcess[str]:
    from _helpers.env import checkout_pythonpath

    env = dict(os.environ)
    env["PYTHONPATH"] = checkout_pythonpath(REPO_ROOT)
    env.pop("RACK_TASKS_DIR", None)
    if extra_env:
        for k, v in extra_env.items():
            if v is None:
                env.pop(k, None)
            else:
                env[k] = v
    return subprocess.run(
        [sys.executable, "-m", "ai_hats_rack", *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_rack_hyp_create_honors_ai_hats_dir_sandbox_write(tmp_path):
    """Reproduce incident verbatim (HATS-1471): env -u AI_HATS_PROJECT_DIR AI_HATS_DIR=<sandbox> rack hyp create ...
    from a project directory.
    """
    from ai_hats_rack.definition import packaged_definition_source

    main_proj = tmp_path / "main"
    main_proj.mkdir()
    (main_proj / ".agent").mkdir()

    sbx_dir = tmp_path / "sandbox"
    sbx_agent = sbx_dir / ".agent" / "ai-hats"
    sbx_tasks = sbx_agent / "tracker" / "backlog" / "tasks"
    sbx_tasks.mkdir(parents=True)
    sbx_hyp = sbx_agent / "tracker" / "hypotheses"
    sbx_hyp.mkdir(parents=True)
    (sbx_hyp / "backlog.yaml").write_text(
        packaged_definition_source("hypotheses"), encoding="utf-8"
    )

    main_before = _snapshot(main_proj)

    # env -u AI_HATS_PROJECT_DIR AI_HATS_DIR=<sandbox>
    env: dict[str, str | None] = {
        "AI_HATS_DIR": str(sbx_agent),
        "AI_HATS_PROJECT_DIR": None,
    }

    res = _rack(
        "hyp",
        "create",
        "sandbox hyp",
        "--hypothesis",
        "h",
        "--expected-outcome",
        "e",
        "--json",
        cwd=main_proj,
        extra_env=env,
    )

    assert res.returncode == 0, res.stdout + res.stderr

    # Main tracker snapshot is byte-identical (paths and contents)
    assert _snapshot(main_proj) == main_before

    # Card exists in sandbox hypotheses catalog
    sbx_cards = list((sbx_hyp).glob("HYP-*/task.yaml")) + list((sbx_hyp).glob("HYP-*/card.yaml"))
    assert sbx_cards, f"Card was not created in sandbox at {sbx_hyp}"


def test_rack_foreign_pin_refuses_write_and_creates_nothing(tmp_path):
    main_proj = tmp_path / "main"
    main_proj.mkdir()
    (main_proj / ".agent").mkdir()

    foreign_proj = tmp_path / "foreign"
    foreign_proj.mkdir()
    (foreign_proj / ".agent").mkdir()

    sbx_dir = tmp_path / "sandbox"
    sbx_agent = sbx_dir / ".agent" / "ai-hats"
    sbx_agent.mkdir(parents=True)

    main_before = _snapshot(main_proj)
    foreign_before = _snapshot(foreign_proj)
    sbx_before = _snapshot(sbx_dir)

    env: dict[str, str | None] = {
        "AI_HATS_DIR": str(sbx_agent),
        "AI_HATS_PROJECT_DIR": str(foreign_proj),
    }

    res = _rack(
        "create",
        "foreign task",
        "--id",
        "HATS-200",
        "--json",
        cwd=main_proj,
        extra_env=env,
    )
    assert res.returncode == 1, res.stdout + res.stderr
    assert "foreign_project_pin" in res.stdout or "foreign_project_pin" in res.stderr

    # Nothing created anywhere
    assert _snapshot(main_proj) == main_before
    assert _snapshot(foreign_proj) == foreign_before
    assert _snapshot(sbx_dir) == sbx_before
