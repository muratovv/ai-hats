"""Back-compat shim — attachments moved to ai_hats_tracker (HATS-933)."""

from ai_hats_tracker.attachments import (  # noqa: F401
    DIGEST_LEN,
    Divergence,
    DivergenceKind,
    FileOp,
    ReconcileAction,
    ReconcileResult,
    attachments_dir,
    compute_digest,
    is_binary,
    is_git_tracked,
    reconcile,
    verify_manifest,
)
