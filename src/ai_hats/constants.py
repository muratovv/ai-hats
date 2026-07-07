"""Layout-name constants — a dependency-free leaf module (HATS-758).

The modules extracted from ``assembler`` in HATS-715 (``relocation``,
``migrations``) need these layout names but must not import them back from the
high-level ``assembler`` "god module" — that re-created a module-level import
cycle. Keeping the names in a leaf with **no internal imports** lets every layer
share them without any cycle. ``assembler`` re-imports them, so
``from ai_hats.assembler import AGENT_DIR`` keeps working unchanged.
HATS-948: ``TraceTag``/``ENV_SESSION_ID`` moved to ``ai_hats_observe.trace``.
"""

AGENT_DIR = ".agent"
GITIGNORE_FILE = ".gitignore"

# HATS-282 — canonical layered layer
CANONICAL_DIR = "ai-hats"
CANONICAL_MANIFEST = "MANAGED"
USER_RULES_SUBDIR = "user-rules"

# HATS-700: the ONLY rule-delivery channel — body delivered iff named here;
# adding one is a deliberate prompt-budget decision. HATS-865: leaf home.
ALWAYS_ON_RULES = {
    "global_rule_destructive_actions",
    "global_rule_resource_hygiene",
    "dev_rule_secure_coding",
    "dev_rule_tool_call_hygiene",
    # HATS-437: primary defense against autonomous shared-state writes.
    # The PreToolUse / pre-push hooks are a safety net for this rule.
    "rule_pause_before_shared_state_write",
    # HATS-452: always-on architectural guard; rationale docs/adr/0005-*.md
    "rule_composition_value_contract",
    # HATS-842: promoted so the agent reads the exact few-shot guide at
    # authoring time; dropped from SUMMARIZED_IN_INJECTION.
    "dev_rule_comment_discipline",
}


# Env-var names shared across modules (HATS-917); single-file knobs stay local.
# (ENV_SESSION_ID lives in ai_hats_observe.trace — observe's schema, HATS-948.)
ENV_REPO_URL = "AI_HATS_REPO_URL"
# HATS-938: launcher → `self init` channel for the editable host source, so init
# seeds `harness.channel: local` without depending on which interpreter it runs under.
ENV_AI_HATS_INIT_SRC = "AI_HATS_INIT_SRC"
ENV_ROLE = "AI_HATS_ROLE"
ENV_LAUNCHER_DEST = "AI_HATS_LAUNCHER_DEST"
ENV_SKIP_RETRO = "HATS_SKIP_RETRO"


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


# Provider registry names (HATS-917) — leaf home: runners must not import providers.
PROVIDER_CLAUDE = "claude"
PROVIDER_GEMINI = "gemini"
