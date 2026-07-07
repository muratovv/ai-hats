"""``quorum_autoclose`` step — safe auto-close of refuted-quorum hypotheses.

HATS-769. Runs at the tail of ``finalize-hitl`` (the post-user-session
pipeline): after every HITL session, sweep active HYPs and close-as-gone any
that have reached a quorum of K independent ``refuted`` verdicts. The new
``refuted`` verdict that tips a HYP over the threshold is itself appended during
a HITL session (judge Phase-2 / reflect), whose ``finally`` block runs this
pipeline — so the close lands in the same session that reached quorum, no cron
needed.

Deterministic, no agent: the mutation lives in code, NOT in ADR-0007's L0
``judge-auditor`` (which stays read-only by construction). The quorum decision
core is the pure ``hypothesis.quorum`` module; this step is the thin pipeline
driver that resolves the store from ``project_dir`` and persists the result.

``failure_policy = "continue"``: a sweep hiccup must never orphan session
finalization — mirror of ``make_audit`` / ``compute_usage``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Mapping

from ..step import Step, StepIO

logger = logging.getLogger(__name__)


class QuorumAutoclose(Step):
    failure_policy = "continue"

    def __init__(self, params: Mapping[str, Any] | None = None) -> None:
        from ai_hats_tracker.hypothesis.quorum import DEFAULT_QUORUM_K

        params = params or {}
        self.k = int(params.get("k", DEFAULT_QUORUM_K))
        if self.k < 1:
            raise ValueError("quorum_autoclose: params.k must be >= 1")

    @property
    def io(self) -> StepIO:
        return StepIO(
            name="quorum_autoclose",
            requires=frozenset({"project_dir"}),
            produces=frozenset({"quorum_closed_hyps"}),
        )

    def run(self, *, project_dir: Path, **_: Any) -> dict[str, Any]:
        from ...hypothesis import HypothesisStore
        from ai_hats_tracker.hypothesis.quorum import autoclose_quorum
        from ...paths import hypotheses_dir

        store = HypothesisStore(hypotheses_dir(project_dir))
        closed = [c.hyp_id for c in autoclose_quorum(store, self.k)]
        if closed:
            logger.info("quorum_autoclose: closed %s", ", ".join(closed))
        # ADR-0005: omit the key (return {}) when nothing closed, never "" / [].
        return {"quorum_closed_hyps": closed} if closed else {}
