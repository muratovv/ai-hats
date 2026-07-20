"""Atomic load/save for HYP and PROP YAML files with filelock protection.

Dual-layout (HATS-1044 compat shim): a catalog is either LEGACY flat
(``<dir>/HYP-008.yaml``) or migrated DIR-PER-CARD (``<dir>/HYP-008/task.yaml``),
and this store reads/writes both so ``ai-hats task hyp/proposal`` keeps working
across the migration. Lock alignment: a dir-per-card write locks
``<dir>/<ID>/.lock`` — the SAME path the rack kernel locks
(``kernel._task_lock``), by path CONVENTION (the tracker must not import the
rack), so a rack-API write and a tracker-CLI write on one migrated card never
lose an update. The migrated ``task.yaml`` stores the rack ``state`` (not
``status``) and carries link kinds (``source_task`` …) in a ``links`` map; the
shim translates both directions on load/save, preserving everything else.
"""  # comment-length: allow

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import yaml
from filelock import FileLock

from ai_hats_core import atomic_write_text
from .model import Hypothesis, ValidationLogEntry
from .proposal import Proposal, Vote

_HYP_FILE_RE = re.compile(r"^HYP-(\d+).*\.ya?ml$")
_PROP_FILE_RE = re.compile(r"^PROP-(\d+)\.ya?ml$")
_HYP_DIR_RE = re.compile(r"^HYP-(\d+)$")
_PROP_DIR_RE = re.compile(r"^PROP-(\d+)$")

#: Link kinds (name → arity) of the packaged rack HYP/PROP definitions. The shim
#: mirrors the rack mapping by CONVENTION (no rack import): ``status``↔``state``
#: and these scalars↔the ``links`` map.
_HYP_LINKS = {"source_task": "one", "supersedes": "one", "superseded_by": "one"}
_PROP_LINKS = {"related_hypotheses": "many"}

#: Rack anchor columns a migrated HYP card may carry that are NOT tracker HYP
#: fields — dropped from the reconstructed model view (HYP is ``extra="allow"``,
#: so this is cosmetic clean-up, never data loss on disk).
_RACK_ANCHORS = frozenset(
    {
        "state", "links", "description", "priority", "assignee", "reviewer", "role",
        "parent_task", "subtasks", "depends_on", "related", "see_also", "folded_into",
        "tags", "work_log", "final_state", "resolution", "completed_at", "updated",
    }
)
#: Proposal's declared fields (tracker model, ``extra="forbid"``) — the ONLY keys
#: kept when reconstructing a Proposal from a rack card.
_PROP_FIELDS = frozenset(
    {
        "id", "created", "title", "category", "target", "description", "rationale",
        "related_hypotheses", "votes", "status", "failed_session_id",
    }
)


def _atomic_dump(path: Path, data: dict) -> None:
    """Write YAML atomically via the canonical helper (HATS-716)."""
    atomic_write_text(path, yaml.safe_dump(data, sort_keys=False, allow_unicode=True))


def _is_dir_card(path: Path) -> bool:
    return path.name == "task.yaml"


def _lock_for(path: Path) -> FileLock:
    # Dir-per-card locks on the rack's `<dir>/<ID>/.lock` (path convention with
    # kernel._task_lock); a legacy flat file keeps its `<file>.lock`.
    if _is_dir_card(path):
        return FileLock(str(path.parent / ".lock"))
    return FileLock(str(path) + ".lock")


def _status_key(path: Path) -> str:
    return "state" if _is_dir_card(path) else "status"


# ----- rack <-> tracker translation (dir-per-card only) -----------------------


#: HYP fields the tracker models as ``date`` but the rack may stamp as a full
#: timestamp (``created`` by the kernel, ``closed`` by ``stamp-lifecycle``) — the
#: shim trims them back to the date part on load.
_HYP_DATE_FIELDS = ("created", "closed", "last_rule_revision_date", "last_judge_protocol_revision_date")


