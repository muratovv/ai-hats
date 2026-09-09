"""Tests for the ``{{backlog_fsm_edges}}`` FSM-token substitution (HATS-1051).

The token lets the hatrack skill carry the FULL, authoritative backlog edge set
without a hand-maintained table: it is rendered from the live backlog FSM and
injected at skill materialization (same gate as the ``<ai_hats_dir>``
placeholder).
"""

from __future__ import annotations

from ai_hats_core.layout import ProjectLayout

import logging
from pathlib import Path

from ai_hats_core import ComponentKind, ResolvedComponent

from ai_hats_rack.fsm import load_topology
from ai_hats.placeholders import (
    FSM_EDGES_TOKEN,
    FSM_EDGES_UNAVAILABLE,
    expand_fsm_edges_token,
    render_backlog_fsm_edges,
)
from ai_hats.materialization import ApplyMaterializer
from ai_hats.surfaces.claude.plugin_dir import materialize_plugin_dir


def _row(table: str, state_value: str) -> str:
    prefix = f"| `{state_value}` |"
    return next(line for line in table.splitlines() if line.startswith(prefix))


#: A tasks backlog.yaml that is NOT the packaged one — no state name overlaps,
#: so a rendered row can only have come from this file.
_CUSTOM_BACKLOG = """\
name: tasks
prefix: HATS
fsm:
  initial: draft
  states:
    - { name: draft }
    - { name: shipped }
  edges:
    - { from: draft, to: shipped, name: ship }
links:
  kinds:
    - { name: parent_task, arity: one }
"""


def _project_with_backlog(project_dir: Path, body: str) -> Path:
    catalog = ProjectLayout.at(project_dir).tracker.tasks_dir
    catalog.mkdir(parents=True, exist_ok=True)
    (catalog / "backlog.yaml").write_text(body, encoding="utf-8")
    return project_dir


# ---------- render_backlog_fsm_edges: table is generated from the live FSM ----------


def test_render_uses_the_resolved_catalog_definition(tmp_path: Path) -> None:
    # HATS-1257: the table is the backlog THIS project runs on — a catalog's own
    # backlog.yaml wins, exactly as `rack transition` resolves its guard.
    table = render_backlog_fsm_edges(
        ProjectLayout.at(_project_with_backlog(tmp_path, _CUSTOM_BACKLOG))
    )
    assert "`shipped`" in _row(table, "draft")
    assert "terminal" in _row(table, "shipped").lower()
    # The packaged states must NOT leak in when the catalog declares its own.
    assert "| `brainstorm` |" not in table


def test_render_annotates_named_edges(tmp_path: Path) -> None:
    # A named edge is typeable in place of the target state — the topology knows
    # the names, so the table stops leaving them to hand-written prose.
    table = render_backlog_fsm_edges(ProjectLayout.at(tmp_path))
    assert "`execute` (reclaim)" in _row(table, "execute")
    assert "`execute` (reopen)" in _row(table, "done")
    # Unnamed edges stay bare — the annotation is not decoration.
    assert "`document`," in _row(table, "execute")


def test_malformed_backlog_yields_marker_not_raise(tmp_path: Path, caplog) -> None:
    # The prompt must still render for the agent who would FIX the broken file;
    # the loud channel is `rack ls`, which fails on the very same file.
    project = _project_with_backlog(tmp_path, "this: is not a backlog\n")
    with caplog.at_level(logging.WARNING, logger="ai_hats.placeholders"):
        table = render_backlog_fsm_edges(ProjectLayout.at(project))
    assert table == FSM_EDGES_UNAVAILABLE
    assert "| From state |" not in table
    assert "rack ls" in caplog.text


# A project with no catalog backlog.yaml (bare tmp_path) renders the PACKAGED
# default, which is what no-arg ``load_topology()`` loads — so the topology below
# is the same FSM the renderer resolved, read through the rack's own accessor.
def test_render_has_a_row_per_state(tmp_path: Path) -> None:
    table = render_backlog_fsm_edges(ProjectLayout.at(tmp_path))
    for state in load_topology().states:
        assert f"| `{state}` |" in table


def test_render_cells_match_valid_transitions(tmp_path: Path) -> None:
    table = render_backlog_fsm_edges(ProjectLayout.at(tmp_path))
    topology = load_topology()
    for state in topology.states:
        row = _row(table, state)
        for target in topology.targets(state):
            assert f"`{target}`" in row, f"{state}->{target} missing"


def test_render_marks_terminal_state(tmp_path: Path) -> None:
    # cancelled has no outgoing edges — it must read as terminal, not blank.
    assert (
        "terminal"
        in _row(render_backlog_fsm_edges(ProjectLayout.at(tmp_path)), "cancelled").lower()
    )


