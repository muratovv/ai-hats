"""Back-compat shim — linked_context moved to ai_hats_tracker (HATS-933)."""

from ai_hats_tracker.linked_context import (  # noqa: F401
    load_linked_context,
    load_ticket,
    render_linked_card,
)
