"""Stock field validators for the HYP/PROP backlogs (HATS-1044, ADR-0017 §4).

Plain ``Callable[[Any], None]`` raising ``ValueError`` (the CardSchema contract),
resolved against the open registry at composition. Each ports a tracker pydantic
shape WITHOUT importing the tracker (import-hygiene): the write-strict half of the
read-tolerant/write-strict policy — a NEW write must match the tracker model an
old card is already validated against, so the two worlds never diverge on writes.
"""

from __future__ import annotations

from typing import Any

#: ValidationLogEntry.VerdictKind (tracker model.py) — ``n/a`` kept so a migrated
#: entry is never rejected on a later append (the tracker validates it too).
_VERDICTS = frozenset({"confirmed", "refuted", "inconclusive", "n/a"})
#: ValidationLogEntry.RecommendationKind (tracker model.py).
_RECOMMENDATIONS = frozenset({"close_confirmed", "close_refuted", "keep", "extend_window"})
#: ExitCriteria fields (tracker model.py, ``extra="forbid"``).
_EXIT_KEYS = frozenset({"confirm", "refute", "stalled"})
#: Vote fields (tracker proposal.py, ``extra="forbid"``).
_VOTE_KEYS = frozenset({"session_id", "timestamp", "reasoning"})


def _str_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def hyp_validation_log(value: Any) -> None:
    """List of ValidationLogEntry (extra keys tolerated, tracker ``extra="allow"``)."""
    if not isinstance(value, list):
        raise ValueError("validation_log must be a list of entries")
    for i, entry in enumerate(value):
        if not isinstance(entry, dict):
            raise ValueError(f"validation_log entry {i} must be a mapping")
        if entry.get("verdict") not in _VERDICTS:
            raise ValueError(
                f"validation_log entry {i}: verdict must be one of {sorted(_VERDICTS)}"
            )
        evidence = entry.get("evidence")
        if not isinstance(evidence, str) or not evidence.strip():
            raise ValueError(f"validation_log entry {i}: 'evidence' must be a non-empty string")
        if not entry.get("date"):
            raise ValueError(f"validation_log entry {i}: 'date' is required")
        rec = entry.get("recommendation")
        if rec is not None and rec not in _RECOMMENDATIONS:
            raise ValueError(
                f"validation_log entry {i}: recommendation must be one of {sorted(_RECOMMENDATIONS)}"
            )


def hyp_exit_criteria(value: Any) -> None:
    """The strict ExitCriteria shape (``extra="forbid"``); ``None`` is allowed
    (the field is ``ExitCriteria | None`` in the tracker model)."""
    if value is None:
        return
    if not isinstance(value, dict):
        raise ValueError("exit_criteria must be a mapping")
    unknown = sorted(set(value) - _EXIT_KEYS)
    if unknown:
        raise ValueError(f"exit_criteria forbids unknown key(s) {unknown}; allowed: {sorted(_EXIT_KEYS)}")
    for key in _EXIT_KEYS:
        if key in value and not _str_list(value[key]):
            raise ValueError(f"exit_criteria.{key} must be a list of strings")


def prop_vote_entries(value: Any) -> None:
    """List of Vote (``extra="forbid"``): session_id/timestamp/reasoning required."""
    if not isinstance(value, list):
        raise ValueError("votes must be a list of vote entries")
    for i, entry in enumerate(value):
        if not isinstance(entry, dict):
            raise ValueError(f"vote {i} must be a mapping")
        unknown = sorted(set(entry) - _VOTE_KEYS)
        if unknown:
            raise ValueError(f"vote {i}: Vote forbids unknown key(s) {unknown}")
        sid = entry.get("session_id")
        if not isinstance(sid, str) or not sid.strip():
            raise ValueError(f"vote {i}: 'session_id' must be a non-empty string")
        reasoning = entry.get("reasoning")
        if not isinstance(reasoning, str) or not reasoning.strip():
            raise ValueError(f"vote {i}: 'reasoning' must be a non-empty string")
        if "timestamp" not in entry:
            raise ValueError(f"vote {i}: 'timestamp' is required")
