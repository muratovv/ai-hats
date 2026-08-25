"""A pre-ADR-0018 out-of-tree surface still works, and says what it cannot do (HATS-1207 R4).

``ai_hats.providers`` is a published entry point, so a third-party surface may
implement only ``build_session_prompt`` and know nothing about categories or
``SessionPolicy``. Routing such a provider through the builder would not raise —
``build_category_artifact`` no-ops — it would hand the session an EMPTY prompt.
Degrade to the legacy call, and when a policy is set that the legacy path cannot
honour, warn instead of dropping it quietly.
"""

from __future__ import annotations

from pathlib import Path

from ai_hats.surfaces import Provider
from ai_hats.session_artifacts import BuiltArtifacts, RunMode, SessionPolicy
from ai_hats.surfaces.claude.provider import ClaudeProvider


class LegacySurface(Provider):
    """What an out-of-tree provider looked like before ADR-0018."""

    name = "legacy"

    def get_cli_command(self) -> list[str]:
        return ["legacy-cli"]

    def get_env(self, session_dir: Path, project_dir: Path) -> dict[str, str]:
        return {}

    def rules_dir(self, project_dir: Path) -> Path:
        return project_dir / ".legacy" / "rules"

    def system_prompt_path(self, project_dir: Path) -> Path:
        return project_dir / "LEGACY.md"

    def build_system_prompt(self, result) -> str:
        return f"LEGACY PROMPT for {result.name}"

    def build_session_prompt(self, project_dir, result, session_id):
        return (["--prompt", self.build_system_prompt(result)], {}, "meta")


def test_legacy_surface_is_detected_as_not_category_aware():
    assert LegacySurface().handles_artifact_categories() is False
    assert ClaudeProvider().handles_artifact_categories() is True


def test_routing_a_legacy_surface_through_the_builder_would_deliver_nothing(tmp_path: Path):
    """The failure this guard exists to prevent — silent, not loud."""
    from ai_hats_core import CompositionResult

    result = CompositionResult(name="r", priorities=[], rules=[], skills=[], injections=[])

    artifacts = LegacySurface().build_session_artifacts(
        tmp_path, result, "sid-1", run_mode=RunMode.HITL, artifacts=BuiltArtifacts()
    )

    assert artifacts.cli_args == []
    assert artifacts.full_content is None


def test_the_legacy_entry_point_still_carries_the_role(tmp_path: Path):
    from ai_hats_core import CompositionResult

    result = CompositionResult(name="r", priorities=[], rules=[], skills=[], injections=[])

    args, env, meta = LegacySurface().build_session_prompt(tmp_path, result, "sid-1")

    assert "LEGACY PROMPT for r" in args
    assert meta == "meta"
    assert env == {}


def test_a_non_default_policy_is_not_silently_dropped():
    """The notice fires on inequality with the default — the condition WrapRunner uses."""
    assert SessionPolicy(context=False) != SessionPolicy()
    assert SessionPolicy(hooks=False) != SessionPolicy()
    assert SessionPolicy() == SessionPolicy()
