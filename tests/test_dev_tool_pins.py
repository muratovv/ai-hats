"""A dev tool whose version decides a gate's answer must have ONE declared version.

HATS-1607: `ruff>=0.4` had no ceiling and lived in two places, so the format
gate answered by the age of the venv running it — 0.15.22 said 755 files and
rc=0 where 0.16.2 said 769 and, on the whole tree, rc=1.

`tests/test_gate_entrypoint_parity.py` cannot cover this: it deliberately skips
install lines (a runner installed is not a runner invoked), so it guards who
INVOKES a gate, never who DECLARES its tool version.
"""

from __future__ import annotations

import importlib.metadata
import re
import tomllib
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"

# Tools whose version changes a gate's verdict, so a second literal must match
# the dev-extras pin. Add a row when a tool earns that property — not for every
# dev dependency (HATS-1607 measured the divergence for ruff alone).
PINNED_TOOLS = ("ruff",)


def dev_extras_pin(tool: str) -> str:
    """The tool's requirement string as declared in [project.optional-dependencies].dev."""
    dev = tomllib.loads(PYPROJECT.read_text())["project"]["optional-dependencies"]["dev"]
    for req in dev:
        if re.match(rf"{re.escape(tool)}\s*[=<>!~]", req):
            return req.strip()
    pytest.fail(f"{tool} is not declared in the dev extras of {PYPROJECT.name}")


def workflow_pins(tool: str) -> list[str]:
    """Every `<tool><specifier>` literal appearing in a ci.yml `run:` body."""
    pattern = re.compile(rf"{re.escape(tool)}\s*[=<>!~]=?[^\"'\s]*")
    found = []
    for job in (yaml.safe_load(CI_WORKFLOW.read_text()).get("jobs") or {}).values():
        for step in job.get("steps") or []:
            found.extend(pattern.findall(str(step.get("run", ""))))
    return found


@pytest.mark.parametrize("tool", PINNED_TOOLS)
def test_the_dev_extras_pin_is_exact(tool: str):
    """A floor (`>=`) is what let two venvs disagree — the pin must name one version."""
    pin = dev_extras_pin(tool)
    assert "==" in pin, f"{pin!r} is a floor, not a pin — two venvs can run different {tool}s"


@pytest.mark.parametrize("tool", PINNED_TOOLS)
def test_ci_repeats_the_pin_verbatim(tool: str):
    """ci.yml may carry its own literal (the lint job installs only ruff), but not its own version."""
    pin = dev_extras_pin(tool)
    pins = workflow_pins(tool)
    assert pins, f"expected {CI_WORKFLOW.name} to install {tool}; found no literal"
    assert set(pins) == {pin}, (
        f"{CI_WORKFLOW.name} declares {sorted(set(pins))}, dev extras say {pin!r}"
    )


@pytest.mark.parametrize("tool", PINNED_TOOLS)
def test_the_installed_version_is_the_pinned_one(tool: str):
    """A declared pin nobody installed is the same defect from the other side.

    Bumping the pin without reinstalling leaves this venv on the old tool: the
    gate stays green locally and CI — which installs from the pin — disagrees.
    """
    pin = dev_extras_pin(tool)
    declared = pin.split("==", 1)[1].strip()
    installed = importlib.metadata.version(tool)
    assert installed == declared, (
        f"{tool} {installed} is installed, {declared} is pinned — "
        f"reinstall the dev extras (`uv pip install -e '.[dev]'`) before trusting a gate"
    )
