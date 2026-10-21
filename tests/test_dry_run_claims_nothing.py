"""A dry-run claims no resource a launch would claim (HATS-1554).

``tests/test_dry_run_guarantee.py`` fingerprints files, so "a dry-run does
nothing" was only ever proven about the filesystem. ``ClineSurface.get_env``
bound a real socket to pick a hub port, and the report was blind to it — it
then named a port the launch would never use, because the launch allocates its
own. On the plan (ADR-0036 D4) a report launches with ``claim=False``.
"""  # comment-length: allow — the gap this closes is exactly what the old guarantee missed

from __future__ import annotations

import socket
from pathlib import Path

import pytest
from ai_hats_core.layout import ProjectLayout

from ai_hats.assembler import Assembler
from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG
from ai_hats.session_artifacts import AT_LAUNCH, RunMode, SessionPolicy
from ai_hats.session_plan import launch, plan_session, preview, probe_host
from ai_hats.surface_registry import get_surface, surface_names
from ai_hats.surfaces import LaunchFlags

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


#: agy wraps no command, so a HITL plan of the consent-declaring ``maintainer``
#: is refused before any socket could open (pinned in the stage acceptance).
CASES = [(RunMode.HITL, s) for s in SURFACES if s != "agy"] + [
    (RunMode.AUTOMATE, s) for s in SURFACES
]


@pytest.mark.parametrize(("run_mode", "surface"), CASES)
def test_a_dry_run_binds_no_socket(project: Path, no_sockets, run_mode: RunMode, surface: str):
    """Every surface, both modes — the invariant belongs to reporting, not to cline."""
    preview(ProjectLayout.at(project), role=None, provider=surface, run_mode=run_mode)


def _unclaimed_launch(project: Path, run_mode: RunMode):
    """The launch pair a report is built from: the same plan, ``claim=False``."""
    layout = ProjectLayout.at(project)
    surface = get_surface("cline")
    from ai_hats.composition_seam import build_preview_payload

    composition = build_preview_payload(project, role=None, provider="cline").plan
    plan = plan_session(
        composition,
        surface,
        run_mode=run_mode,
        policy=SessionPolicy(),
        root=layout.cache.session("dry-run"),
        layout=layout,
        host=probe_host(surface=surface),
    )
    flags = LaunchFlags(
        session_id="dry-run",
        session_dir=layout.cache.session("dry-run"),
        trace_path=AT_LAUNCH,
        root_pid=AT_LAUNCH,
        provider_session_id=AT_LAUNCH,
        claim=False,
    )
    return launch(plan, flags, layout=layout)


@pytest.mark.parametrize("run_mode", [RunMode.HITL, RunMode.AUTOMATE])
def test_the_reported_hub_port_is_the_launchs_to_pick(project: Path, run_mode: RunMode):
    launched = _unclaimed_launch(project, run_mode)

    assert launched.env["CLINE_HUB_PORT"] == AT_LAUNCH, (
        "the report must say the port is the launch's to pick, not invent one"
    )


def test_the_port_key_survives_into_the_report(project: Path):
    """Purity must not be bought by dropping the key — that hides it instead."""
    shown = preview(ProjectLayout.at(project), role=None, provider="cline", run_mode=RunMode.HITL)

    assert "CLINE_HUB_PORT" in shown.record["env_keys"]


def test_a_real_launch_claims_a_usable_port(project: Path):
    """The sentinel is a report value; a launch still gets a bound-and-free port."""
    claimed = get_surface("cline").claim_launch_env(project, ProjectLayout.at(project))

    assert set(claimed) == {"CLINE_HUB_PORT"}
    assert 1024 < int(claimed["CLINE_HUB_PORT"]) <= 65535


@pytest.mark.parametrize("provider_name", sorted(surface_names()))
def test_what_a_launch_claims_is_a_key_the_report_already_names(provider_name: str, tmp_path: Path):
    """Else ``env_keys`` would depend on the mode, which is the same defect moved.

    Every registered provider, not just cline: the invariant belongs to the hook,
    and a surface added later inherits the trap, not the guarantee.
    """
    provider = get_surface(provider_name)

    claimed = set(provider.claim_launch_env(tmp_path, ProjectLayout.at(tmp_path)))
    reported = set(provider.get_env(tmp_path, ProjectLayout.at(tmp_path)))

    assert claimed <= reported, f"{provider_name} claims keys its report never names"
