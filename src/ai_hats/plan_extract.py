"""Back-compat shim — plan_extract moved to ai_hats_tracker (HATS-933)."""

from ai_hats_tracker.plan_extract import (  # noqa: F401
    Candidate,
    extract_candidates,
    mark_extracted,
)
