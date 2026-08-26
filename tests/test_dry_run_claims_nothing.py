"""A dry-run claims no resource a launch would claim (HATS-1554).

``tests/test_dry_run_guarantee.py`` fingerprints files, so "a dry-run does
nothing" was only ever proven about the filesystem. ``ClineSurface.get_env``
bound a real socket to pick a hub port, and both ``--dry-run`` and the
materialization port were blind to it — the report then named a port the launch
would never use, because the launch allocates its own.
"""  # comment-length: allow — the gap this closes is exactly what the old guarantee missed

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from ai_hats.assembler import Assembler
from ai_hats.dry_run import AT_LAUNCH, dry_run_automate, dry_run_hitl
from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG
from ai_hats.surface_registry import get_surface, surface_names

REPO_ROOT = Path(__file__).resolve().parent.parent
LIBRARY_DIR = REPO_ROOT / "packages" / "ai-hats-library" / "src" / "ai_hats_library"


SURFACES = ["claude", "agy", "cline"]


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    proj = tmp_path / "proj"
    proj.mkdir()
    ProjectConfig(
        provider="cline",
        library_paths=[str(LIBRARY_DIR)],
        ai_hats_dir=".agent/ai-hats",
        active_role="maintainer",
        default_role="maintainer",
    ).save(proj / PROJECT_CONFIG)
    asm = Assembler(proj, library_paths=[LIBRARY_DIR])
    asm.init()
    asm.set_role("maintainer", provider_name="cline")
    monkeypatch.chdir(proj)
    monkeypatch.setenv("AI_HATS_NO_UPDATE_CHECK", "1")
    return proj


@pytest.fixture
def no_sockets(monkeypatch):
    """Any socket built while this is active fails the test where it happens."""

    def _refuse(*args, **kwargs):
        raise AssertionError("a dry-run opened a socket")

    monkeypatch.setattr(socket, "socket", _refuse)


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("report_of", [dry_run_hitl, dry_run_automate])
def test_a_dry_run_binds_no_socket(project: Path, no_sockets, report_of, surface: str):
    """Every surface, both modes — the invariant belongs to reporting, not to cline."""
    report_of(project, provider=surface)


@pytest.mark.parametrize("report_of", [dry_run_hitl, dry_run_automate])
def test_the_reported_hub_port_is_the_launchs_to_pick(project: Path, report_of):
    report = report_of(project, provider="cline")

    assert report.env["CLINE_HUB_PORT"] == AT_LAUNCH, (
        "the report must say the port is the launch's to pick, not invent one"
    )


def test_the_port_key_survives_into_the_report(project: Path):
    """Purity must not be bought by dropping the key — that hides it instead."""
    env_keys = dry_run_hitl(project, provider="cline").to_dict()["env_keys"]

    assert "CLINE_HUB_PORT" in env_keys


def test_a_real_launch_claims_a_usable_port(project: Path):
    """The sentinel is a report value; a launch still gets a bound-and-free port."""
    claimed = get_surface("cline").claim_launch_env(project, project)

    assert set(claimed) == {"CLINE_HUB_PORT"}
    assert 1024 < int(claimed["CLINE_HUB_PORT"]) <= 65535


@pytest.mark.parametrize("provider_name", sorted(surface_names()))
def test_what_a_launch_claims_is_a_key_the_report_already_names(provider_name: str, tmp_path: Path):
    """Else ``env_keys`` would depend on the mode, which is the same defect moved.

    Every registered provider, not just cline: the invariant belongs to the hook,
    and a surface added later inherits the trap, not the guarantee.
    """
    provider = get_surface(provider_name)

    claimed = set(provider.claim_launch_env(tmp_path, tmp_path))
    reported = set(provider.get_env(tmp_path, tmp_path))

    assert claimed <= reported, f"{provider_name} claims keys its report never names"