def _hyp_from_rack(raw: dict) -> dict:
    """Rack task.yaml dict → tracker Hypothesis dict."""
    d = {k: v for k, v in raw.items() if k not in _RACK_ANCHORS}
    d["status"] = raw.get("state", raw.get("status"))
    links = raw.get("links") or {}
    for name in _HYP_LINKS:  # all arity one
        ids = links.get(name)
        if ids:
            d[name] = ids[0]
    for name in _HYP_DATE_FIELDS:
        value = d.get(name)
        if isinstance(value, str) and "T" in value:
            d[name] = value[:10]
    return d


def _prop_from_rack(raw: dict) -> dict:
    """Rack task.yaml dict → tracker Proposal dict (declared fields only)."""
    d = {k: v for k, v in raw.items() if k in _PROP_FIELDS}
    d["status"] = raw.get("state", raw.get("status"))
    links = raw.get("links") or {}
    ids = links.get("related_hypotheses")
    if ids:
        d["related_hypotheses"] = list(ids)
    return d


def _to_rack(model_dict: dict, link_arity: dict[str, str]) -> dict:
    """Tracker model dict → rack task.yaml dict (``status``→``state``, link
    scalars→the ``links`` map). The inverse of ``_*_from_rack``."""
    out: dict = {
        "id": model_dict["id"],
        "title": model_dict.get("title", ""),
        "state": model_dict.get("status", ""),
    }
    links: dict[str, list] = {}
    for key, value in model_dict.items():
        if key in ("id", "title", "status"):
            continue
        if key in link_arity:
            ids = [value] if link_arity[key] == "one" else list(value)
            ids = [v for v in ids if v]
            if ids:
                links[key] = ids
            continue
        out[key] = value
    if links:
        out["links"] = links
    return out


def _max_id(dir_: Path, file_re: re.Pattern, dir_re: re.Pattern) -> int:
    """Highest N across BOTH flat ``<ID>[-slug].yaml`` files and ``<ID>/`` dirs
    (they coexist post-migration)."""
    max_n = 0
    if dir_.exists():
        for p in dir_.iterdir():
            m = (dir_re.match(p.name) if p.is_dir() else file_re.match(p.name))
            if m:
                max_n = max(max_n, int(m.group(1)))
    return max_n


