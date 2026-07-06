"""HATS-938: `self init` auto-seeds harness.channel:local on an editable host.

The seed is greenfield-only and follows the precedence flag → AI_HATS_INIT_SRC
(launcher-exported) → in-init `_is_editable_install()`. Non-editable hosts keep
the STABLE default; an existing harness block on re-init is never clobbered.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_hats.assembler import Assembler
from ai_hats.constants import ENV_AI_HATS_INIT_SRC
from ai_hats.models import Channel, ProjectConfig
from ai_hats.paths import PROJECT_CONFIG


def _harness(project: Path):
    return ProjectConfig.from_yaml(project / PROJECT_CONFIG).harness


@pytest.fixture(autouse=True)
def _no_init_src_env(monkeypatch):
    """Isolate from a real dev-machine AI_HATS_INIT_SRC leaking into tests."""
    monkeypatch.delenv(ENV_AI_HATS_INIT_SRC, raising=False)


def test_editable_host_greenfield_seeds_local(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(
        "ai_hats.cli.maintenance._is_editable_install",
        lambda: (True, "file:///Users/dev/ai-hats"),
    )

    Assembler(project).init(provider="claude")

    h = _harness(project)
    assert h.channel is Channel.LOCAL
    assert h.path == "/Users/dev/ai-hats"


def test_non_editable_host_keeps_stable(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(
        "ai_hats.cli.maintenance._is_editable_install", lambda: (False, None)
    )

    Assembler(project).init(provider="claude")

    assert _harness(project).channel is Channel.STABLE


def test_init_src_env_beats_in_init_detection(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    # Interpreter is NOT editable, but the launcher exported a source path.
    monkeypatch.setattr(
        "ai_hats.cli.maintenance._is_editable_install", lambda: (False, None)
    )
    monkeypatch.setenv(ENV_AI_HATS_INIT_SRC, "/monorepo/ai-hats")

    Assembler(project).init(provider="claude")

    h = _harness(project)
    assert h.channel is Channel.LOCAL
    assert h.path == "/monorepo/ai-hats"


def test_explicit_channel_flag_overrides_auto(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    # Editable host would auto-seed local; explicit --channel stable wins.
    monkeypatch.setattr(
        "ai_hats.cli.maintenance._is_editable_install",
        lambda: (True, "file:///Users/dev/ai-hats"),
    )

    Assembler(project).init(provider="claude", channel="stable")

    assert _harness(project).channel is Channel.STABLE


def test_reinit_does_not_clobber_existing_harness(tmp_path, monkeypatch):
    from ai_hats.migrations import latest_step
    from ai_hats.models import HarnessConfig

    project = tmp_path / "project"
    project.mkdir()
    ProjectConfig(
        provider="claude",
        harness=HarnessConfig(channel=Channel.LOCAL, path="."),
        migration_step=latest_step(),
    ).save(project / PROJECT_CONFIG)

    # A DIFFERENT editable source on re-init must not overwrite path=".".
    monkeypatch.setattr(
        "ai_hats.cli.maintenance._is_editable_install",
        lambda: (True, "file:///elsewhere/ai-hats"),
    )
    Assembler(project).init()

    h = _harness(project)
    assert h.channel is Channel.LOCAL
    assert h.path == "."
