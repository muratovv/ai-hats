"""A surface without a planner is refused on every road into a session (ADR-0036 D2).

``ai_hats.providers`` is a published entry point, so a third-party surface may
predate ``Surface.plan``. It used to be built through ``build_session_prompt``
and handed a session it could not fully deliver; now the runners, ``--dry-run``
and ``show-prompt`` all pass ``plan_session``, which refuses it before anything
is written — loudly, with the member to implement named.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from ai_hats_core.layout import ProjectLayout

from ai_hats.materialization import describe_mkdir
from ai_hats.session_artifacts import RunMode, SessionPolicy
from ai_hats.session_plan import plan_session, plans, preview, probe_host
from ai_hats.surfaces import Surface
from ai_hats.surfaces.plan import (
    CompositionPlan,
    Hooks,
    Launch,
    MaterializationPlan,
    Prompt,
    PromptBlock,
    PromptMember,
)


class LegacySurface(Surface):
    """What an out-of-tree provider looked like before ``Surface.plan``."""

    name = "legacy"

    def get_cli_command(self, args=None) -> list[str]:
        return ["legacy-cli", *(args or [])]

    def get_env(self, session_dir: Path, layout) -> dict[str, str]:
        return {}

    def rules_dir(self, project_dir: Path) -> Path:
        return project_dir / ".legacy" / "rules"

    def system_prompt_path(self, layout) -> Path:
        return layout.root / "LEGACY.md"

    def build_system_prompt(self, result) -> str:
        return f"LEGACY PROMPT for {result.name}"

    def build_session_prompt(self, layout, result, session_id):
        return (["--prompt", self.build_system_prompt(result)], {}, "meta")


class PlanningSurface(LegacySurface):
    """The same surface once it has written its planner — the positive control."""

    def plan(self, composition, *, run_mode, policy, root, layout, host):
        return MaterializationPlan(
            composition=composition,
            prompt=composition.prompt,
            surface=self.name,
            run_mode=RunMode(run_mode),
            policy=policy,
            root=root,
            entries=(describe_mkdir(root),),
            env={},
            launch=Launch(args=("--legacy",), sdk_options=None),
        )


def _composition() -> CompositionPlan:
    return CompositionPlan(
        identity="r",
        prompt=Prompt((PromptBlock(None, (PromptMember("r::prompt", "# r\n", None),)),)),
        skills=(),
        hooks=Hooks((), ()),
        trace=(),
    )


def _payload(surface: Surface, layout: ProjectLayout):
    return SimpleNamespace(
        provider=surface,
        layout=layout,
        plan=_composition(),
        result=MagicMock(name="result"),
        effective_role="r",
        diagnostics=(),
    )


def test_plans_tells_a_planner_from_a_surface_without_one():
    assert plans(LegacySurface()) is False
    assert plans(PlanningSurface()) is True


def test_the_runners_road_refuses_a_surface_without_a_planner(tmp_path: Path):
    """``plan_session`` is what both runners call first — the refusal names the fix."""
    layout = ProjectLayout.at(tmp_path / "proj")
    root = tmp_path / "sessions" / "s1"

    def plan_with(surface):
        return plan_session(
            _composition(),
            surface,
            run_mode=RunMode.HITL,
            policy=SessionPolicy(),
            root=root,
            layout=layout,
            host=probe_host(surface=surface),
        )

    with pytest.raises(
        RuntimeError, match="'legacy' does not plan a session; implement Surface.plan"
    ):
        plan_with(LegacySurface())
    assert not root.exists(), "refused before anything is written"
    assert plan_with(PlanningSurface()).launch.args == ("--legacy",)


def test_the_dry_run_refuses_a_surface_without_a_planner(tmp_path: Path, monkeypatch):
    layout = ProjectLayout.at(tmp_path / "proj")
    planning = PlanningSurface()
    surfaces = iter((LegacySurface(), planning))
    monkeypatch.setattr(
        "ai_hats.composition_seam.build_preview_payload",
        lambda *a, **kw: _payload(next(surfaces), layout),
    )
    # The launch looks the surface up by the plan's name; the stub is not an entry point.
    monkeypatch.setattr("ai_hats.surface_registry.get_surface", lambda name: planning)

    with pytest.raises(RuntimeError, match="does not plan a session"):
        preview(layout, role="r", provider="legacy", run_mode=RunMode.HITL)
    assert not layout.cache.root.exists(), "a refused dry-run leaves no cache behind"

    shown = preview(layout, role="r", provider="legacy", run_mode=RunMode.HITL)
    assert shown.record["launch"] == ["legacy-cli", "--legacy"]


def test_show_prompt_refuses_a_surface_without_a_planner(tmp_path: Path):
    from ai_hats.pipeline.steps.materialize import MaterializeSystemPrompt

    layout = ProjectLayout.at(tmp_path / "proj")

    with pytest.raises(RuntimeError, match="does not plan a session"):
        MaterializeSystemPrompt().run(composition=_payload(LegacySurface(), layout))

    out = MaterializeSystemPrompt().run(composition=_payload(PlanningSurface(), layout))
    assert out["system_prompt_text"] == "# r\n"
