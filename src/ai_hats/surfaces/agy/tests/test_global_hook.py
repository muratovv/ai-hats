"""The global dispatcher registration as the plan carries it: one merge outside
the session root, applied into whatever the person's settings file holds."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from ai_hats.materialization import WriteKind
from ai_hats.session_artifacts import RunMode, SessionPolicy
from ai_hats.surfaces.agy.global_hook import (
    DISPATCHER_COMMAND,
    DISPATCHER_EVENTS,
    MANAGED_DISPATCHER_TAG,
    desired_hooks,
    plan_global_hook,
)
from ai_hats.surfaces.plan import (
    CompositionPlan,
    EscapeUndeclared,
    Hooks,
    Launch,
    MaterializationPlan,
    Prompt,
    PromptBlock,
    PromptMember,
    apply,
    validate,
)


def _plan_with(entry, root: Path) -> MaterializationPlan:
    composition = CompositionPlan(
        identity="r",
        prompt=Prompt((PromptBlock(None, (PromptMember("r::prompt", "# r\n", None),)),)),
        skills=(),
        hooks=Hooks((), ()),
        trace=(),
    )
    return MaterializationPlan(
        composition=composition,
        prompt=composition.prompt,
        surface="agy",
        run_mode=RunMode.HITL,
        policy=SessionPolicy(),
        root=root,
        entries=(entry,),
        env={},
        launch=Launch(args=(), sdk_options=None),
    )


def test_the_registration_is_one_merge_of_the_managed_rows_outside_the_root(tmp_path: Path):
    settings = tmp_path / "home" / "settings.json"

    entry = plan_global_hook(settings)

    assert entry.kind is WriteKind.MERGE_JSON and entry.target == settings and entry.escape
    assert dict(entry.data) == desired_hooks()
    assert set(desired_hooks()["hooks"]) == set(DISPATCHER_EVENTS)
    for rows in desired_hooks()["hooks"].values():
        assert rows == [
            {
                "matcher": "*",
                "command": DISPATCHER_COMMAND,
                "_ai_hats_managed": MANAGED_DISPATCHER_TAG,
            }
        ]
    validate(_plan_with(entry, tmp_path / "sessions" / "s"))
    with pytest.raises(EscapeUndeclared):
        validate(_plan_with(dataclasses.replace(entry, escape=False), tmp_path / "sessions" / "s"))


def test_applying_it_creates_the_settings_file_when_missing(tmp_path: Path):
    settings = tmp_path / "home" / "settings.json"
    plan = _plan_with(plan_global_hook(settings), tmp_path / "sessions" / "s")

    assert apply(plan).changed is True
    data = json.loads(settings.read_text())
    assert data == desired_hooks()
    before = settings.stat().st_mtime_ns
    assert apply(plan).changed is False and settings.stat().st_mtime_ns == before


def test_applying_it_keeps_the_persons_keys_and_rows(tmp_path: Path):
    """The person's keys and hook rows stay; the managed row is replaced as a
    set and lands after theirs — the builder kept its index, the merge does not."""
    settings = tmp_path / "home" / "settings.json"
    settings.parent.mkdir()
    personal = {"matcher": "Edit", "command": "/path/to/custom_user_hook.sh"}
    stale = {"matcher": "Edit", "command": "old", "_ai_hats_managed": MANAGED_DISPATCHER_TAG}
    settings.write_text(
        json.dumps(
            {
                "model": "Gemini 3.6 Flash",
                "permissions": {"allow": ["command(*)"]},
                "hooks": {"PreToolUse": [stale, personal]},
            },
            indent=2,
        )
    )

    apply(_plan_with(plan_global_hook(settings), tmp_path / "sessions" / "s"))

    data = json.loads(settings.read_text())
    assert data["model"] == "Gemini 3.6 Flash"
    assert data["permissions"]["allow"] == ["command(*)"]
    assert data["hooks"]["PreToolUse"] == [personal, desired_hooks()["hooks"]["PreToolUse"][0]]
    for event in DISPATCHER_EVENTS[1:]:
        assert data["hooks"][event] == desired_hooks()["hooks"][event]
