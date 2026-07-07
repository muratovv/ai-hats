"""Hypothesis backlog: pydantic models, atomic IO for HYP and PROP files.

Used by reflect-session role and ai-hats task hyp/proposal CLIs (HATS-210).
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
    next_hypothesis_id,
    next_proposal_id,
)
from .intake import (
    CreateAction,
    IntakeDraft,
    IntakeParseError,
    IntakeResult,
    MergeAction,
    parse_intake_yaml,
)

__all__ = [
    "Baseline",
    "CreateAction",
    "ExitCriteria",
    "Hypothesis",
    "HypothesisStatus",
    "HypothesisStore",
    "IntakeDraft",
    "IntakeParseError",
    "IntakeResult",
    "MergeAction",
    "Proposal",
    "ProposalCategory",
    "ProposalStatus",
    "ProposalStore",
    "ValidationLogEntry",
    "VerdictKind",
    "Vote",
    "next_hypothesis_id",
    "next_proposal_id",
    "parse_intake_yaml",
]
