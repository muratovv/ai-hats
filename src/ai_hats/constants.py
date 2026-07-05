"""Layout-name constants — a dependency-free leaf module (HATS-758).

The modules extracted from ``assembler`` in HATS-715 (``relocation``,
``migrations``) need these layout names but must not import them back from the
high-level ``assembler`` "god module" — that re-created a module-level import
cycle. Keeping the names in a leaf with **no internal imports** lets every layer
share them without any cycle. ``assembler`` re-imports them, so
``from ai_hats.assembler import AGENT_DIR`` keeps working unchanged.
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


# HATS-867: trace-tag vocabulary — leaf home so runtime bricks need no observe import
class TraceTag:
    REQ = "[REQ]"
    RES = "[RES]"
    ACT = "[ACT]"
    TOOL = "[TOOL]"
    SYS = "[SYS]"
    SUB = "[SUB]"
