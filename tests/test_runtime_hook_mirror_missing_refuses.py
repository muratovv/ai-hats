"""A hook script the skill ships but the session mirror lacks refuses the build (HATS-1862).

The mirror is ai-hats's own write, so this fault is never the skill author's;
the build stops with an error naming the mirror path instead of wiring a gate
that cannot run. The mirror is broken through the port on purpose: the real
build writes it a moment earlier, so no honest filesystem move produces this
state, and that is exactly why the check is a regression guard. Nothing around
``build_session_artifacts`` catches the error (``wrap_runner`` and
``subagent_runner`` call it bare), so what escapes here is what the launch shows.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_hats_core import ComponentKind, CompositionResult, ResolvedComponent
from ai_hats_core.layout import ProjectLayout
from ai_hats.hook_collection import RuntimeHookMirrorError
from ai_hats.materialization import ApplyMaterializer
from ai_hats.session_artifacts import BuiltArtifacts, RunMode
from ai_hats.surfaces.claude.provider import ClaudeSurface

SESSION_ID = "sid-mirror-missing"


class _MirrorLostTheScript(ApplyMaterializer):
    """The real port in every respect but one: whatever it copied, it does not
    find executable afterwards — the seam a mirror writer gone wrong would show."""

    def executable_at(self, path: Path) -> bool:
        return False


def _shipped_gate(root: Path) -> ResolvedComponent:
    skill = root / "gate-skill"
    (skill / "hooks").mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\n"
        "name: gate-skill\n"
        "description: A skill shipping one Bash gate.\n"
        "ai_hats:\n"
        "  runtime_hooks:\n"
        "    PreToolUse:\n"
        "      - matcher: Bash\n"
        "        script: hooks/gate.sh\n"
        "---\n"
        "# gate-skill\n"
    )
    script = skill / "hooks" / "gate.sh"
    script.write_text("#!/usr/bin/env bash\nexit 0\n")
    script.chmod(0o755)
    return ResolvedComponent(
        name="gate-skill", component_type=ComponentKind.SKILL, source_path=skill
    )


@pytest.mark.parametrize("run_mode", [RunMode.HITL, RunMode.AUTOMATE])
def test_a_mirror_that_lacks_a_shipped_script_stops_the_build(
    tmp_path: Path, monkeypatch, run_mode
) -> None:
    monkeypatch.setenv("AI_HATS_CACHE_HOME", str(tmp_path / "cache-home"))
    project = tmp_path / "project"
    project.mkdir()
    result = CompositionResult(
        name="r", priorities=[], rules=[], skills=[_shipped_gate(tmp_path)], injections=[]
    )
    artifacts = BuiltArtifacts(port=_MirrorLostTheScript())

    with pytest.raises(RuntimeHookMirrorError) as excinfo:
        ClaudeSurface().build_session_artifacts(
            ProjectLayout.at(project), result, SESSION_ID, run_mode=run_mode, artifacts=artifacts
        )

    message = str(excinfo.value)
    assert "gate-skill" in message and "hooks/gate.sh" in message
    assert "plugin/skills/gate-skill/hooks/gate.sh" in message, "the mirror path, to triage"
    assert artifacts.notices == [], "not the author's fault, so no warning is offered instead"
