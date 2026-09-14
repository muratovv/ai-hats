"""``write_event_log`` step — the session's canonical events as ``events.jsonl``.

HATS-1966. Sibling of ``compute_usage`` (``pipeline/steps/compute_usage.py``):
same post-session transcript, same fail-soft contract, wired next to it in
``finalize-hitl`` / ``finalize-subagent`` — and a THIRD artifact beside the two
they already write. ``audit.md`` stays the human rendering and ``usage.json``
the measured aggregate; this writes the events both are projections of, so a
judge, an A/B comparison or a cost report reads the record rather than
re-deriving it from prose.

Purely additive: nothing reads this artifact yet, ``failure_policy = "continue"``
and the swallowed exception below mean a surface that cannot produce events — or
a write that fails — costs the session nothing it used to have.

WHICH surface ran is not in the finalize funnel (steps are handed a transcript
*resolver* and a *parser*, never the surface), so it is read back from the
session's own ``metrics.json``, the same place ``compute_usage`` reads
``provider`` from, and resolved through the surface registry. Every surface but
Claude answers ``event_reader() is None`` today and this step no-ops for them.
"""  # comment-length: allow — where the surface comes from is the one surprise here

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable, Mapping

from ai_hats_core.layout import ProjectLayout

from ai_hats_observe.artifacts import METRICS_JSON
from ai_hats_observe.event_log import EVENT_LOG_JSONL, write_events
from ai_hats_observe.session import SESSION_FILE_MODE

from ..step import Step, StepIO

logger = logging.getLogger(__name__)


class WriteEventLog(Step):
    failure_policy = "continue"

    def __init__(self, params: Mapping[str, Any] | None = None) -> None:
        del params

    @property
    def io(self) -> StepIO:
        return StepIO(
            name="write_event_log",
            requires=frozenset(
                {
                    "session_id",
                    "session_dir",
                    "claude_session_id",
                    "layout",
                }
            ),
            # Same runner-threaded resolver ``compute_usage`` reads the transcript
            # through; absent on paths that don't inject it → nothing to read.
            optional=frozenset({"transcript_resolver"}),
            produces=frozenset({"event_log_path"}),
        )

    def run(
        self,
        *,
        session_id: str,
        session_dir: Path,
        claude_session_id: str,
        layout: ProjectLayout,
        transcript_resolver=None,
        **_: Any,
    ) -> dict[str, Any]:
        event_log_path = session_dir / EVENT_LOG_JSONL
        try:
            reader_factory = _event_reader_for(session_dir)
            if reader_factory is None:
                logger.debug("write_event_log: surface has no canonical event reader")
                return {}

            transcripts = _transcripts(
                transcript_resolver,
                layout.root,
                session_id,
                claude_session_id,
            )
            if not transcripts:
                logger.debug("write_event_log: no transcript for %s", claude_session_id)
                return {}

            written = 0
            for index, transcript in enumerate(transcripts):
                # A resolver can return several files (a surface that rotates its
                # log, or the resume path matching by mtime), so the later ones
                # append instead of truncating what the first one wrote.
                written += write_events(
                    reader_factory(transcript).read(),
                    event_log_path,
                    append=index > 0,
                )
            # Session artifacts are private. ``write_events`` owns the line format,
            # not the mode, so the file is tightened the way ``copy_artifact``
            # tightens one it did not create itself.
            event_log_path.chmod(SESSION_FILE_MODE)
            logger.debug("write_event_log: %d events -> %s", written, event_log_path)
        except (Exception, KeyboardInterrupt):
            logger.warning("write_event_log failed", exc_info=True)
            return {}

        return {"event_log_path": event_log_path}


def _transcripts(
    transcript_resolver,
    project_dir: Path,
    session_id: str,
    claude_session_id: str,
) -> list[Path]:
    """The transcripts ``compute_usage`` reads, filtered to the ones on disk.

    Provider owns discovery; no resolver → nothing to read. Tolerates a resolver
    that answers with a single path, as ``compute_usage`` does.
    """
    if transcript_resolver is None:
        return []
    resolved = transcript_resolver(
        project_dir,
        session_id,
        provider_session_id=claude_session_id or None,
    )
    if resolved is None:
        return []
    if isinstance(resolved, Path):
        resolved = [resolved]
    return [path for path in resolved if path.exists()]


def _event_reader_for(session_dir: Path) -> Callable[[Path], Any] | None:
    """The reader of the surface that ran this session, or ``None`` for no reader.

    ``metrics.json`` names the provider (written at session start by
    ``init_audit``, refreshed by the upstream ``make_audit`` step), and the
    surface answers whether it has a canonical reading of its own transcript.
    """
    metrics_path = session_dir / METRICS_JSON
    if not metrics_path.exists():
        return None
    try:
        metrics = json.loads(metrics_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    provider = metrics.get("provider") if isinstance(metrics, dict) else None
    if not provider:
        return None

    from ...surface_registry import UnknownSurfaceError, get_surface

    try:
        return get_surface(str(provider)).event_reader()
    except UnknownSurfaceError:
        # An out-of-tree surface that is no longer installed still has sessions
        # on disk; that is a session without events, not a finalize failure.
        logger.debug("write_event_log: provider %r does not resolve", provider)
        return None
