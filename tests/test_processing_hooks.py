"""Join of the five hook kinds (ADR-0035 D6): one identity, one row."""

from __future__ import annotations

from pathlib import Path

import pytest
from ai_hats_core.layout import ProjectLayout

from ai_hats.diagnostics import Diagnostic
from ai_hats.processing import ProcessingError, Root, compose
from ai_hats.surfaces.hook_channel import HookEvent
from ai_hats.surfaces.plan import OnError, TraceEntry

_RUN = "../skills/tool/scripts/run.sh"
_SCRIPT = "#!/usr/bin/env bash\nexit 0\n"


def _hook(kind: str, body: str) -> str:
    return f"schema_version: 1\ntype: hook\nkind: {kind}\n{body}"


def _layout(tmp_path: Path) -> ProjectLayout:
    project = tmp_path / "proj"
    (project / ".agent" / "ai-hats").mkdir(parents=True, exist_ok=True)
    return ProjectLayout(root=project, base=project / ".agent" / "ai-hats")


def test_identical_hooks_collapse_and_the_stricter_error_wins(tmp_path: Path):
    """Two declarations of one identity become one row; refuse beats warn; the first keeps the slot."""
    root = tmp_path / "lib"
    files = {
        "alpha.yaml": (
            "schema_version: 1\ntype: composite\n"
            "expr: pre-push + gate-warn + safety + provision + consent-done\n"
        ),
        "beta.yaml": (
            "schema_version: 1\ntype: composite\n"
            "expr: pre-push-late + gate-strict + safety-late + provision-late + consent-done-late\n"
        ),
        "skills/tool/scripts/run.sh": _SCRIPT,
        "operations/rack.transition.yaml": (
            "schema_version: 1\ntype: operation\nsurface: rack\n"
            "reader: rack_transition\nbinds_ticket: true\n"
        ),
        "hooks/pre-push.yaml": _hook("git", f"at: pre-push\nrun: {_RUN}\n"),
        "hooks/pre-push-late.yaml": _hook("git", f"at: pre-push\nrun: {_RUN}\n"),
        "hooks/gate-warn.yaml": _hook(
            "workflow", f"app: rack\nobject: tasks\nat: '->done'\nrun: {_RUN}\non_error: warn\n"
        ),
        "hooks/gate-strict.yaml": _hook(
            "workflow", f"app: rack\nobject: tasks\nat: '->done'\nrun: {_RUN}\non_error: refuse\n"
        ),
        "hooks/safety.yaml": _hook("runtime", f"at: PreToolUse\nmatcher: Bash\nrun: {_RUN}\n"),
        "hooks/safety-late.yaml": _hook("runtime", f"at: PreToolUse\nmatcher: Bash\nrun: {_RUN}\n"),
        "hooks/provision.yaml": _hook("worktree", f"at: wt_in\nrun: {_RUN}\n"),
        "hooks/provision-late.yaml": _hook("worktree", f"at: wt_in\nrun: {_RUN}\n"),
        "hooks/consent-done.yaml": _hook("consent", "operation: rack.transition\nat: '->done'\n"),
        "hooks/consent-done-late.yaml": _hook(
            "consent", "operation: rack.transition\nat: '->done'\n"
        ),
    }
    for rel, text in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    script = root / "skills" / "tool" / "scripts" / "run.sh"
    script.chmod(0o755)

    plan = compose("alpha + beta", [Root(root)], layout=_layout(tmp_path), diagnostics=[])

    (git,) = [h for h in plan.hooks.external if h.app == "git"]
    assert git.at == "pre-push"
    assert git.declared_by == "alpha"
    (gate,) = [h for h in plan.hooks.external if h.app == "rack"]
    assert gate.on_error is OnError.REFUSE
    assert gate.object == "tasks"
    assert gate.at == "->done"
    assert gate.declared_by == "alpha"
    (runtime,) = plan.hooks.runtime
    assert runtime.at is HookEvent.PRE_TOOL_USE
    assert runtime.matcher == "Bash"
    (carry,) = [h for h in plan.hooks.external if h.app == "wt"]
    assert carry.at == "create"
    assert carry.declared_by == "alpha"
    (consent,) = [h for h in plan.hooks.external if h.app == "consent_gate"]
    assert consent.object == "rack.transition"
    assert consent.at == "->done"
    assert consent.run is None
    assert consent.declared_by == "alpha"


