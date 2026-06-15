"""Layout-name constants — a dependency-free leaf module (HATS-758).

The modules extracted from ``assembler`` in HATS-715 (``relocation``,
``migrations``) need these layout names but must not import them back from the
high-level ``assembler`` "god module" — that re-created a module-level import
cycle. Keeping the names in a leaf with **no internal imports** lets every layer
share them without any cycle. ``assembler`` re-imports them, so
``from ai_hats.assembler import AGENT_DIR`` keeps working unchanged.
"""

AGENT_DIR = ".agent"
PROJECT_CONFIG = "ai-hats.yaml"
GITIGNORE_FILE = ".gitignore"

# HATS-282 — canonical layered layer
CANONICAL_DIR = "ai-hats"
CANONICAL_MANIFEST = "MANAGED"
USER_RULES_SUBDIR = "user-rules"
