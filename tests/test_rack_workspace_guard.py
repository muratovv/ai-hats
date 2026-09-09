"""HATS-839 on the integrator facade: no phantom tracker from a stray root.

The rack has its own validating resolver (``ai_hats_rack.resolver.resolve_root``),
so ``rack create`` outside a project refuses. This facade does NOT go through it —
its callers resolve the project with ``cli/_helpers._project_dir``, which falls
back to a bare cwd — so the HATS-839 creator guards the write path here instead
(HATS-1264).
"""

from __future__ import annotations

from ai_hats_core.layout import ProjectLayout

import pytest

from ai_hats.rack_workspace import NotAnAiHatsProjectError
from ai_hats.paths.constants import ENV_AI_HATS_DIR, PROJECT_CONFIG
from ai_hats.rack_workspace import create_proposal, ensure_backlog, rack_workspace


@pytest.fixture(autouse=True)
def _no_env_optin(monkeypatch):
    """AI_HATS_DIR is an explicit opt-in — unset it so the marker check is what bites."""
    monkeypatch.delenv(ENV_AI_HATS_DIR, raising=False)


def test_ensure_backlog_refuses_a_stray_root(tmp_path):
    """`reflect issue` in a non-project dir must not bootstrap `.agent/`."""
    stray = tmp_path / "stray"  # no .agent/, no ai-hats.yaml, no env opt-in
    stray.mkdir()

    with pytest.raises(NotAnAiHatsProjectError):
        ensure_backlog(ProjectLayout.at(stray), "hypotheses")

    assert not (stray / ".agent").exists(), "phantom tracker bootstrapped at a stray root"


def test_ensure_backlog_seeds_an_onboarded_project(tmp_path):
    """Complement: a real project still gets its catalog seeded (and is idempotent)."""
    (tmp_path / PROJECT_CONFIG).write_text("schema_version: 4\nprovider: claude\n")
    catalog = tmp_path / ".agent" / "ai-hats" / "tracker" / "backlog" / "hypotheses"

    ensure_backlog(ProjectLayout.at(tmp_path), "hypotheses")
    assert (catalog / "backlog.yaml").is_file()

    before = (catalog / "backlog.yaml").read_text()
    ensure_backlog(ProjectLayout.at(tmp_path), "hypotheses")
    assert (catalog / "backlog.yaml").read_text() == before


def test_card_create_cannot_reach_its_mkdir_on_a_stray_root(tmp_path):
    """The sibling write path is safe for a different reason — keep it that way.

    ``_create_card`` mkdirs with ``parents=True``, but ``instance_for`` refuses an
    unmounted prefix first, so a stray root never reaches it. Pinning that ordering
    here: a future change that mounts catalogs eagerly would open a second hole.
    """
    stray = tmp_path / "stray"
    stray.mkdir()

    with pytest.raises(Exception, match="no backlog for id prefix"):
        create_proposal(
            rack_workspace(ProjectLayout.at(stray)),
            title="t",
            category="process",
            target="x",
            description="d",
            rationale="r",
        )

    assert not (stray / ".agent").exists()
