"""Literal-homes contract: canonical constants are NOT re-declared elsewhere (HATS-917).

Only place tests duplicate raw literals; typos in constants fall here and only here.
"""  # comment-length: allow

import importlib
import tomllib
from pathlib import Path

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
from ai_hats.pipeline.registry import STEP_ENTRY_POINT_GROUP


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


def test_funnel_keys():
    """Pipeline boundary-crossing key names (explicit, no computed names).

    Explicit assertions prevent typos in constant definitions from hiding. Only the
    keys with a reader are declared and pinned: HATS-1783 dropped 15 that nothing but
    this test named, so the list here is the whole of ``keys.py`` minus the init
    funnel, which ``init_steps.py`` declares and reads in one file.
    """  # comment-length: allow — why a key is absent is the part that is not obvious
    assert keys.KEY_ROLE == "role"
    assert keys.KEY_PROJECT_DIR == "project_dir"
    assert keys.KEY_PROVIDER == "provider"
    assert keys.KEY_SESSION_ID == "session_id"
    assert keys.KEY_SESSION_DIR == "session_dir"
    assert keys.KEY_CLAUDE_SESSION_ID == "claude_session_id"
    assert keys.KEY_ERRORS == "errors"


def test_the_pipeline_names_are_not_the_area_s_to_declare():
    """The catalog is the application's; ``keys.py`` must not grow a copy (HATS-1783).

    The eleven ``PIPELINE_*`` constants this test used to pin were an enumeration of
    the *application's* pipelines living inside the area — §6 of
    docs/adr/attachments/area-extraction-notes.md — duplicating ``ai_hats/pipeline_catalog.py``. The
    names are held against the shipped YAML by
    ``tests/test_area_boundary.py::test_every_shipped_pipeline_is_declared_in_the_catalog``,
    which is a stronger guard than a literal pin: it cannot go green on a pipeline
    nobody declared.
    """  # comment-length: allow — a deleted pin has to say what replaced it
    assert not [name for name in dir(keys) if name.startswith("PIPELINE_")]


def _declared_steps() -> dict[str, str]:
    """The built-in step ids as ``pyproject.toml`` declares them (HATS-1783).

    Read from the file, not from ``importlib.metadata``: the installed metadata is a
    *build* of this block, so a venv that has not been re-synced since the block was
    edited answers for the previous edit — this gate would then pass on a pyproject
    nobody checked (docs/adr/attachments/area-extraction-notes.md §5, row 4). Whether the block
    reached a built distribution is a different subject, and it has its own tier
    (``tests/e2e/test_step_entry_point_resolution.py``).
    """  # comment-length: allow — which tree this reads is the point of the helper
    root = Path(__file__).resolve().parents[1]
    data = tomllib.loads((root / "pyproject.toml").read_text())
    return data["project"]["entry-points"][STEP_ENTRY_POINT_GROUP]


def _load(target: str) -> type:
    """``module:attr`` as an entry point spells it, resolved without a metadata read."""
    module, _, attr = target.partition(":")
    return getattr(importlib.import_module(module), attr)


def test_step_registry_names_frozen():
    """Built-in step registry is frozen (drift guard).

    The 23 canonical step IDs are declared under the ``ai_hats.steps`` entry-point
    group. Any addition/removal must be deliberate and reflected here.
    """
    expected = [
        "bootstrap_project",
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
        "prepare_execute_session",
        "provider",
        "quorum_autoclose",
        "render_update_banner",
        "resolve_prompt",
        "run_session_end",
        "run_session_review",
        "save_artifact",
        "select_provider",
        "spawn_session_review",
    ]
    assert sorted(_declared_steps()) == expected


def test_step_entry_point_keys_are_the_steps_own_names():
    """HATS-917: the YAML id is spelled in the step, and pyproject must repeat it.

    Declaring the built-ins in pyproject.toml (HATS-1783) put the id in a second file,
    so this holds that copy to the original — every entry-point key equals the
    ``StepIO.name`` of the class it names. ``launch_provider`` is the one sanctioned
    mismatch: HATS-535 split the step and kept the old id as an alias for pre-split
    YAML, so it points at the same class ``provider`` does.
    """
    declarations = _declared_steps()
    mismatched = {}
    for key, target in declarations.items():
        if key == "launch_provider":
            continue
        cls = _load(target)
        # The derivation the registry used before the ids moved to pyproject: a step
        # whose id is a class constant says so, the rest answer through their StepIO.
        declared = getattr(cls, "_NAME", None) or cls().io.name
        if declared != key:
            mismatched[key] = declared
    assert not mismatched, (
        f"entry-point key != the step's own StepIO.name: {mismatched} — the id belongs "
        "to the step (HATS-917); fix the key in pyproject.toml, not the step"
    )

    assert declarations["launch_provider"] == declarations["provider"], (
        "HATS-535: `launch_provider` is an alias for `provider` and must name the same "
        f"class; it names {declarations['launch_provider']} and "
        f"{declarations['provider']}"
    )


def test_judge_markers_taught_where_extracted():
    """Drift guard: extract_marker markers are taught in library (HATS-917).

    Each marker used in library/core/pipelines/*.yaml extract_marker steps
    must be taught in library/core/skills/**/SKILL.md or
    library/core/roles/**/config.yaml. Silent drift (pipeline extracts
    undocumented marker, or agent trained on undeclared marker) fails here.
    """
    from pathlib import Path

    pipelines_dir = Path("packages/ai-hats-library/src/ai_hats_library/core/pipelines")
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
    skills_dir = Path("packages/ai-hats-library/src/ai_hats_library/core/skills")
    roles_dir = Path("packages/ai-hats-library/src/ai_hats_library/core/roles")

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
