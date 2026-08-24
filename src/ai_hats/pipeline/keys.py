"""Boundary-crossing pipeline vocabulary.

Admission criterion: a key belongs here ONLY if it crosses the
CLI/runner <-> pipeline boundary (seeded into the harness initial state
or read back from the final state) **and has a reader**. Step-internal
produce/require keys stay inline in each StepIO -- the literal IS the
contract declaration (open-registry convention).

Pipeline names are not here. Which pipelines exist is knowledge about how
this product uses the area, so it lives in ``ai_hats/pipeline_catalog.py``
outside it (ADR-0026 D14, and §6 of docs/how-to-extract-an-area.md).
"""  # comment-length: allow — the admission criterion is what keeps this file from regrowing

# Seeded by CLI / runners into the initial state.
KEY_ROLE = "role"
KEY_PROJECT_DIR = "project_dir"
KEY_PROVIDER = "provider"
KEY_NO_WIZARD = "no_wizard"
KEY_CHANNEL = "channel"
KEY_TASK_PREFIX = "task_prefix"
KEY_AI_HATS_DIR = "ai_hats_dir"
KEY_VENV_PATH = "venv_path"
KEY_NO_MANAGE_GITIGNORE = "no_manage_gitignore"
KEY_HARNESS_PATH = "harness_path"
KEY_PROJECT_CONFIG = "project_config"

# Read back from the final state by CLI / runners.
KEY_SESSION_ID = "session_id"
KEY_SESSION_DIR = "session_dir"
KEY_CLAUDE_SESSION_ID = "claude_session_id"
KEY_ERRORS = "errors"
KEY_EXECUTE_CMD = "execute_cmd"

__all__ = [
    "KEY_ROLE",
    "KEY_PROJECT_DIR",
    "KEY_PROVIDER",
    "KEY_NO_WIZARD",
    "KEY_CHANNEL",
    "KEY_TASK_PREFIX",
    "KEY_AI_HATS_DIR",
    "KEY_VENV_PATH",
    "KEY_NO_MANAGE_GITIGNORE",
    "KEY_HARNESS_PATH",
    "KEY_PROJECT_CONFIG",
    "KEY_SESSION_ID",
    "KEY_SESSION_DIR",
    "KEY_CLAUDE_SESSION_ID",
    "KEY_ERRORS",
    "KEY_EXECUTE_CMD",
]
