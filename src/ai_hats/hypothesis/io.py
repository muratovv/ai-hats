"""Atomic load/save for HYP and PROP YAML files with filelock protection.

Lock semantics: per-resource lock file alongside the YAML
(`HYP-008.yaml.lock`, `PROP-001.yaml.lock`). Concurrent appenders block.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import yaml
from filelock import FileLock

from .model import Hypothesis, ValidationLogEntry
from .proposal import Proposal, Vote

_HYP_FILE_RE = re.compile(r"^HYP-(\d+).*\.ya?ml$")
_PROP_FILE_RE = re.compile(r"^PROP-(\d+)\.ya?ml$")


def _atomic_dump(path: Path, data: dict) -> None:
    """Write YAML atomically: tmp file in same dir + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    tmp.replace(path)


def _lock_for(path: Path) -> FileLock:
    return FileLock(str(path) + ".lock")


class HypothesisStore:
    """Read/write HYP-*.yaml under .agent/hypotheses/."""

    def __init__(self, hypotheses_dir: Path) -> None:
        self.dir = hypotheses_dir

    def path(self, hyp_id: str) -> Path:
        # Lookup by id prefix (filename may carry a slug after id).
        if self.dir.exists():
            for p in sorted(self.dir.iterdir()):
                m = _HYP_FILE_RE.match(p.name)
                if m and f"HYP-{int(m.group(1)):03d}" == hyp_id:
                    return p
        return self.dir / f"{hyp_id}.yaml"

    def load(self, hyp_id: str) -> Hypothesis:
        p = self.path(hyp_id)
        raw = yaml.safe_load(p.read_text()) or {}
        return Hypothesis.model_validate(raw)

    def list_all(self) -> list[Hypothesis]:
        result: list[Hypothesis] = []
        if not self.dir.exists():
            return result
        for p in sorted(self.dir.iterdir()):
            if not _HYP_FILE_RE.match(p.name):
                continue
            try:
                raw = yaml.safe_load(p.read_text()) or {}
                result.append(Hypothesis.model_validate(raw))
            except Exception:  # noqa: BLE001 — keep listing partial-failures explicit
                continue
        return result

    def list_active(self) -> list[Hypothesis]:
        return [h for h in self.list_all() if h.status == "active"]

    def append_verdict(
        self,
        hyp_id: str,
        entry: ValidationLogEntry,
    ) -> Hypothesis:
        """Append one ValidationLogEntry under filelock; preserves all extras."""
        p = self.path(hyp_id)
        with _lock_for(p):
            raw = yaml.safe_load(p.read_text()) or {}
            log = list(raw.get("validation_log") or [])
            log.append(entry.model_dump(mode="json", exclude_none=True))
            raw["validation_log"] = log
            _atomic_dump(p, raw)
            return Hypothesis.model_validate(raw)

    def append_then_set_status(
        self,
        hyp_id: str,
        entry: ValidationLogEntry,
        *,
        status: str,
        only_if_status: str | None = None,
    ) -> Hypothesis | None:
        """Append a verdict AND flip status under a SINGLE filelock (atomic).

        ``only_if_status`` guards the write: when set and the on-disk status
        differs, make no change and return ``None``. This makes a quorum
        auto-close (HATS-769) safe against a concurrent/repeat close — the
        verdict append and the status flip cannot interleave with another
        closer, so no duplicate synthetic entry and no double-close.
        """
        p = self.path(hyp_id)
        with _lock_for(p):
            raw = yaml.safe_load(p.read_text()) or {}
            if only_if_status is not None and raw.get("status") != only_if_status:
                return None
            log = list(raw.get("validation_log") or [])
            log.append(entry.model_dump(mode="json", exclude_none=True))
            raw["validation_log"] = log
            raw["status"] = status
            _atomic_dump(p, raw)
            return Hypothesis.model_validate(raw)

    def save(self, hypothesis: Hypothesis, *, preserve_extras: dict | None = None) -> Path:
        """Write the full Hypothesis. If preserve_extras given, merge unknown keys."""
        p = self.path(hypothesis.id)
        with _lock_for(p):
            data = hypothesis.model_dump(mode="json", exclude_none=True)
            if preserve_extras:
                for k, v in preserve_extras.items():
                    data.setdefault(k, v)
            _atomic_dump(p, data)
            return p

    def create(self, hypothesis: Hypothesis) -> Path:
        p = self.path(hypothesis.id)
        with _lock_for(p):
            if p.exists():
                raise FileExistsError(f"{hypothesis.id} already exists at {p}")
            _atomic_dump(p, hypothesis.model_dump(mode="json", exclude_none=True))
            return p

    def set_status(self, hyp_id: str, status: str) -> Hypothesis:
        p = self.path(hyp_id)
        with _lock_for(p):
            raw = yaml.safe_load(p.read_text()) or {}
            raw["status"] = status
            _atomic_dump(p, raw)
            return Hypothesis.model_validate(raw)


