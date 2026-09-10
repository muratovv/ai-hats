"""``-p`` reaches the Automate runner on every batch surface (HATS-1218).

Pipeline-integration layer: the CLI, the compose seam and the ``execute``
pipeline run for real; only the runner boundary is stubbed (``mock_runners``),
so these assert what ``SubAgentRunner`` was actually constructed with.

Pre-fix ``build_composition_payload`` hard-read ``cfg.provider`` whenever
``interactive=False``, so ``execute -p X --batch`` ran the configured surface
and ``ai-hats agent`` had no ``-p`` at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from ai_hats import surface_registry as prov
from ai_hats.cli import main
from ai_hats.surfaces import Surface
from ai_hats.surface_registry import register_surface

# The project fixture configures ``claude``; an override must beat it.
OVERRIDE = "stub-surface"


class _StubProvider(Surface):
    @property
    def name(self) -> str:
        return OVERRIDE

    def system_prompt_path(self, layout) -> Path:
        return layout.root / "STUB.md"

    def rules_dir(self, session_dir: Path) -> Path:
        return session_dir / "rules"

    def build_system_prompt(self, result) -> str:  # noqa: ANN001
        return "stub-prompt"

    def get_cli_command(self, args: list[str] | None = None) -> list[str]:
        return ["stub-cli", *(args or [])]

    def get_env(self, session_dir: Path, layout) -> dict[str, str]:
        return {}


@pytest.fixture
def stub_provider():
    """Register a second surface, so "override wins" is observable at all."""
    register_surface(OVERRIDE, _StubProvider)
    yield OVERRIDE
    prov._PROVIDER_REGISTRY.pop(OVERRIDE, None)


def _launched_provider(captured: dict) -> str:
    assert len(captured["sub_calls"]) == 1, captured["sub_calls"]
    return captured["sub_calls"][0]["payload"].provider.name


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param(
            ["execute", "--role", "session-reviewer", "--batch", "-p", OVERRIDE],
            id="execute--batch",
        ),
        pytest.param(
            ["agent", "session-reviewer", "--task", "ping", "-p", OVERRIDE],
            id="agent",
        ),
    ],
)
def test_batch_surfaces_honour_provider_override(
    project_dir: Path,
    mock_runners,
    stub_provider,
    argv,
):
    """R2 + R3: both batch entry-points launch the surface ``-p`` names."""
    res = CliRunner().invoke(main, argv)
    assert res.exit_code == 0, res.output
    assert _launched_provider(mock_runners) == OVERRIDE


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param(
            ["execute", "--role", "session-reviewer", "--batch"],
            id="execute--batch",
        ),
        pytest.param(
            ["agent", "session-reviewer", "--task", "ping"],
            id="agent",
        ),
    ],
)
def test_batch_surfaces_fall_back_to_configured_provider(
    project_dir: Path,
    mock_runners,
    argv,
):
    """The other half of R4: no override still resolves ``ai-hats.yaml``."""
    res = CliRunner().invoke(main, argv)
    assert res.exit_code == 0, res.output
    assert _launched_provider(mock_runners) == "claude"
