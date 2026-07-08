"""Literal-homes contract: canonical constants are NOT re-declared elsewhere (HATS-917).

Only place tests duplicate raw literals; typos in constants fall here and only here.
"""  # comment-length: allow

from ai_hats_observe.trace import ENV_SESSION_ID  # HATS-948: observe owns the session env var
from ai_hats.constants import (
    ENV_LAUNCHER_DEST,
    ENV_REPO_URL,
    ENV_ROLE,
    ENV_SKIP_RETRO,
    HOOK_NOTIFICATION,
    HOOK_POST_TOOL_USE,
    HOOK_PRE_TOOL_USE,
    HOOK_SESSION_END,
    HOOK_SESSION_START,
    HOOK_STOP,
    HOOK_SUBAGENT_STOP,
    HOOK_USER_PROMPT_SUBMIT,
    PROVIDER_CLAUDE,
    PROVIDER_GEMINI,
)
from ai_hats_observe.artifacts import (
    AUDIT_MD,
    META_PROMPT_TXT,
    METRICS_JSON,
    PTY_RAW_LOG,
    REASONING_LOG,
    RETRO_LOG,
    SESSION_PREFIX,
    TRACE_LOG,
    TRANSCRIPT_TXT,
    USAGE_JSON,
    session_dirname,
    strip_session_prefix,
)
from ai_hats.paths import (
    AI_HATS_PROJECT_DIR_ENV,
    ENV_AI_HATS_DIR,
    ENV_AI_HATS_VENV,
    PROJECT_CONFIG,
)
from ai_hats.pipeline import keys
from ai_hats.pipeline.steps import _BUILTINS


def test_project_config():
    """PROJECT_CONFIG matches the on-disk canonical name."""
    assert PROJECT_CONFIG == "ai-hats.yaml"


def test_session_artifact_filenames():
    """Session artifact filenames match canonical on-disk names."""
    assert TRACE_LOG == "trace.log"
    assert AUDIT_MD == "audit.md"
    assert TRANSCRIPT_TXT == "transcript.txt"
    assert METRICS_JSON == "metrics.json"
    assert USAGE_JSON == "usage.json"
    assert META_PROMPT_TXT == "meta_prompt.txt"
    assert REASONING_LOG == "reasoning.log"
    assert PTY_RAW_LOG == "pty_raw.log"
    assert RETRO_LOG == "retro.log"


def test_session_dir_naming():
    """Session directory naming: prefix, builder, and strip (idempotent)."""
    assert SESSION_PREFIX == "session_"
    assert session_dirname("abc") == "session_abc"
    assert strip_session_prefix("session_abc") == "abc"
    assert strip_session_prefix("abc") == "abc"  # idempotent


def test_env_var_names():
    """Environment variable names for runtime / config resolution."""
    assert ENV_SESSION_ID == "AI_HATS_SESSION_ID"
    assert ENV_REPO_URL == "AI_HATS_REPO_URL"
    assert ENV_ROLE == "AI_HATS_ROLE"
    assert ENV_LAUNCHER_DEST == "AI_HATS_LAUNCHER_DEST"
    assert ENV_SKIP_RETRO == "HATS_SKIP_RETRO"
    assert ENV_AI_HATS_DIR == "AI_HATS_DIR"
    assert ENV_AI_HATS_VENV == "AI_HATS_VENV"
    assert AI_HATS_PROJECT_DIR_ENV == "AI_HATS_PROJECT_DIR"


def test_hook_event_names():
    """Claude Code hook event names (engine vocabulary)."""
    assert HOOK_PRE_TOOL_USE == "PreToolUse"
    assert HOOK_POST_TOOL_USE == "PostToolUse"
    assert HOOK_SESSION_START == "SessionStart"
    assert HOOK_SESSION_END == "SessionEnd"
    assert HOOK_USER_PROMPT_SUBMIT == "UserPromptSubmit"
    assert HOOK_STOP == "Stop"
    assert HOOK_SUBAGENT_STOP == "SubagentStop"
    assert HOOK_NOTIFICATION == "Notification"


def test_provider_names():
    """Provider registry names (runner vocabulary)."""
    assert PROVIDER_CLAUDE == "claude"
    assert PROVIDER_GEMINI == "gemini"


