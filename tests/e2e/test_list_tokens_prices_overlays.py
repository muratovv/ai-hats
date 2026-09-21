"""e2e (HATS-2004)

flow:   a project shapes a shipped role through `customizations:` in ai-hats.yaml
        and asks what that role costs
cmds:
    ai-hats self init -p claude -r assistant
    ai-hats list tokens assistant --approx
expect: the table carries the trait the overlay added (its injection and its
        skill) and the overlay's own injection_append, with an Always-on footer
why:    `list tokens` walked the role's declared tree, so everything a project or
        a user-global customizations.yaml added was missing from the figure the
        skill-engineer injection tells the agent to quote; show-prompt composed
        with overlays and list tokens without, two answers to one question
"""
# comment-length: allow — the four-field catalog block, schema in gen_e2e_catalog.py

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.integration, pytest.mark.library]

OVERLAY_TRAIT = "dev::go-grpc"  # not in assistant's declared tree
OVERLAY_TRAIT_SKILL = "golang-grpc"
OVERLAY_SENTINEL = "## ZZ OVERLAY SENTINEL"


def _run(cmd, *, cwd, env, timeout):
    result = subprocess.run(
        [str(c) for c in cmd],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    assert result.returncode == 0, f"{cmd}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    return result


def test_list_tokens_prices_the_overlaid_role(shared_launcher, tmp_path: Path):
    launcher, base_env, _venv = shared_launcher
    env = {**base_env, "COLUMNS": "220"}  # keep the table from wrapping names
    env.pop("PYTHONPATH", None)

    proj = tmp_path / "proj"
    proj.mkdir()
    _run(
        [launcher, "self", "init", "-p", "claude", "-r", "assistant"],
        cwd=proj,
        env=env,
        timeout=120,
    )

    cfg_path = proj / "ai-hats.yaml"
    cfg = yaml.safe_load(cfg_path.read_text())
    cfg["customizations"] = {
        "assistant": {
            "add": {"traits": [OVERLAY_TRAIT]},
            "injection_append": OVERLAY_SENTINEL + "\n",
        }
    }
    cfg_path.write_text(yaml.safe_dump(cfg))

    tokens = _run(
        [launcher, "list", "tokens", "assistant", "--approx"], cwd=proj, env=env, timeout=60
    )
    out = tokens.stdout
    assert OVERLAY_TRAIT in out, f"the overlay-added trait's injection is not priced:\n{out}"
    assert OVERLAY_TRAIT_SKILL in out, f"the overlay-added trait's skill is not priced:\n{out}"
    assert "overrides::project" in out, f"the overlay's own injection_append is not priced:\n{out}"
    assert "Always-on" in out, f"no always-on figure to quote:\n{out}"
