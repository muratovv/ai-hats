"""HATS-1399 — the consumer side of version skew.

The producer gate refuses a source change that does not outrank the published
wheel. Nothing checked the other direction until now: a pin free to resolve an
older wheel than the code it is imported by.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from packaging.version import Version

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "check_dependency_floor.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_dependency_floor", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mod = _load()


# --- declared_floor --------------------------------------------------------


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("ai-hats-core>=0.6.0", "0.6.0"),
        ("ai-hats-core==0.6.0", "0.6.0"),
        ("ai-hats-core~=0.6.0", "0.6.0"),
        ("ai-hats-core==0.6.*", "0.6"),
        ("ai-hats-core>=0.5.0,<1.0", "0.5.0"),
        ("ai-hats-core>=0.5.0; python_version>='3.11'", "0.5.0"),
    ],
)
def test_declared_floor_reads_the_lower_bound(spec, expected):
    assert mod.declared_floor(spec) == Version(expected)


def test_a_pin_with_no_lower_bound_has_no_floor():
    assert mod.declared_floor("ai-hats-core") is None
    assert mod.declared_floor("ai-hats-core<2.0") is None


# --- violations ------------------------------------------------------------

VERSIONS = {"ai-hats-core": Version("0.6.1"), "ai-hats-observe": Version("0.5.0")}


def _consumer(*specs, optional=None):
    project = {"dependencies": list(specs)}
    if optional:
        project["optional-dependencies"] = optional
    return {"pyproject.toml": {"project": project}}


def test_a_floor_below_the_package_version_is_a_violation():
    found = mod.violations(_consumer("ai-hats-core>=0.2.0"), VERSIONS)

    assert len(found) == 1
    assert found[0].package == "ai-hats-core"
    assert found[0].floor == Version("0.2.0")
    assert found[0].version == Version("0.6.1")
    assert ">=0.6.1" in str(found[0])


def test_the_exact_hats_1397_shape_is_a_violation():
    """The defect that shipped: observe pinned at 0.3.0, is_measured added in 0.5.0."""
    found = mod.violations(_consumer("ai-hats-observe>=0.3.0"), VERSIONS)

    assert [v.package for v in found] == ["ai-hats-observe"]


def test_a_floor_at_the_package_version_passes():
    assert mod.violations(_consumer("ai-hats-core>=0.6.1"), VERSIONS) == []


def test_a_floor_above_the_package_version_passes():
    assert mod.violations(_consumer("ai-hats-core>=0.7.0"), VERSIONS) == []


def test_a_third_party_pin_is_not_our_business():
    assert mod.violations(_consumer("click>=8.0"), VERSIONS) == []


def test_an_optional_dependency_group_is_checked_too():
    consumers = _consumer("click>=8.0", optional={"dev": ["ai-hats-core>=0.1.0"]})

    assert [v.package for v in mod.violations(consumers, VERSIONS)] == ["ai-hats-core"]


def test_a_pin_without_a_floor_cannot_be_judged():
    assert mod.violations(_consumer("ai-hats-core"), VERSIONS) == []


# --- the real repo ---------------------------------------------------------


def test_workspace_versions_finds_every_package():
    found = mod.workspace_versions(REPO_ROOT)

    # HATS-1826 folded the surfaces into src/ai_hats/surfaces, so packages/ is flat
    # again and every member is named here rather than sampled.
    assert set(found) == {
        "ai-hats-core",
        "ai-hats-library",
        "ai-hats-observe",
        "ai-hats-rack",
        "ai-hats-wt",
    }, f"workspace packages not discovered: {sorted(found)}"


def test_every_pin_in_this_repo_is_at_or_above_its_package_version():
    versions = mod.workspace_versions(REPO_ROOT)
    found = mod.violations(mod.collect_consumers(REPO_ROOT), versions)

    assert not found, "stale dependency floors:\n" + "\n".join(str(v) for v in found)