def test_funnel_keys():
    """Pipeline boundary-crossing key names (explicit, no computed names).

    These are the 22 keys seeded by CLI/runners or read from final state.
    Explicit assertions prevent typos in constant definitions from hiding.
    """
    assert keys.KEY_ROLE == "role"
    assert keys.KEY_INTERACTIVE == "interactive"
    assert keys.KEY_PROJECT_DIR == "project_dir"
    assert keys.KEY_PROMPT_PATH == "prompt_path"
    assert keys.KEY_PROVIDER == "provider"
    assert keys.KEY_MODEL == "model"
    assert keys.KEY_ISOLATION == "isolation"
    assert keys.KEY_TICKET == "ticket"
    assert keys.KEY_TAGS == "tags"
    assert keys.KEY_EXTRA_ARGS == "extra_args"
    assert keys.KEY_COMPOSITION == "composition"
    assert keys.KEY_SESSION_MGR == "session_mgr"
    assert keys.KEY_TRACER_FACTORY == "tracer_factory"
    assert keys.KEY_MAX_RETRIES == "max_retries"
    assert keys.KEY_SESSION_ID == "session_id"
    assert keys.KEY_SESSION_DIR == "session_dir"
    assert keys.KEY_CLAUDE_SESSION_ID == "claude_session_id"
    assert keys.KEY_EXIT_CODE == "exit_code"
    assert keys.KEY_ERRORS == "errors"
    assert keys.KEY_REVIEW_PATH == "review_path"
    assert keys.KEY_SAVED_PATH == "saved_path"
    assert keys.KEY_INTAKE_RESULT == "intake_result"


def test_pipeline_names():
    """Core pipeline names (Python-side spellings)."""
    assert keys.PIPELINE_HUMAN == "human"
    assert keys.PIPELINE_EXECUTE == "execute"
    assert keys.PIPELINE_FINALIZE_HITL == "finalize-hitl"
    assert keys.PIPELINE_FINALIZE_SUBAGENT == "finalize-subagent"
    assert keys.PIPELINE_REFLECT_SESSION == "reflect-session"
    assert keys.PIPELINE_REFLECT_ALL == "reflect-all"
    assert keys.PIPELINE_REFLECT_HYPOTHESIS_PHASE1 == "reflect-hypothesis-phase1"
    assert keys.PIPELINE_REFLECT_HYPOTHESIS_PHASE2 == "reflect-hypothesis-phase2"
    assert keys.PIPELINE_REFLECT_ROLE == "reflect-role"
    assert keys.PIPELINE_REFLECT_ISSUE == "reflect-issue"


def test_step_registry_names_frozen():
    """Built-in step registry is frozen (drift guard).

    The 19 canonical step IDs are registered in _BUILTINS.
    Any addition/removal must be deliberate and reflected here.
    """
    expected = [
        "build_handoff",
        "check_update_async",
        "compose_role",
        "compute_usage",
        "emit_stdout",
        "extract_marker",
        "launch_provider",
        "make_audit",
        "materialize_system_prompt",
        "maybe_spawn_session_reviewer",
        "post_log",
        "pre_log",
        "provider",
        "quorum_autoclose",
        "render_update_banner",
        "resolve_prompt",
        "run_session_end",
        "run_session_review",
        "save_artifact",
        "spawn_session_review",
    ]
    assert sorted(_BUILTINS) == expected


def test_judge_markers_taught_where_extracted():
    """Drift guard: extract_marker markers are taught in library (HATS-917).

    Each marker used in library/core/pipelines/*.yaml extract_marker steps
    must be taught in library/core/skills/**/SKILL.md or
    library/core/roles/**/config.yaml. Silent drift (pipeline extracts
    undocumented marker, or agent trained on undeclared marker) fails here.
    """
    from pathlib import Path

    pipelines_dir = Path("library/core/pipelines")
    markers_to_yaml = {}  # marker -> yaml_file for error reporting

    # Extract markers from all pipelines
    for yaml_file in sorted(pipelines_dir.glob("*.yaml")):
        import yaml

        try:
            data = yaml.safe_load(yaml_file.read_text())
            if data and "steps" in data:
                for step in data["steps"]:
                    if step.get("id") == "extract_marker":
                        params = step.get("params", {})
                        start_marker = params.get("start")
                        end_marker = params.get("end")

                        if start_marker:
                            markers_to_yaml[start_marker] = yaml_file.name
                        if end_marker:
                            markers_to_yaml[end_marker] = yaml_file.name
        except Exception as e:
            raise AssertionError(f"Failed to parse {yaml_file.name}: {e}") from e

    # Verify each marker is taught in library
    skills_dir = Path("library/core/skills")
    roles_dir = Path("library/core/roles")

    for marker, yaml_file in sorted(markers_to_yaml.items()):
        marker_found = False

        # Search SKILL.md files
        for skill_md in skills_dir.glob("*/SKILL.md"):
            if marker in skill_md.read_text():
                marker_found = True
                break

        # Search roles config.yaml files
        if not marker_found:
            for config_yaml in roles_dir.glob("*/config.yaml"):
                if marker in config_yaml.read_text():
                    marker_found = True
                    break

        assert marker_found, (
            f"Marker '{marker}' extracted in {yaml_file} "
            f"but not taught in library/core/skills/**/SKILL.md "
            f"or library/core/roles/**/config.yaml"
        )
