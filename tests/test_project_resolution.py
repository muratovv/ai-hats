"""Live-defect pins for the project-resolution slice (HATS-1606).

Each xfail(strict) documents a reproduced defect and names the step that turns
it green — an accidental pass is a signal, not a bonus.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_hats.config.project import ProjectConfig, ProjectConfigError


@pytest.fixture()
def future_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An onboarded project whose config schema is from the future."""
    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    monkeypatch.delenv("AI_HATS_PROJECT_DIR", raising=False)
    (tmp_path / ".agent" / "ai-hats").mkdir(parents=True)
    (tmp_path / "ai-hats.yaml").write_text("schema_version: 99\nai_hats_dir: .agent/custom\n")
    return tmp_path


def test_sanctioned_reader_refuses_future_schema(future_project: Path) -> None:
    """Positive control: fail-loud-on-newer works where it is implemented."""
    with pytest.raises(ProjectConfigError):
        ProjectConfig.from_yaml(future_project / "ai-hats.yaml")


@pytest.mark.xfail(
    strict=True,
    reason="HATS-1606 step 5: the raw peek bypasses fail-loud until the one reader owns it",
)
def test_raw_config_peek_refuses_future_schema_too(future_project: Path) -> None:
    """The bypass: the tracker path is taken from a config the system just
    declared unreadable — ai_hats_dir() must refuse exactly like from_yaml."""
    from ai_hats.paths import ai_hats_dir

    with pytest.raises(ProjectConfigError):
        ai_hats_dir(future_project)
