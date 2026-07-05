"""Boundary-crossing pipeline vocabulary (HATS-917).

Admission criterion: a key belongs here ONLY if it crosses the
CLI/runner <-> pipeline boundary (seeded into the harness initial state
or read back from the final state). Step-internal produce/require keys
stay inline in each StepIO -- the literal IS the contract declaration
(open-registry convention, HATS-261). YAML pipeline ids stay declared
config; the PIPELINE_* constants are the Python-side spellings.
"""

# Seeded by CLI / runners into the initial state.
KEY_ROLE = "role"
KEY_INTERACTIVE = "interactive"
KEY_PROJECT_DIR = "project_dir"
KEY_PROMPT_PATH = "prompt_path"
KEY_PROVIDER = "provider"
KEY_MODEL = "model"
KEY_ISOLATION = "isolation"
KEY_TICKET = "ticket"
KEY_TAGS = "tags"
KEY_EXTRA_ARGS = "extra_args"
KEY_COMPOSITION = "composition"
KEY_SESSION_MGR = "session_mgr"
KEY_TRACER_FACTORY = "tracer_factory"
KEY_MAX_RETRIES = "max_retries"

# Read back from the final state by CLI / runners.
KEY_SESSION_ID = "session_id"
KEY_SESSION_DIR = "session_dir"
KEY_CLAUDE_SESSION_ID = "claude_session_id"
KEY_EXIT_CODE = "exit_code"
KEY_ERRORS = "errors"
KEY_REVIEW_PATH = "review_path"
KEY_SAVED_PATH = "saved_path"
KEY_INTAKE_RESULT = "intake_result"

# Core pipeline names (Python-side).
PIPELINE_HUMAN = "human"
PIPELINE_EXECUTE = "execute"
PIPELINE_FINALIZE_HITL = "finalize-hitl"
PIPELINE_FINALIZE_SUBAGENT = "finalize-subagent"
PIPELINE_REFLECT_SESSION = "reflect-session"
PIPELINE_REFLECT_ALL = "reflect-all"
PIPELINE_REFLECT_HYPOTHESIS_PHASE1 = "reflect-hypothesis-phase1"
PIPELINE_REFLECT_HYPOTHESIS_PHASE2 = "reflect-hypothesis-phase2"
PIPELINE_REFLECT_ROLE = "reflect-role"
PIPELINE_REFLECT_ISSUE = "reflect-issue"

__all__ = [
    "KEY_ROLE",
    "KEY_INTERACTIVE",
    "KEY_PROJECT_DIR",
    "KEY_PROMPT_PATH",
    "KEY_PROVIDER",
    "KEY_MODEL",
    "KEY_ISOLATION",
    "KEY_TICKET",
    "KEY_TAGS",
    "KEY_EXTRA_ARGS",
    "KEY_COMPOSITION",
    "KEY_SESSION_MGR",
    "KEY_TRACER_FACTORY",
    "KEY_MAX_RETRIES",
    "KEY_SESSION_ID",
    "KEY_SESSION_DIR",
    "KEY_CLAUDE_SESSION_ID",
    "KEY_EXIT_CODE",
    "KEY_ERRORS",
    "KEY_REVIEW_PATH",
    "KEY_SAVED_PATH",
    "KEY_INTAKE_RESULT",
    "PIPELINE_HUMAN",
    "PIPELINE_EXECUTE",
    "PIPELINE_FINALIZE_HITL",
    "PIPELINE_FINALIZE_SUBAGENT",
    "PIPELINE_REFLECT_SESSION",
    "PIPELINE_REFLECT_ALL",
    "PIPELINE_REFLECT_HYPOTHESIS_PHASE1",
    "PIPELINE_REFLECT_HYPOTHESIS_PHASE2",
    "PIPELINE_REFLECT_ROLE",
    "PIPELINE_REFLECT_ISSUE",
]
