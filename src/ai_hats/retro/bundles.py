"""BundleManager — create, list, retrieve BundleV1 artifacts.

Bundle ids follow the BUNDLE-YYYY-MM-DD-NNN pattern with a daily counter.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from pathlib import Path

from filelock import FileLock

from .bundle import BundleV1
from .loader import load
from .writer import dump

BUNDLE_FILE_RE = re.compile(r"^BUNDLE-(\d{4}-\d{2}-\d{2})-(\d{3})\.yaml$")
SESSION_PREFIX = "session_"


def next_bundle_id(bundles_dir: Path, today: date | None = None) -> str:
    """Generate the next BUNDLE-YYYY-MM-DD-NNN id, with NNN reset daily.

    Pure function — no side effects. `today` is overridable for tests.
    """
    today = today or datetime.now(timezone.utc).date()
    today_str = today.isoformat()
    max_seq = 0
    if bundles_dir.exists():
        for entry in bundles_dir.iterdir():
            m = BUNDLE_FILE_RE.match(entry.name)
            if m and m.group(1) == today_str:
                max_seq = max(max_seq, int(m.group(2)))
    return f"BUNDLE-{today_str}-{max_seq + 1:03d}"


def _normalize_session_id(session_id: str) -> str:
    """Strip leading `session_` prefix if present (canonical form is bare id)."""
    if session_id.startswith(SESSION_PREFIX):
        return session_id[len(SESSION_PREFIX):]
    return session_id


class BundleManager:
    """Manages BundleV1 artifacts under .agent/retrospectives/bundles/."""

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir
        self.bundles_dir = project_dir / ".agent" / "retrospectives" / "bundles"
        self.gitlog_dir = project_dir / ".gitlog"

    def _project_name(self) -> str:
        return self.project_dir.name

    def _session_exists(self, session_id: str) -> bool:
        sid = _normalize_session_id(session_id)
        return (self.gitlog_dir / f"{SESSION_PREFIX}{sid}").is_dir()

    def path_of(self, bundle_id: str) -> Path:
        return self.bundles_dir / f"{bundle_id}.yaml"

    def get(self, bundle_id: str) -> BundleV1:
        """Load and validate one bundle. Raises FileNotFoundError if missing."""
        path = self.path_of(bundle_id)
        if not path.exists():
            raise FileNotFoundError(f"Bundle not found: {bundle_id}")
        model, _ = load(path)
        if not isinstance(model, BundleV1):
            raise ValueError(f"Expected BundleV1, got {type(model).__name__}")
        return model

    def list(self) -> list[BundleV1]:
        """Load all bundles, sorted by bundle_id."""
        if not self.bundles_dir.exists():
            return []
        bundles: list[BundleV1] = []
        for entry in sorted(self.bundles_dir.iterdir()):
            if not BUNDLE_FILE_RE.match(entry.name):
                continue
            model, _ = load(entry)
            if isinstance(model, BundleV1):
                bundles.append(model)
        return bundles

    def create(
        self,
        session_ids: list[str],
        *,
        notes: str | None = None,
        now: datetime | None = None,
    ) -> BundleV1:
        """Create a bundle. Validates session existence; idempotent within a day.

        Idempotency: if a bundle already exists today with the same sorted set
        of session_ids, it is returned unchanged. Bundles are lens-agnostic —
        the focus lens lives on the judge run, not on the bundle, so the same
        set of sessions reuses one bundle across many judge invocations.
        """
        if not session_ids:
            raise ValueError("session_ids must not be empty")
        normalized = [_normalize_session_id(s) for s in session_ids]
        for sid in normalized:
            if not self._session_exists(sid):
                raise ValueError(f"Session not found in .gitlog/: {sid}")

        self.bundles_dir.mkdir(parents=True, exist_ok=True)
        lock = FileLock(str(self.bundles_dir / ".lock"))
        with lock:
            existing = self._find_idempotent_match(normalized)
            if existing is not None:
                return existing

            created = now or datetime.now(timezone.utc)
            bundle_id = next_bundle_id(self.bundles_dir, today=created.date())
            bundle = BundleV1(
                schema="hats-bundle/v1",
                bundle_id=bundle_id,
                project=self._project_name(),
                created=created,
                session_ids=normalized,
                notes=notes,
            )
            dump(bundle, self.path_of(bundle_id))
            return bundle

    def _find_idempotent_match(self, session_ids: list[str]) -> BundleV1 | None:
        """Look for an existing bundle with the same sorted session set."""
        target_key = tuple(sorted(session_ids))
        for existing in self.list():
            if tuple(sorted(existing.session_ids)) == target_key:
                return existing
        return None

    def reviewed_session_ids(self) -> set[str]:
        """Return all session_ids that appear in any existing bundle."""
        reviewed: set[str] = set()
        for bundle in self.list():
            reviewed.update(bundle.session_ids)
        return reviewed

    def create_from_last(
        self,
        n: int,
        *,
        notes: str | None = None,
    ) -> BundleV1:
        """Create a bundle from the N most recent sessions in .gitlog/."""
        from ..observe import SessionManager

        sessions = SessionManager(self.project_dir).list_sessions(
            last_n=n, productive_only=True,
        )
        if not sessions:
            raise ValueError("No productive sessions found in .gitlog/")
        return self.create([s.session_id for s in sessions], notes=notes)

    def create_from_since(
        self,
        since: date,
        *,
        notes: str | None = None,
    ) -> BundleV1:
        """Create a bundle from all sessions whose timestamp is on or after `since`."""
        from ..observe import SessionManager

        sessions = SessionManager(self.project_dir).list_sessions(productive_only=True)
        keep: list[str] = []
        for s in sessions:
            try:
                ts = datetime.strptime(s.session_id[:8], "%Y%m%d").date()
            except ValueError:
                continue
            if ts >= since:
                keep.append(s.session_id)
        if not keep:
            raise ValueError(f"No sessions found since {since.isoformat()}")
        return self.create(keep, notes=notes)