class HypothesisStore:
    """Read/write HYP cards (flat or dir-per-card) with a dual-PATH catalog split.

    HATS-1054: dir-cards + ``backlog.yaml`` + all writes live under ``hypotheses_dir``
    (the new ``tracker/backlog/hypotheses`` catalog); legacy flat ``HYP-*.yaml`` files
    are read as a fallback from ``flat_dir`` (the old ``tracker/hypotheses``). Read
    resolution order: (1) dir-card in ``hypotheses_dir``, (2) flat file in
    ``hypotheses_dir``, (3) flat file in ``flat_dir``. When ``flat_dir`` is omitted it
    defaults to ``hypotheses_dir`` (single-dir standalone — byte-identical behavior)."""  # comment-length: allow

    def __init__(self, hypotheses_dir: Path, *, flat_dir: Path | None = None) -> None:
        self.dir = hypotheses_dir
        self.flat_dir = flat_dir if flat_dir is not None else hypotheses_dir

    def _flat_dirs(self) -> list[Path]:
        """Dirs scanned for legacy flat ``HYP-*.yaml`` files, primary first — the
        legacy ``flat_dir`` is appended only when it differs from the catalog."""
        return [self.dir] if self.flat_dir == self.dir else [self.dir, self.flat_dir]

    def _dir_mode(self) -> bool:
        """A migrated catalog carries a ``backlog.yaml``; new writes then go
        dir-per-card so the rack sees them."""
        return (self.dir / "backlog.yaml").is_file()

    def path(self, hyp_id: str) -> Path:
        dir_card = self.dir / hyp_id / "task.yaml"
        if dir_card.exists():
            return dir_card
        for d in self._flat_dirs():
            if not d.exists():
                continue
            for p in sorted(d.iterdir()):
                if not p.is_file():
                    continue
                m = _HYP_FILE_RE.match(p.name)
                if m and f"HYP-{int(m.group(1)):03d}" == hyp_id:
                    return p
        return dir_card if self._dir_mode() else self.dir / f"{hyp_id}.yaml"

    def _to_hyp(self, raw: dict, path: Path) -> Hypothesis:
        return Hypothesis.model_validate(_hyp_from_rack(raw) if _is_dir_card(path) else raw)

    def load(self, hyp_id: str) -> Hypothesis:
        p = self.path(hyp_id)
        raw = yaml.safe_load(p.read_text()) or {}
        return self._to_hyp(raw, p)

    def list_all(self) -> list[Hypothesis]:
        result: list[Hypothesis] = []
        if not self.dir.exists():
            return result
        seen: set[str] = set()
        for cdir in sorted(self.dir.iterdir()):
            p = cdir / "task.yaml"
            if not (cdir.is_dir() and p.is_file()):
                continue
            try:
                h = Hypothesis.model_validate(_hyp_from_rack(yaml.safe_load(p.read_text()) or {}))
            except Exception:  # noqa: BLE001, S112 — keep listing partial-failures explicit
                continue
            seen.add(h.id)
            result.append(h)
        for d in self._flat_dirs():
            if not d.exists():
                continue
            for p in sorted(d.iterdir()):
                if not (p.is_file() and _HYP_FILE_RE.match(p.name)):
                    continue
                try:
                    h = Hypothesis.model_validate(yaml.safe_load(p.read_text()) or {})
                except Exception:  # noqa: BLE001, S112
                    continue
                if h.id in seen:  # a dir-per-card card (or nearer flat dir) shadows this
                    continue
                seen.add(h.id)
                result.append(h)
        result.sort(key=lambda h: _id_key(h.id))
        return result

    def list_active(self) -> list[Hypothesis]:
        return [h for h in self.list_all() if h.status == "active"]

    def next_id(self) -> str:
        """Next ``HYP-NNN`` across the catalog AND the legacy flat dir (they coexist
        post-migration) — the alloc must not reuse an id present in either."""
        max_n = max((_max_id(d, _HYP_FILE_RE, _HYP_DIR_RE) for d in self._flat_dirs()), default=0)
        return f"HYP-{max_n + 1:03d}"

    def append_verdict(self, hyp_id: str, entry: ValidationLogEntry) -> Hypothesis:
        """Append one ValidationLogEntry under filelock; preserves all extras."""
        p = self.path(hyp_id)
        with _lock_for(p):
            raw = yaml.safe_load(p.read_text()) or {}
            log = list(raw.get("validation_log") or [])
            log.append(entry.model_dump(mode="json", exclude_none=True))
            raw["validation_log"] = log
            _atomic_dump(p, raw)
            return self._to_hyp(raw, p)

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
        differs, make no change and return ``None`` (HATS-769 safe auto-close)."""
        p = self.path(hyp_id)
        key = _status_key(p)
        with _lock_for(p):
            raw = yaml.safe_load(p.read_text()) or {}
            if only_if_status is not None and raw.get(key) != only_if_status:
                return None
            log = list(raw.get("validation_log") or [])
            log.append(entry.model_dump(mode="json", exclude_none=True))
            raw["validation_log"] = log
            raw[key] = status
            _atomic_dump(p, raw)
            return self._to_hyp(raw, p)

    def save(self, hypothesis: Hypothesis, *, preserve_extras: dict | None = None) -> Path:
        """Write the full Hypothesis. If preserve_extras given, merge unknown keys."""
        p = self.path(hypothesis.id)
        with _lock_for(p):
            data = hypothesis.model_dump(mode="json", exclude_none=True)
            if preserve_extras:
                for k, v in preserve_extras.items():
                    data.setdefault(k, v)
            _atomic_dump(p, _to_rack(data, _HYP_LINKS) if _is_dir_card(p) else data)
            return p

    def create(self, hypothesis: Hypothesis) -> Path:
        p = self.path(hypothesis.id)
        with _lock_for(p):
            if p.exists():
                raise FileExistsError(f"{hypothesis.id} already exists at {p}")
            data = hypothesis.model_dump(mode="json", exclude_none=True)
            p.parent.mkdir(parents=True, exist_ok=True)
            _atomic_dump(p, _to_rack(data, _HYP_LINKS) if _is_dir_card(p) else data)
            return p

    def set_status(self, hyp_id: str, status: str) -> Hypothesis:
        p = self.path(hyp_id)
        with _lock_for(p):
            raw = yaml.safe_load(p.read_text()) or {}
            raw[_status_key(p)] = status
            _atomic_dump(p, raw)
            return self._to_hyp(raw, p)


class ProposalStore:
    """Read/write PROP cards under ``proposals_dir`` (flat or dir-per-card)."""

    def __init__(self, proposals_dir: Path) -> None:
        self.dir = proposals_dir

    def _dir_mode(self) -> bool:
        return (self.dir / "backlog.yaml").is_file()

    def path(self, prop_id: str) -> Path:
        dir_card = self.dir / prop_id / "task.yaml"
        if dir_card.exists():
            return dir_card
        flat = self.dir / f"{prop_id}.yaml"
        if flat.exists():
            return flat
        return dir_card if self._dir_mode() else flat

    def _to_prop(self, raw: dict, path: Path) -> Proposal:
        return Proposal.model_validate(_prop_from_rack(raw) if _is_dir_card(path) else raw)

    def load(self, prop_id: str) -> Proposal:
        p = self.path(prop_id)
        raw = yaml.safe_load(p.read_text()) or {}
        return self._to_prop(raw, p)

    def list_all(self) -> list[Proposal]:
        result: list[Proposal] = []
        if not self.dir.exists():
            return result
        seen: set[str] = set()
        for cdir in sorted(self.dir.iterdir()):
            p = cdir / "task.yaml"
            if not (cdir.is_dir() and p.is_file()):
                continue
            try:
                pr = Proposal.model_validate(_prop_from_rack(yaml.safe_load(p.read_text()) or {}))
            except Exception:  # noqa: BLE001, S112
                continue
            seen.add(pr.id)
            result.append(pr)
        for p in sorted(self.dir.iterdir()):
            if not (p.is_file() and _PROP_FILE_RE.match(p.name)):
                continue
            try:
                pr = Proposal.model_validate(yaml.safe_load(p.read_text()) or {})
            except Exception:  # noqa: BLE001, S112
                continue
            if pr.id in seen:
                continue
            seen.add(pr.id)
            result.append(pr)
        result.sort(key=lambda pr: _id_key(pr.id))
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
            data = proposal.model_dump(mode="json", exclude_none=True)
            p.parent.mkdir(parents=True, exist_ok=True)
            _atomic_dump(p, _to_rack(data, _PROP_LINKS) if _is_dir_card(p) else data)
            return p

    def add_vote(self, prop_id: str, vote: Vote) -> Proposal:
        p = self.path(prop_id)
        with _lock_for(p):
            raw = yaml.safe_load(p.read_text()) or {}
            votes = list(raw.get("votes") or [])
            votes.append(vote.model_dump(mode="json", exclude_none=True))
            raw["votes"] = votes
            _atomic_dump(p, raw)
            return self._to_prop(raw, p)

    def set_status(self, prop_id: str, status: str) -> Proposal:
        p = self.path(prop_id)
        with _lock_for(p):
            raw = yaml.safe_load(p.read_text()) or {}
            raw[_status_key(p)] = status
            _atomic_dump(p, raw)
            return self._to_prop(raw, p)


def _id_key(item_id: str) -> tuple[str, int]:
    m = re.search(r"(\d+)$", item_id)
    return (item_id[: m.start()] if m else item_id, int(m.group(1)) if m else -1)


def next_proposal_id(proposals_dir: Path) -> str:
    """Compute next PROP-NNN id by scanning existing files and dir-per-card dirs."""
    return f"PROP-{_max_id(proposals_dir, _PROP_FILE_RE, _PROP_DIR_RE) + 1:03d}"


def next_hypothesis_id(hypotheses_dir: Path) -> str:
    """Compute next HYP-NNN id by scanning existing files and dir-per-card dirs."""
    return f"HYP-{_max_id(hypotheses_dir, _HYP_FILE_RE, _HYP_DIR_RE) + 1:03d}"


def utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)
