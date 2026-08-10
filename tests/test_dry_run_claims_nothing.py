"""A dry-run claims no resource a launch would claim (HATS-1554).

``tests/test_dry_run_guarantee.py`` fingerprints files, so "a dry-run does
nothing" was only ever proven about the filesystem. ``ClineProvider.get_env``
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
from ai_hats.providers import get_provider, provider_names

REPO_ROOT = Path(__file__).resolve().parent.parent
LIBRARY_DIR = REPO_ROOT / "packages" / "ai-hats-library" / "src" / "ai_hats_library"


@pytest.fixture
def cline_project(tmp_path: Path, monkeypatch) -> Path:
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


@pytest.mark.parametrize("report_of", [dry_run_hitl, dry_run_automate])
def test_a_dry_run_binds_no_socket(cline_project: Path, no_sockets, report_of):
    report = report_of(cline_project)

    assert report.env["CLINE_HUB_PORT"] == AT_LAUNCH, (
        "the report must say the port is the launch's to pick, not invent one"
    )


def test_the_port_key_survives_into_the_report(cline_project: Path):
    """Purity must not be bought by dropping the key — that hides it instead."""
    assert "CLINE_HUB_PORT" in dry_run_hitl(cline_project).to_dict()["env_keys"]


def test_a_real_launch_claims_a_usable_port(cline_project: Path):
    """The sentinel is a report value; a launch still gets a bound-and-free port."""
    claimed = get_provider("cline").claim_launch_env(cline_project, cline_project)

    assert set(claimed) == {"CLINE_HUB_PORT"}
    assert 1024 < int(claimed["CLINE_HUB_PORT"]) <= 65535


@pytest.mark.parametrize("provider_name", sorted(provider_names()))
def test_what_a_launch_claims_is_a_key_the_report_already_names(
    provider_name: str, tmp_path: Path
):
    """Else ``env_keys`` would depend on the mode, which is the same defect moved.

    Every registered provider, not just cline: the invariant belongs to the hook,
    and a surface added later inherits the trap, not the guarantee.
    """
    provider = get_provider(provider_name)

    claimed = set(provider.claim_launch_env(tmp_path, tmp_path))
    reported = set(provider.get_env(tmp_path, tmp_path))

    assert claimed <= reported, f"{provider_name} claims keys its report never names"
