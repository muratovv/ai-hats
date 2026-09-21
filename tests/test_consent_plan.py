"""The consent wrapper as a planner: entries and environment from the plan's
external hooks and the host, refusals before any write (ADR-0036 D2)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from ai_hats_core.layout import ProjectLayout

from ai_hats.consent_wrapper import CONFIG_ENV, ConsentPolicyError, plan_consent, policy_of
from ai_hats.env import ENV_SESSION_CACHE_DIR
from ai_hats.materialization import WriteKind, describe_mkdir
from ai_hats.session_artifacts import RunMode, SessionPolicy
from ai_hats.surfaces.claude.provider import ClaudeSurface
from ai_hats.surfaces.codex.provider import CodexSurface
from ai_hats.surfaces.plan import (
    CompositionPlan,
    ExternalHook,
    Hooks,
    Host,
    Launch,
    MaterializationPlan,
    Prompt,
    PromptBlock,
    PromptMember,
)


def _consent(operation: str, at: str) -> ExternalHook:
    return ExternalHook("consent_gate", operation, at, None, None, "trait-agent")


def _plan(tmp_path: Path, *hooks: ExternalHook, surface: str = "claude") -> MaterializationPlan:
    composition = CompositionPlan(
        identity="r",
        prompt=Prompt((PromptBlock(None, (PromptMember("r::prompt", "# r\n", None),)),)),
        skills=(),
        hooks=Hooks((), hooks),
        trace=(),
    )
    root = tmp_path / "sessions" / "s1"
    return MaterializationPlan(
        composition=composition,
        prompt=composition.prompt,
        surface=surface,
        run_mode=RunMode.HITL,
        policy=SessionPolicy(),
        root=root,
        entries=(describe_mkdir(root),),
        env={"PATH": "/usr/bin", ENV_SESSION_CACHE_DIR: str(root)},
        launch=Launch(args=("--settings", str(root / "settings.json")), sdk_options=None),
    )


def _host(tmp_path: Path, *names: str) -> Host:
    bins = tmp_path / "bin"
    bins.mkdir(exist_ok=True)
    return Host(
        python=Path("/opt/py/bin/python3"),
        path=str(bins),
        commands={name: bins / name for name in names},
    )


LAYOUT_ROOT = Path("/proj")


def test_the_policy_is_the_consent_rows_grouped_by_operation():
    rows = (
        ExternalHook("git", None, "pre-commit", None, None, "skills::g"),
        _consent("rack.transition", "plan->execute"),
        _consent("rack.transition", "review->done"),
        _consent("wt.merge", "pre-merge"),
    )
    assert policy_of(rows) == {
        "rack.transition": ("plan->execute", "review->done"),
        "wt.merge": ("pre-merge",),
    }


@pytest.mark.parametrize(
    "selector, reason", [("plan-execute", "an arrow"), ("a->b->c", "exactly one")]
)
def test_a_selector_the_operation_cannot_read_is_refused_at_planning(selector, reason):
    with pytest.raises(ConsentPolicyError) as exc:
        policy_of((_consent("rack.transition", selector),))
    assert selector in str(exc.value) and reason in str(exc.value)


def test_a_consent_row_without_an_operation_is_refused():
    with pytest.raises(ConsentPolicyError, match="exactly one operation"):
        policy_of((ExternalHook("consent_gate", None, "review->done", None, None, "t"),))


def test_the_wrapper_is_three_entries_and_a_path_that_leads_with_them(tmp_path: Path):
    plan = _plan(tmp_path, _consent("rack.transition", "plan->execute"))
    host = _host(tmp_path, "rack", "ai-hats")

    armed = plan_consent(plan, ClaudeSurface(), ProjectLayout.at(LAYOUT_ROOT), host)

    home = plan.root / "consent-wrapper"
    added = armed.entries[len(plan.entries) :]
    assert [(e.kind, e.target) for e in added] == [
        (WriteKind.WRITE_TEXT, home / "config.json"),
        (WriteKind.WRITE_EXECUTABLE, home / "bin" / "consent"),
        (WriteKind.WRITE_EXECUTABLE, home / "bin" / "rack"),
    ]
    assert json.loads(added[0].content) == {
        "project_dir": str(LAYOUT_ROOT),
        "originals": {"rack": str(tmp_path / "bin" / "rack")},
        "policy": {"rack.transition": ["plan->execute"]},
    }
    assert all(e.content.startswith("#!/opt/py/bin/python3\n") for e in added[1:])
    assert armed.env["PATH"] == os.pathsep.join([str(home / "bin"), "/usr/bin"])
    assert armed.env[CONFIG_ENV] == str(home / "config.json")
    assert armed.launch == plan.launch and armed.composition == plan.composition


def test_a_role_that_declares_no_consent_leaves_the_plan_as_it_is(tmp_path: Path):
    plan = _plan(tmp_path)
    assert (
        plan_consent(plan, ClaudeSurface(), ProjectLayout.at(LAYOUT_ROOT), _host(tmp_path)) == plan
    )


def test_a_command_the_host_does_not_have_is_refused_before_planning_ends(tmp_path: Path):
    plan = _plan(tmp_path, _consent("rack.transition", "plan->execute"))
    with pytest.raises(RuntimeError, match="cannot wrap 'rack'"):
        plan_consent(plan, ClaudeSurface(), ProjectLayout.at(LAYOUT_ROOT), _host(tmp_path))


def test_a_command_that_is_itself_a_wrapper_is_refused(tmp_path: Path):
    plan = _plan(tmp_path, _consent("rack.transition", "plan->execute"))
    inherited = tmp_path / "old" / "consent-wrapper" / "bin" / "rack"
    host = Host(python=Path("/opt/py/bin/python3"), path="/usr/bin", commands={"rack": inherited})
    with pytest.raises(RuntimeError, match="is a consent wrapper"):
        plan_consent(plan, ClaudeSurface(), ProjectLayout.at(LAYOUT_ROOT), host)


def test_a_surface_without_command_wrappers_cannot_carry_the_role(tmp_path: Path):
    from ai_hats.surfaces.agy.provider import AgySurface

    plan = _plan(tmp_path, _consent("wt.merge", "pre-merge"), surface="agy")
    with pytest.raises(RuntimeError, match="cannot enforce"):
        plan_consent(plan, AgySurface(), ProjectLayout.at(LAYOUT_ROOT), _host(tmp_path, "ai-hats"))


def test_codex_gets_its_form_server_on_the_launch_line(tmp_path: Path):
    plan = _plan(tmp_path, _consent("rack.transition", "plan->execute"), surface="codex")
    host = _host(tmp_path, "rack")

    armed = plan_consent(plan, CodexSurface(), ProjectLayout.at(LAYOUT_ROOT), host)

    assert armed.launch.args[: len(plan.launch.args)] == plan.launch.args
    extra = armed.launch.args[len(plan.launch.args) :]
    assert extra and extra[0] == "-c" and any("mcp_servers" in a for a in extra)
