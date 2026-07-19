"""Tests for the ``{{backlog_fsm_edges}}`` FSM-token substitution (HATS-1051).

The token lets the hatrack skill carry the FULL, authoritative backlog edge set
without a hand-maintained table: it is rendered from ``TaskState`` and injected
at skill materialization (same gate as the ``<ai_hats_dir>`` placeholder).
"""

from __future__ import annotations

from pathlib import Path

from ai_hats_core import ComponentKind, ResolvedComponent

from ai_hats.models import TaskState
from ai_hats.placeholders import (
    FSM_EDGES_TOKEN,
    expand_fsm_edges_token,
    render_backlog_fsm_edges,
)
from ai_hats.plugin_dir import materialize_plugin_dir


def _row(table: str, state_value: str) -> str:
    prefix = f"| `{state_value}` |"
    return next(line for line in table.splitlines() if line.startswith(prefix))


# ---------- render_backlog_fsm_edges: table is generated from the live FSM ----------


def test_render_has_a_row_per_state() -> None:
    table = render_backlog_fsm_edges()
    for state in TaskState:
        assert f"| `{state.value}` |" in table


def test_render_cells_match_valid_transitions() -> None:
    table = render_backlog_fsm_edges()
    for state, targets in TaskState.valid_transitions().items():
        row = _row(table, state.value)
        for target in targets:
            assert f"`{target.value}`" in row, f"{state.value}->{target.value} missing"


def test_render_marks_terminal_state() -> None:
    # cancelled has no outgoing edges — it must read as terminal, not blank.
    assert "terminal" in _row(render_backlog_fsm_edges(), "cancelled").lower()


def test_render_includes_pragmatic_edges() -> None:
    # execute self-loop (HATS-955) + done->execute reopen (HATS-328) — the
    # off-happy-path edges neither skill enumerated before this token.
    table = render_backlog_fsm_edges()
    assert "`execute`" in _row(table, "execute")  # self-loop
    assert "`execute`" in _row(table, "done")  # reopen


def test_render_omits_review_to_execute_until_1052() -> None:
    # Guard-rail: review->execute is HATS-1052 and is NOT in the FSM yet. If
    # this assertion ever fails, the edge landed — flip the hatrack rework-loop
    # policy row from "(pending HATS-1052)" to a live edge.
    assert "`execute`" not in _row(render_backlog_fsm_edges(), "review")


# ---------- expand_fsm_edges_token: substitution contract ----------


def test_expand_substitutes_the_token() -> None:
    out = expand_fsm_edges_token(f"## FSM\n\n{FSM_EDGES_TOKEN}\n\nend")
    assert FSM_EDGES_TOKEN not in out
    assert "| From state | Legal transitions |" in out


def test_absent_token_is_identity_same_object() -> None:
    # Cheap no-op path: a skill without the token pays no render cost.
    body = "a skill body with no fsm token at all"
    assert expand_fsm_edges_token(body) is body


def test_idempotent() -> None:
    once = expand_fsm_edges_token(f"x {FSM_EDGES_TOKEN} y")
    twice = expand_fsm_edges_token(once)
    assert once == twice
    assert FSM_EDGES_TOKEN not in twice


def test_multiple_occurrences_all_replaced() -> None:
    out = expand_fsm_edges_token(f"{FSM_EDGES_TOKEN} ... {FSM_EDGES_TOKEN}")
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
    out = materialize_plugin_dir("some-role", [skill], tmp_path, tmp_path / "plugin")
    body = (out / "skills" / "hatrack" / "SKILL.md").read_text()
    assert FSM_EDGES_TOKEN not in body
    assert "| From state | Legal transitions |" in body
    assert "| `execute` |" in body


def test_materialize_leaves_non_token_skill_byte_identical(tmp_path: Path) -> None:
    original = "# Plain skill\n\nno tokens here.\n"
    skill = _make_skill("plain", tmp_path / "src", body=original)
    out = materialize_plugin_dir("some-role", [skill], tmp_path, tmp_path / "plugin")
    assert (out / "skills" / "plain" / "SKILL.md").read_text() == original


def test_materialize_is_layer_agnostic_for_arm_dir_source(tmp_path: Path) -> None:
    # An arm-dir override (library_paths last-wins) resolves the skill's
    # source_path to a dir outside the built-in library. Materialization runs
    # AFTER resolution, so the override body gets the same substitution.
    arm_skills = tmp_path / "arms" / "new" / "skills"
    skill = _make_skill("hatrack", arm_skills, body=f"arm-dir body\n\n{FSM_EDGES_TOKEN}\n")
    out = materialize_plugin_dir("some-role", [skill], tmp_path, tmp_path / "plugin")
    body = (out / "skills" / "hatrack" / "SKILL.md").read_text()
    assert FSM_EDGES_TOKEN not in body
    assert "| `brainstorm` |" in body