def test_a_payload_outside_the_root_refuses_and_the_same_file_inside_passes(tmp_path: Path):
    """A run that leaves the layer root refuses; the same bytes inside the root become a hook."""
    root = tmp_path / "lib"
    outside = tmp_path / "run.sh"
    outside.write_text(_SCRIPT)
    outside.chmod(0o755)
    hook = root / "gate.yaml"
    hook.parent.mkdir(parents=True)
    hook.write_text("schema_version: 1\ntype: hook\nkind: git\nat: pre-push\nrun: ../run.sh\n")
    with pytest.raises(ProcessingError, match="outside the library root"):
        compose("gate", [Root(root)], layout=_layout(tmp_path), diagnostics=[])

    inside = root / "run.sh"
    inside.write_text(_SCRIPT)
    inside.chmod(0o755)
    hook.write_text("schema_version: 1\ntype: hook\nkind: git\nat: pre-push\nrun: run.sh\n")
    plan = compose("gate", [Root(root)], layout=_layout(tmp_path), diagnostics=[])
    (git,) = plan.hooks.external
    assert git.run is not None and git.run.path == inside.resolve()


def _consent_library(root: Path) -> None:
    files = {
        "operations/rack.transition.yaml": (
            "schema_version: 1\ntype: operation\nsurface: rack\n"
            "reader: rack_transition\nbinds_ticket: true\n"
        ),
        "hooks/consent/rack.transition/plan-execute.yaml": _hook(
            "consent", "operation: rack.transition\nat: plan->execute\n"
        ),
        "hooks/consent/rack.transition/done.yaml": _hook(
            "consent", "operation: rack.transition\nat: '->done'\n"
        ),
        "hooks/consent/wt.merge/pre-merge.yaml": _hook(
            "consent", "operation: wt.merge\nat: pre-merge\n"
        ),
        "operations/wt.merge.yaml": (
            "schema_version: 1\ntype: operation\nsurface: ai-hats\n"
            "reader: wt_merge\nbinds_ticket: false\n"
        ),
        "trait-agent.yaml": (
            "schema_version: 1\ntype: composite\n"
            "expr: rack.transition::plan-execute + rack.transition::done + wt.merge::pre-merge\n"
        ),
        "quiet.yaml": (
            "schema_version: 1\ntype: composite\nexpr: trait-agent - rack.transition::done\n"
        ),
    }
    for rel, text in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)


def test_removing_an_armed_consent_point_warns_wherever_the_minus_is_written(tmp_path: Path):
    """Subtracting one consent point leaves the others armed, records who removed it, and warns.

    The minus at the top of the expression and the same minus inside a composite file both warn.
    """
    root = tmp_path / "lib"
    _consent_library(root)
    done = "hooks::consent::rack.transition::done"

    diagnostics: list[Diagnostic] = []
    plan = compose(
        "trait-agent - rack.transition::done",
        [Root(root)],
        layout=_layout(tmp_path),
        diagnostics=diagnostics,
    )
    armed = [hook.at for hook in plan.hooks.external if hook.app == "consent_gate"]
    assert sorted(armed) == ["plan->execute", "pre-merge"]
    assert (
        TraceEntry(done, "trait-agent", removed_by="trait-agent - rack.transition::done")
        in plan.trace
    )
    assert any(done in item.text for item in diagnostics)

    inner: list[Diagnostic] = []
    again = compose("quiet", [Root(root)], layout=_layout(tmp_path), diagnostics=inner)
    assert [hook.at for hook in again.hooks.external] == armed
    assert TraceEntry(done, "trait-agent", removed_by="quiet") in again.trace
    assert any(done in item.text for item in inner)

    (root / "note.md").write_text("---\nschema_version: 1\ntype: prompt\nblock: body\n---\nN\n")
    (root / "holder.yaml").write_text("schema_version: 1\ntype: composite\nexpr: note\n")
    plain: list[Diagnostic] = []
    compose("holder - note", [Root(root)], layout=_layout(tmp_path), diagnostics=plain)
    assert not any("disarms consent" in item.text for item in plain)
