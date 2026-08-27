"""Layout-name constants — a dependency-free leaf module (HATS-758).

The modules extracted from ``assembler`` in HATS-715 (``relocation``,
``migrations``) need these layout names but must not import them back from the
high-level ``assembler`` "god module" — that re-created a module-level import
cycle. Keeping the names in a leaf with **no internal imports** lets every layer
share them without any cycle. ``assembler`` re-imports them, so
``from ai_hats.assembler import AGENT_DIR`` keeps working unchanged.
HATS-948: ``TraceTag``/``ENV_SESSION_ID`` moved to ``ai_hats_observe.trace``.
HATS-1613: the one exception to "no internal imports" is the ``env`` leaf, which
the leaf gate exempts by name — ``ENV_ROLE``/``ENV_ROOT_PID`` are re-exported
from there rather than re-declared (ADR-0025 D1).
"""  # comment-length: allow — the leaf's import contract is the point of the module

from .env import ENV_ROLE as ENV_ROLE, ENV_ROOT_PID as ENV_ROOT_PID

AGENT_DIR = ".agent"
GITIGNORE_FILE = ".gitignore"

# HATS-1521: the interpreter `self init` / `self update` provision. A leaf home so
# the venv builder and the session-start check share one value; the hand-written
# copies (launcher, floors, CI) are held to it by scripts/check_python_pin.py.
PINNED_PYTHON = "3.13"

# HATS-282 — canonical layered layer
CANONICAL_DIR = "ai-hats"
CANONICAL_MANIFEST = "MANAGED"
USER_RULES_SUBDIR = "user-rules"
# HATS-1617: resolution contract the host launcher must implement. Bump only when
# the launcher's resolution behaviour changes — never for comments or a release.
# Paired with the `LAUNCHER_CONTRACT=` literal in scripts/ai-hats-launcher.
LAUNCHER_CONTRACT = 1
LAUNCHER_CONTRACT_FILE = "launcher-contract"


# Env-var names shared across modules (HATS-917); single-file knobs stay local.
# (ENV_SESSION_ID lives in ai_hats_observe.trace — observe's schema, HATS-948.)
ENV_REPO_URL = "AI_HATS_REPO_URL"
# HATS-938: launcher → `self init` channel for the editable host source, so init
# seeds `harness.channel: local` without depending on which interpreter it runs under.
ENV_AI_HATS_INIT_SRC = "AI_HATS_INIT_SRC"
ENV_LAUNCHER_DEST = "AI_HATS_LAUNCHER_DEST"
ENV_SKIP_RETRO = "HATS_SKIP_RETRO"
ENV_AI_HATS_DEBUG = "AI_HATS_DEBUG"
ENV_AI_HATS_VERBOSE = "AI_HATS_VERBOSE"
ENV_PTY_IN_FD = "AI_HATS_PTY_IN_FD"
ENV_PTY_OUT_FD = "AI_HATS_PTY_OUT_FD"
DEBUG_FLAGS = frozenset({"--debug", "--verbose", "-v"})

# Consent's own artefacts. Whether one of these crosses into a child is the consent
# engine's answer, not this seam's (HATS-1738 / HATS-1739), so the shape test below
# carves them out rather than withholding them.
CONSENT_OWNED_KEYS = frozenset(
    {
        "AI_HATS_CONSENT_ACK",
        "AI_HATS_CONSENT_TICKET",
        "AI_HATS_MERGE_ACK",
        "AI_HATS_PLAN_ACK",
    }
)

#: How a withheld approval is SPELLED. Recognising one by shape rather than by a
#: roster is what makes the seam fail closed: a gate flag added next month — here or
#: in a project that only consumes ai-hats — is withheld from a sub-agent with nobody
#: remembering to declare it. A roster fails the other way, and did: it was written
#: from the shipped-hook vocabulary and so missed `AI_HATS_E2E_CATALOG_ACK`, which a
#: repo script reads (HATS-1743 review).
BYPASS_FLAG_SUFFIXES = ("_ACK", "_OFF", "_SKIP")


def withheld_from_subagent(name: str) -> bool:
    """Is ``name`` an approval the parent holds that its sub-agent must not?"""
    if not name.startswith("AI_HATS_") or name in CONSENT_OWNED_KEYS:
        return False
    return name == "AI_HATS_YOLO" or name.endswith(BYPASS_FLAG_SUFFIXES)


# The flags this repo KNOWS are withheld — every one of them also answers the shape
# test above, which is what actually holds the line. Naming them keeps the launch
# record identical on every machine (the dry-run-equals-the-launch invariant) and
# gives the reader the roster; forgetting one now costs a line in that record, not
# the withholding itself.
BYPASS_FLAGS_NOT_INHERITED = frozenset(
    {
        "AI_HATS_BACKLOG_GATE_OFF",
        "AI_HATS_COMMENT_LINT_OFF",
        "AI_HATS_DESTRUCTIVE_ACK",
        "AI_HATS_DOCS_INDEX_ACK",
        "AI_HATS_E2E_CATALOG_ACK",
        "AI_HATS_GIT_GATE_BROKEN_ACK",
        "AI_HATS_NO_RAW_DESTRUCTIVE_SKIP",
        "AI_HATS_PRIVACY_ACK",
        "AI_HATS_RULE_DELIVERY_ACK",
        "AI_HATS_SECURITY_LINT_OFF",
        "AI_HATS_SHARED_STATE_ACK",
        "AI_HATS_SKILL_LINT_ACK",
        "AI_HATS_SMOKE_SKIP",
        "AI_HATS_TICKET_IDS_ACK",
        "AI_HATS_TOOL_HYGIENE_OFF",
        "AI_HATS_WT_ENTRY_OFF",
        "AI_HATS_WT_GATE_OFF",
        "AI_HATS_WT_INTERP_OFF",
        "AI_HATS_YOLO",
    }
)


def is_debug_mode(argv: list[str] | None = None) -> bool:
    """Return True if debug or verbose mode is enabled via env or CLI flags (HATS-1120)."""
    import os
    import sys

    if os.environ.get(ENV_AI_HATS_DEBUG) == "1" or os.environ.get(ENV_AI_HATS_VERBOSE) == "1":
        return True
    args = argv if argv is not None else sys.argv[1:]
    return any(arg in DEBUG_FLAGS for arg in args)


# Claude Code hook-event names (HATS-917). Engine vocabularies (HATS-915)
# compose from these; leaf home so libraries/models needs no providers import.
HOOK_PRE_TOOL_USE = "PreToolUse"
HOOK_POST_TOOL_USE = "PostToolUse"
HOOK_SESSION_START = "SessionStart"
HOOK_SESSION_END = "SessionEnd"
HOOK_USER_PROMPT_SUBMIT = "UserPromptSubmit"
HOOK_STOP = "Stop"
HOOK_SUBAGENT_STOP = "SubagentStop"
HOOK_NOTIFICATION = "Notification"


# Surface registry names (HATS-917) — leaf home: runners must not import providers.
# Only the builtin lives here; agy/cline are out-of-tree surfaces (own their names).
PROVIDER_CLAUDE = "claude"

INJECTION_START = "<!-- AI-HATS:START -->"
INJECTION_END = "<!-- AI-HATS:END -->"

# HATS-284: lowercase scaffold markers — used in `./CLAUDE.md` to delimit the
# user-owned ai-hats block. PUBLISH_AGGREGATOR_* names kept for backwards
# compatibility of imports across the codebase; functionally these are the
# scaffold markers.
PUBLISH_AGGREGATOR_START = "<!-- ai-hats:start -->"
PUBLISH_AGGREGATOR_END = "<!-- ai-hats:end -->"
