"""Hypothesis backlog: pydantic models, atomic IO for HYP and PROP files.

Used by reflect-session role and ai-hats hyp/proposal CLIs (HATS-210).
"""

from .model import (
    Baseline,
    ExitCriteria,
    Hypothesis,
    HypothesisStatus,
    ValidationLogEntry,
    VerdictKind,
)
from .proposal import (
    Proposal,
    ProposalCategory,
    ProposalStatus,
    Vote,
)
from .io import (
    HypothesisStore,
    ProposalStore,
    next_proposal_id,
)

__all__ = [
    "Baseline",
    "ExitCriteria",
    "Hypothesis",
    "HypothesisStatus",
    "HypothesisStore",
    "Proposal",
    "ProposalCategory",
    "ProposalStatus",
    "ProposalStore",
    "ValidationLogEntry",
    "VerdictKind",
    "Vote",
    "next_proposal_id",
]