class ProposalStore:
    """Read/write PROP-*.yaml under .agent/backlog/proposals/."""

    def __init__(self, proposals_dir: Path) -> None:
        self.dir = proposals_dir

    def path(self, prop_id: str) -> Path:
        return self.dir / f"{prop_id}.yaml"

    def load(self, prop_id: str) -> Proposal:
        p = self.path(prop_id)
        raw = yaml.safe_load(p.read_text()) or {}
        return Proposal.model_validate(raw)

    def list_all(self) -> list[Proposal]:
        result: list[Proposal] = []
        if not self.dir.exists():
            return result
        for p in sorted(self.dir.iterdir()):
            if not _PROP_FILE_RE.match(p.name):
                continue
            try:
                raw = yaml.safe_load(p.read_text()) or {}
                result.append(Proposal.model_validate(raw))
            except Exception:  # noqa: BLE001
                continue
        return result

    def filter(
        self,
        *,
        status: str | None = None,
        category: str | None = None,
        target: str | None = None,
    ) -> list[Proposal]:
        out = self.list_all()
        if status is not None:
            out = [p for p in out if p.status == status]
        if category is not None:
            out = [p for p in out if p.category == category]
        if target is not None:
            out = [p for p in out if p.target == target]
        return out

    def create(self, proposal: Proposal) -> Path:
        p = self.path(proposal.id)
        with _lock_for(p):
            if p.exists():
                raise FileExistsError(f"{proposal.id} already exists at {p}")
            _atomic_dump(p, proposal.model_dump(mode="json", exclude_none=True))
            return p

    def add_vote(self, prop_id: str, vote: Vote) -> Proposal:
        p = self.path(prop_id)
        with _lock_for(p):
            raw = yaml.safe_load(p.read_text()) or {}
            votes = list(raw.get("votes") or [])
            votes.append(vote.model_dump(mode="json", exclude_none=True))
            raw["votes"] = votes
            _atomic_dump(p, raw)
            return Proposal.model_validate(raw)

    def set_status(self, prop_id: str, status: str) -> Proposal:
        p = self.path(prop_id)
        with _lock_for(p):
            raw = yaml.safe_load(p.read_text()) or {}
            raw["status"] = status
            _atomic_dump(p, raw)
            return Proposal.model_validate(raw)


def next_proposal_id(proposals_dir: Path) -> str:
    """Compute next PROP-NNN id by scanning existing files."""
    max_n = 0
    if proposals_dir.exists():
        for p in proposals_dir.iterdir():
            m = _PROP_FILE_RE.match(p.name)
            if m:
                max_n = max(max_n, int(m.group(1)))
    return f"PROP-{max_n + 1:03d}"


def next_hypothesis_id(hypotheses_dir: Path) -> str:
    """Compute next HYP-NNN id by scanning existing files."""
    max_n = 0
    if hypotheses_dir.exists():
        for p in hypotheses_dir.iterdir():
            m = _HYP_FILE_RE.match(p.name)
            if m:
                max_n = max(max_n, int(m.group(1)))
    return f"HYP-{max_n + 1:03d}"


def utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)
