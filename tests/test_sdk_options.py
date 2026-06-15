"""Unit tests for ``ai_hats.sdk_options`` (HATS-474 Phase 1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_hats.composer import CompositionResult, ResolvedComponent
from ai_hats.models import ComponentType, HooksConfig
from ai_hats.sdk_options import (
    _build_plugins,
    _build_system_prompt,
    build_first_user_message,
    build_options,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _empty_composition(name: str = "test-role") -> CompositionResult:
    return CompositionResult(
        name=name,
        priorities=[],
        rules=[],
        skills=[],
        hooks=HooksConfig(),
        injections=[],
    )


def _make_skill(root: Path, name: str, body: str = "# Skill body") -> ResolvedComponent:
    """Create a skill on disk and return a ResolvedComponent pointing at it."""
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\ndescription: {name} skill\n---\n{body}\n"
    )
    return ResolvedComponent(
        name=name,
        component_type=ComponentType.SKILL,
        source_path=skill_dir,
        injection=body,
    )


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """Project root with an ``.agent/ai-hats`` dir so session_cache_dir works."""
    pdir = tmp_path / "proj"
    pdir.mkdir()
    # session_cache_dir needs ai_hats_dir to be resolvable; create the
    # canonical path (`.agent/ai-hats/`) so the helper finds it.
    (pdir / ".agent" / "ai-hats").mkdir(parents=True)
    return pdir


# ---------------------------------------------------------------------------
# build_options — happy path + section coverage
# ---------------------------------------------------------------------------


def test_build_options_minimal(project_dir: Path) -> None:
    """Empty composition produces a preset system_prompt, no plugins, cwd=project_dir."""
    opts = build_options(
        _empty_composition(),
        project_dir=project_dir,
        session_id="20260524-120000-abc",
    )
    assert opts.system_prompt == {
        "type": "preset",
        "preset": "claude_code",
        "append": "",
    }
    assert opts.plugins == []
    assert opts.cwd == str(project_dir)
    # No optional fields should leak in
    assert opts.model is None
    assert opts.session_id is None
    assert opts.resume is None


def test_build_options_priorities_render_in_append(project_dir: Path) -> None:
    comp = CompositionResult(
        name="role",
        priorities=["Reliability", "Cleanliness"],
        rules=[],
        skills=[],
        hooks=HooksConfig(),
        injections=[],
    )
    opts = build_options(
        comp,
        project_dir=project_dir,
        session_id="sid",
    )
    append = opts.system_prompt["append"]  # type: ignore[index]
    assert "## PRIORITIES" in append
    assert "1. Reliability" in append
    assert "2. Cleanliness" in append


def test_build_options_always_on_rule_appears_in_append(
    project_dir: Path, tmp_path: Path,
) -> None:
    """Rules listed in ALWAYS_ON_RULES end up in the RULES section."""
    rule_dir = tmp_path / "rule"
    rule_dir.mkdir()
    # HATS-700: the always-on body is read on demand from source_path/rule.md
    # (the composer no longer eager-loads it into injection).
    (rule_dir / "rule.md").write_text("Don't rm -rf the homedir.")
    always_on = ResolvedComponent(
        name="global_rule_destructive_actions",
        component_type=ComponentType.RULE,
        source_path=rule_dir,
    )
    comp = CompositionResult(
        name="role",
        priorities=[],
        rules=[always_on],
        skills=[],
        hooks=HooksConfig(),
        injections=[],
    )
    append = build_options(
        comp, project_dir=project_dir, session_id="sid",
    ).system_prompt["append"]  # type: ignore[index]
    assert "## RULES" in append
    assert "global_rule_destructive_actions" in append
    assert "Don't rm -rf the homedir." in append


def test_build_options_non_always_on_rule_not_inlined(
    project_dir: Path, tmp_path: Path,
) -> None:
    """Rules outside ALWAYS_ON_RULES stay off the system prompt."""
    rule_dir = tmp_path / "rule"
    rule_dir.mkdir()
    other_rule = ResolvedComponent(
        name="some_optional_rule",
        component_type=ComponentType.RULE,
        source_path=rule_dir,
        injection="Optional rule body.",
    )
    comp = CompositionResult(
        name="role",
        priorities=[],
        rules=[other_rule],
        skills=[],
        hooks=HooksConfig(),
        injections=[],
    )
    append = build_options(
        comp, project_dir=project_dir, session_id="sid",
    ).system_prompt["append"]  # type: ignore[index]
    # Section absent entirely (no always-on rules in this composition).
    assert "Optional rule body." not in append


def test_build_options_skills_absent_from_append_but_materialized(
    project_dir: Path, tmp_path: Path,
) -> None:
    """HATS-701: skills do NOT surface as an AVAILABLE SKILLS index in the
    append text — Claude discovers them via the materialized SDK plugin, so
    re-emitting the index would be a 2-3x duplicate. Discovery is preserved
    because ``plugins`` still carries the per-session plugin-dir.
    """
    skill = _make_skill(tmp_path / "skills", "doc-protocol")
    comp = CompositionResult(
        name="role",
        priorities=[],
        rules=[],
        skills=[skill],
        hooks=HooksConfig(),
        injections=[],
    )
    opts = build_options(comp, project_dir=project_dir, session_id="sid")
    append = opts.system_prompt["append"]  # type: ignore[index]
    assert "## AVAILABLE SKILLS" not in append
    assert "doc-protocol" not in append
    # Discovery channel intact: the skill is materialized as an SDK plugin.
    assert opts.plugins, "composed skill must still materialize as an SDK plugin"
    assert opts.plugins[0]["type"] == "local"


# ---------------------------------------------------------------------------
# Plugins (skill materialization)
# ---------------------------------------------------------------------------


def test_build_plugins_empty_when_no_skills(project_dir: Path) -> None:
    assert _build_plugins(_empty_composition(), project_dir, "sid") == []


def test_build_options_plugins_populated_when_skills_present(
    project_dir: Path, tmp_path: Path,
) -> None:
    skill = _make_skill(tmp_path / "skills", "git-mastery")
    comp = CompositionResult(
        name="role",
        priorities=[],
        rules=[],
        skills=[skill],
        hooks=HooksConfig(),
        injections=[],
    )
    opts = build_options(
        comp, project_dir=project_dir, session_id="sid-001",
    )
    assert len(opts.plugins) == 1
    plugin = opts.plugins[0]
    assert plugin["type"] == "local"
    plugin_path = Path(plugin["path"])
    # Materialized on disk under per-session cache
    assert plugin_path.exists()
    assert plugin_path.is_dir()
    assert (plugin_path / ".claude-plugin" / "plugin.json").is_file()
    assert (plugin_path / "skills" / "git-mastery" / "SKILL.md").is_file()


# ---------------------------------------------------------------------------
# Passthrough fields
# ---------------------------------------------------------------------------


def test_build_options_claude_session_id_passthrough(project_dir: Path) -> None:
    sid = "deadbeef-1234-1234-1234-deadbeefcafe"
    opts = build_options(
        _empty_composition(),
        project_dir=project_dir,
        session_id="sid",
        claude_session_id=sid,
    )
    assert opts.session_id == sid


def test_build_options_cwd_defaults_to_project_dir(project_dir: Path) -> None:
    opts = build_options(
        _empty_composition(), project_dir=project_dir, session_id="sid",
    )
    assert opts.cwd == str(project_dir)


def test_build_options_cwd_uses_work_dir_when_given(
    project_dir: Path, tmp_path: Path,
) -> None:
    wt = tmp_path / "wt"
    wt.mkdir()
    opts = build_options(
        _empty_composition(),
        project_dir=project_dir,
        session_id="sid",
        work_dir=wt,
    )
    assert opts.cwd == str(wt)


def test_build_options_model_passthrough(project_dir: Path) -> None:
    opts = build_options(
        _empty_composition(),
        project_dir=project_dir,
        session_id="sid",
        model="claude-haiku-4-5",
    )
    assert opts.model == "claude-haiku-4-5"


def test_build_options_empty_model_omitted(project_dir: Path) -> None:
    """Empty string model should NOT set the SDK field — keeps SDK default."""
    opts = build_options(
        _empty_composition(),
        project_dir=project_dir,
        session_id="sid",
        model="",
    )
    assert opts.model is None


def test_build_options_mcp_config_path_converted_to_str(
    project_dir: Path, tmp_path: Path,
) -> None:
    mcp_file = tmp_path / "mcp.json"
    mcp_file.write_text("{}")
    opts = build_options(
        _empty_composition(),
        project_dir=project_dir,
        session_id="sid",
        mcp_config=mcp_file,
    )
    assert opts.mcp_servers == str(mcp_file)


def test_build_options_mcp_config_str_passthrough(project_dir: Path) -> None:
    opts = build_options(
        _empty_composition(),
        project_dir=project_dir,
        session_id="sid",
        mcp_config="/some/abs/path",
    )
    assert opts.mcp_servers == "/some/abs/path"


def test_build_options_settings_passthrough(project_dir: Path) -> None:
    opts = build_options(
        _empty_composition(),
        project_dir=project_dir,
        session_id="sid",
        settings="/path/to/settings.json",
    )
    assert opts.settings == "/path/to/settings.json"


def test_build_options_extra_env_copied(project_dir: Path) -> None:
    env = {"AI_HATS_ROLE": "maintainer", "FOO": "bar"}
    opts = build_options(
        _empty_composition(),
        project_dir=project_dir,
        session_id="sid",
        extra_env=env,
    )
    assert opts.env == env
    # Should be a copy, not the same reference (caller may mutate).
    env["MUTATED"] = "yes"
    assert "MUTATED" not in opts.env


def test_build_options_budget_and_turns(project_dir: Path) -> None:
    opts = build_options(
        _empty_composition(),
        project_dir=project_dir,
        session_id="sid",
        max_budget_usd=1.5,
        max_turns=10,
    )
    assert opts.max_budget_usd == 1.5
    assert opts.max_turns == 10


def test_build_options_resume_passthrough(project_dir: Path) -> None:
    opts = build_options(
        _empty_composition(),
        project_dir=project_dir,
        session_id="sid",
        resume="prior-session-uuid",
    )
    assert opts.resume == "prior-session-uuid"


def test_build_options_fork_session(project_dir: Path) -> None:
    opts = build_options(
        _empty_composition(),
        project_dir=project_dir,
        session_id="sid",
        fork_session=True,
    )
    assert opts.fork_session is True


def test_build_options_fork_session_default_false(project_dir: Path) -> None:
    opts = build_options(
        _empty_composition(), project_dir=project_dir, session_id="sid",
    )
    assert opts.fork_session is False


def test_build_options_permission_mode(project_dir: Path) -> None:
    opts = build_options(
        _empty_composition(),
        project_dir=project_dir,
        session_id="sid",
        permission_mode="acceptEdits",
    )
    assert opts.permission_mode == "acceptEdits"


def test_build_options_allowed_tools_passthrough(project_dir: Path) -> None:
    opts = build_options(
        _empty_composition(),
        project_dir=project_dir,
        session_id="sid",
        allowed_tools=["Read", "Edit", "Bash(git *)"],
    )
    assert opts.allowed_tools == ["Read", "Edit", "Bash(git *)"]


# ---------------------------------------------------------------------------
# Placeholder expansion
# ---------------------------------------------------------------------------


def test_build_system_prompt_expands_ai_hats_dir_placeholder(
    project_dir: Path,
) -> None:
    """`<ai_hats_dir>` literal in role injection must be expanded by build time."""
    comp = CompositionResult(
        name="role",
        priorities=[],
        rules=[],
        skills=[],
        hooks=HooksConfig(),
        injections=["See files under <ai_hats_dir>/library/"],
    )
    sp = _build_system_prompt(comp, project_dir)
    append = sp["append"]
    # The literal token must NOT survive into the agent's prompt.
    assert "<ai_hats_dir>" not in append
    # The expanded path must be present.
    assert ".agent/ai-hats" in append


# ---------------------------------------------------------------------------
# build_first_user_message
# ---------------------------------------------------------------------------


def test_build_first_user_message_all_empty_returns_empty() -> None:
    assert build_first_user_message() == ""


def test_build_first_user_message_task_only() -> None:
    msg = build_first_user_message(task="Do thing")
    assert msg == "# TASK\nDo thing"


def test_build_first_user_message_state_only() -> None:
    msg = build_first_user_message(project_state="cwd=...")
    assert msg == "# PROJECT_STATE\ncwd=..."


def test_build_first_user_message_ticket_only() -> None:
    msg = build_first_user_message(ticket_context="ticket body")
    assert msg == "# TICKET_CONTEXT\nticket body"


def test_build_first_user_message_section_order() -> None:
    """When all sections are present, order is STATE → TICKET → TASK."""
    msg = build_first_user_message(
        project_state="state",
        ticket_context="ticket",
        task="task",
    )
    state_idx = msg.index("# PROJECT_STATE")
    ticket_idx = msg.index("# TICKET_CONTEXT")
    task_idx = msg.index("# TASK")
    assert state_idx < ticket_idx < task_idx


def test_build_first_user_message_skips_empty_sections() -> None:
    """Empty sections are omitted entirely — no blank section headers."""
    msg = build_first_user_message(project_state="", ticket_context="t", task="T")
    assert "PROJECT_STATE" not in msg
    assert "# TICKET_CONTEXT\nt" in msg
    assert "# TASK\nT" in msg


def test_build_first_user_message_sections_separated_by_blank_line() -> None:
    msg = build_first_user_message(ticket_context="t", task="T")
    assert msg == "# TICKET_CONTEXT\nt\n\n# TASK\nT"