def test_render_includes_pragmatic_edges(tmp_path: Path) -> None:
    # execute self-loop (HATS-955) + done->execute reopen (HATS-328) — the
    # off-happy-path edges neither skill enumerated before this token.
    table = render_backlog_fsm_edges(ProjectLayout.at(tmp_path))
    assert "`execute`" in _row(table, "execute")  # self-loop
    assert "`execute`" in _row(table, "done")  # reopen


def test_render_includes_review_to_execute(tmp_path: Path) -> None:
    # HATS-1052: review->execute (the rework loop-back) is now legal, so the
    # rendered FSM token carries `execute` in the review row — the hatrack skill
    # inherits the live rework edge without a hand-maintained table.
    assert "`execute`" in _row(render_backlog_fsm_edges(ProjectLayout.at(tmp_path)), "review")


# ---------- expand_fsm_edges_token: substitution contract ----------


def test_expand_substitutes_the_token(tmp_path: Path) -> None:
    out = expand_fsm_edges_token(f"## FSM\n\n{FSM_EDGES_TOKEN}\n\nend", ProjectLayout.at(tmp_path))
    assert FSM_EDGES_TOKEN not in out
    assert "| From state | Legal transitions |" in out


def test_absent_token_is_identity_same_object(tmp_path: Path) -> None:
    # Cheap no-op path: a skill without the token pays no render cost.
    body = "a skill body with no fsm token at all"
    assert expand_fsm_edges_token(body, ProjectLayout.at(tmp_path)) is body


def test_idempotent(tmp_path: Path) -> None:
    once = expand_fsm_edges_token(f"x {FSM_EDGES_TOKEN} y", ProjectLayout.at(tmp_path))
    twice = expand_fsm_edges_token(once, ProjectLayout.at(tmp_path))
    assert once == twice
    assert FSM_EDGES_TOKEN not in twice


def test_multiple_occurrences_all_replaced(tmp_path: Path) -> None:
    out = expand_fsm_edges_token(
        f"{FSM_EDGES_TOKEN} ... {FSM_EDGES_TOKEN}", ProjectLayout.at(tmp_path)
    )
    assert FSM_EDGES_TOKEN not in out
    assert out.count("| From state | Legal transitions |") == 2


# ---------- end-to-end at the materialization gate (layer-agnostic) ----------


def _make_skill(name: str, root: Path, body: str) -> ResolvedComponent:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(body)
    return ResolvedComponent(
        name=name,
        component_type=ComponentKind.SKILL,
        source_path=skill_dir,
        injection="",
    )


def test_materialize_substitutes_token_in_skill_md(tmp_path: Path) -> None:
    skill = _make_skill("hatrack", tmp_path / "src", body=f"# Hatrack\n\n{FSM_EDGES_TOKEN}\n")
    out = materialize_plugin_dir(
        "some-role", [skill], ProjectLayout.at(tmp_path), tmp_path / "plugin", ApplyMaterializer()
    )
    body = (out / "skills" / "hatrack" / "SKILL.md").read_text()
    assert FSM_EDGES_TOKEN not in body
    assert "| From state | Legal transitions |" in body
    assert "| `execute` |" in body


def test_materialize_leaves_non_token_skill_byte_identical(tmp_path: Path) -> None:
    original = "# Plain skill\n\nno tokens here.\n"
    skill = _make_skill("plain", tmp_path / "src", body=original)
    out = materialize_plugin_dir(
        "some-role", [skill], ProjectLayout.at(tmp_path), tmp_path / "plugin", ApplyMaterializer()
    )
    assert (out / "skills" / "plain" / "SKILL.md").read_text() == original


def test_materialize_is_layer_agnostic_for_arm_dir_source(tmp_path: Path) -> None:
    # An arm-dir override (library_paths last-wins) resolves the skill's
    # source_path to a dir outside the built-in library. Materialization runs
    # AFTER resolution, so the override body gets the same substitution.
    arm_skills = tmp_path / "arms" / "new" / "skills"
    skill = _make_skill("hatrack", arm_skills, body=f"arm-dir body\n\n{FSM_EDGES_TOKEN}\n")
    out = materialize_plugin_dir(
        "some-role", [skill], ProjectLayout.at(tmp_path), tmp_path / "plugin", ApplyMaterializer()
    )
    body = (out / "skills" / "hatrack" / "SKILL.md").read_text()
    assert FSM_EDGES_TOKEN not in body
    assert "| `brainstorm` |" in body
