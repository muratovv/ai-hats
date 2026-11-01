"""Emission (ADR-0035 D9): processing hands back the plan's composition half
(ADR-0036 D1) — identity, prompt, skills with their trees, hooks, trace."""

from __future__ import annotations

from pathlib import Path

import pytest
from ai_hats_core.layout import ProjectLayout

from ai_hats.diagnostics import Diagnostic
from ai_hats.processing import Root, compose
from ai_hats.surfaces.plan import CompositionPlan, Hooks, TraceEntry, composition_record
from tests._flat_library import write_root


@pytest.fixture
def layout(tmp_path: Path) -> ProjectLayout:
    project = tmp_path / "proj"
    (project / ".agent" / "ai-hats").mkdir(parents=True)
    return ProjectLayout(root=project, base=project / ".agent" / "ai-hats")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return write_root(tmp_path / "lib")


def test_a_candidate_plan_names_the_re_marker(root: Path, layout: ProjectLayout):
    """The record and the child both read the marker off the plan; a bare name cannot say it."""
    plan = compose("alpha", [Root(root)], layout=layout, diagnostics=[])

    assert plan.dsl == "re"
    assert composition_record(plan)["dsl"] == "re"


def test_the_sample_role_yields_a_plan(root: Path, layout: ProjectLayout):
    diagnostics: list[Diagnostic] = []
    plan = compose("alpha", [Root(root)], layout=layout, diagnostics=diagnostics)
    assert isinstance(plan, CompositionPlan)
    assert plan.identity == "alpha"
    assert [(b.name, [m.name for m in b.members]) for b in plan.prompt.blocks] == [
        (None, ["alpha::prompt", "shared::prompt"]),
        ("PRIORITIES", ["alpha::priorities"]),
        ("RULES", ["rules::rule-one"]),
    ]
    assert plan.prompt.text.startswith(
        "ALPHA\n\n\nSHARED\n\n\n## PRIORITIES\n1. Speed\n\n## RULES\n"
    )
    (tool,) = plan.skills
    assert tool.name == "skills::tool"
    assert tool.path == (root / "skills" / "tool").resolve()
    assert tool.document is not None and tool.document.startswith("---\nname: tool")
    assert tool.on_path == ("scripts",)
    assert plan.hooks == Hooks(runtime=(), external=())
    assert plan.trace == (
        TraceEntry("alpha", "alpha", None),
        TraceEntry("shared", "alpha", None),
        TraceEntry("alpha::prompt", "alpha", None),
        TraceEntry("alpha::priorities", "alpha", None),
        TraceEntry("shared::prompt", "shared", None),
        TraceEntry("rules::rule-one", "shared", None),
        TraceEntry("skills::tool", "shared", None),
    )
    assert [d.text for d in diagnostics if "README" not in str(d.where)] == []
    assert composition_record(plan)["identity"] == "alpha", "the record projects the value"


def test_the_identity_is_the_canonical_expression(root: Path, layout: ProjectLayout):
    plan = compose("  alpha   +   bare ", [Root(root)], layout=layout, diagnostics=[])
    assert plan.identity == "alpha + bare"
    assert [m.name for m in plan.prompt.blocks[0].members] == [
        "alpha::prompt",
        "shared::prompt",
        "bare::prompt",
    ]


def test_placeholders_are_expanded_for_the_layout(root: Path, layout: ProjectLayout):
    (root / "bare" / "prompt.md").write_text(
        "---\nschema_version: 1\ntype: prompt\nblock: body\n---\nsee <ai_hats_dir>/x and <project_dir>\n"
    )
    (root / "skills" / "tool" / "SKILL.md").write_text(
        "---\nname: tool\nai_hats:\n  schema_version: 1\n  type: skill\n---\nread <ai_hats_dir>/tracker\n"
    )
    plan = compose("bare + tool", [Root(root)], layout=layout, diagnostics=[])
    assert plan.prompt.text == f"see .agent/ai-hats/x and {layout.root.resolve().as_posix()}\n"
    assert plan.skills[0].document.endswith("read .agent/ai-hats/tracker\n")


def test_a_hook_term_reaches_the_plan(root: Path, layout: ProjectLayout):
    script = root / "skills" / "tool" / "scripts" / "run.sh"
    script.chmod(0o755)
    (root / "gate.yaml").write_text(
        "schema_version: 1\ntype: hook\nkind: git\nat: pre-push\nrun: skills/tool/scripts/run.sh\n"
    )
    plan = compose("alpha + gate", [Root(root)], layout=layout, diagnostics=[])
    (git,) = [hook for hook in plan.hooks.external if hook.app == "git"]
    assert git.at == "pre-push"
    assert git.run is not None and git.run.path == script.resolve()


def test_two_roots_compose_last_wins(root: Path, layout: ProjectLayout, tmp_path: Path):
    upper = write_root(
        tmp_path / "upper",
        {"alpha.yaml": "schema_version: 1\ntype: composite\nexpr: alpha - tool + bare\n"},
    )
    plan = compose("alpha", [Root(root), Root(upper)], layout=layout, diagnostics=[])
    assert plan.skills == ()
    assert [m.name for m in plan.prompt.blocks[0].members] == [
        "alpha::prompt",
        "shared::prompt",
        "bare::prompt",
    ]
    assert TraceEntry("skills::tool", "shared", removed_by="alpha") in plan.trace
