"""The candidate maintainer arms the same hooks as the shipped role, overlays off."""

from __future__ import annotations

from pathlib import Path

from ai_hats.composition_seam import build_plan_preview, build_preview_payload
from ai_hats.consent_wrapper import policy_of
from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG


def _run_key(path: Path) -> str:
    parts = path.parts
    if "skills" in parts:
        index = max(i for i, part in enumerate(parts) if part == "skills")
        return "/".join(parts[index + 1 :])
    return path.name


def _ident(hook) -> tuple:
    if hook.__class__.__name__ == "RuntimeHook":
        return ("runtime", hook.at.value, hook.matcher, _run_key(hook.run.path))
    run = None if hook.run is None else _run_key(hook.run.path)
    return (hook.app, hook.object, hook.at, run)


def test_maintainer_hooks_match_the_shipped_role_without_a_global_overlay(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("AI_HATS_USER_HOME", str(tmp_path / "home"))
    project = tmp_path / "proj"
    project.mkdir()
    ProjectConfig(provider="claude", library_paths=[]).save(project / PROJECT_CONFIG)

    reference = build_plan_preview(project, role="maintainer").plan
    candidate = build_preview_payload(project, role="maintainer", experimental=True).plan
    assert candidate is not None and reference is not None
    ref = {_ident(hook) for hook in (*reference.hooks.runtime, *reference.hooks.external)}
    got = {_ident(hook) for hook in (*candidate.hooks.runtime, *candidate.hooks.external)}
    assert got == ref
    assert got | {("git", None, "pre-push", "not-a-real-script")} != ref
    assert any(item[0] == "rack" and item[2] == "->done" for item in got)
    assert any(item[0] == "wt" and item[2] == "pre-merge" for item in got)

    policy = policy_of(candidate.hooks.external)
    assert "->done" in policy["rack.transition"]
    assert "plan->execute" in policy["rack.transition"]
    assert "critical-path" in policy["fs.remove"]

    from ai_hats.githooks_resolve import resolve_git_gates_from_plan

    gates = resolve_git_gates_from_plan(candidate, "pre-push")
    assert any(gate.path.name == "pre-push-e2e-master.sh" for gate in gates.gates)
    assert gates.refusals == ()
