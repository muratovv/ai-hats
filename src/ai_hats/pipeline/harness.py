"""Pipeline harness — materialize CLI input + namespace cleanup + run.

Per ADR-0002 §1 Harness contract: pipeline-core sees only ``Path`` and
flat values. The harness turns CLI-style inputs (raw text, optional
arguments) into a deterministic file-on-disk that pipeline steps read.

Per-session namespace (HATS-308): each ``PipelineHarness`` instance owns
a unique ``<ai_hats_dir>/sessions/runs/pipeline_runs/<pipeline_name>/<session_id>/``
subdir. Concurrent invocations of the same pipeline name are safe — they
get disjoint namespaces.

Retention: at most N most-recent sessions per pipeline are kept on disk.
Configurable via ``AI_HATS_PIPELINE_KEEP_N`` (default: 10). Older sibling
sessions are ``rmtree``'d on next ``__enter__`` of any harness for the
same pipeline name. ``ignore_errors=True`` makes concurrent GC of the
same oldest dir benign.

Trace-mode (HATS-274): when env ``AI_HATS_PIPELINE_TRACE`` is set, the
harness wires a ``JsonlTraceWriter`` into ``pipeline.run`` so every
step emits a TraceEvent. Two value modes:

  - ``AI_HATS_PIPELINE_TRACE=/path/to.jsonl`` → that exact file
  - ``AI_HATS_PIPELINE_TRACE=1`` (or any other truthy non-``.jsonl``
    value) → auto-named under ``<traces_dir>/<pipeline>-<ts>.jsonl``

Plus ``AI_HATS_PIPELINE_TRACE_VALUES=1`` to include truncated value
reprs in events (default: keys only, no values — avoids leaking prompt
contents). ``AI_HATS_DIR`` overrides the runtime base namespace
(see ``ai_hats.paths``).
"""

from __future__ import annotations

import os
import secrets
import shutil
import string
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ..paths import core_pipeline_path, runs_dir, traces_dir
from .loader import load_pipeline
from .pipeline import run as run_pipeline
from .trace import JsonlTraceWriter, TraceHook
from .user_steps import load_user_steps


def _resolve_trace_path(value: str, project_dir: Path, name: str) -> Path:
    """Decide where to write the trace based on env value.

    Explicit ``.jsonl`` path → use as-is. Anything else (``1``, ``auto``,
    etc) → auto-named file in ``traces_dir``.
    """
    expanded = Path(value).expanduser()
    if value.endswith(".jsonl"):
        return expanded
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")
    return traces_dir(project_dir) / f"{name}-{ts}.jsonl"


def _generate_session_id() -> str:
    """Generate a per-session id: ``YYYYMMDDTHHMMSS-XXX`` (UTC + 3 chars).

    Sortable lexically (= chronologically); the random suffix avoids
    collisions for sub-second back-to-back runs.
    """
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")
    alphabet = string.ascii_lowercase + string.digits
    rand = "".join(secrets.choice(alphabet) for _ in range(3))
    return f"{ts}-{rand}"


class PipelineHarness:
    """Context-manager harness for CLI → pipeline dispatch.

    Usage:
        with PipelineHarness("execute", project_dir) as h:
            final = h.run({
                "prompt_path": h.materialize_prompt(text),
                "interactive": True,
                ...
            })
    """

    def __init__(
        self,
        pipeline_name: str,
        project_dir: Path,
        session_id: str | None = None,
    ) -> None:
        self.name = pipeline_name
        self.project_dir = project_dir
        self.session_id = session_id or _generate_session_id()
        self._pipeline_root = runs_dir(project_dir) / "pipeline_runs" / pipeline_name
        self.namespace = self._pipeline_root / self.session_id
        # Trace wiring — opt-in via env. Path resolved eagerly so the
        # filename's timestamp reflects "harness construction" (= run
        # start) rather than "first event emitted".
        self._on_step: TraceHook | None = None
        self.trace_path: Path | None = None
        trace_env = os.environ.get("AI_HATS_PIPELINE_TRACE", "").strip()
        if trace_env:
            self.trace_path = _resolve_trace_path(
                trace_env, project_dir, pipeline_name
            )
            self._on_step = JsonlTraceWriter(self.trace_path)
        self._trace_values = os.environ.get(
            "AI_HATS_PIPELINE_TRACE_VALUES", ""
        ).strip() not in ("", "0", "false", "False")

    def __enter__(self) -> "PipelineHarness":
        # HATS-275: import user-authored step modules BEFORE any YAML
        # is loaded — so user step IDs are resolvable from YAML the
        # same way as built-ins. Errors propagate (fail-fast on a
        # broken step-dir, don't half-start the pipeline).
        load_user_steps(self.project_dir)
        self._gc_old_sessions()
        self.namespace.mkdir(parents=True, exist_ok=True)
        return self

    def __exit__(self, *exc: Any) -> None:
        # No teardown work: artefacts are kept for inspection and the
        # per-pipeline namespace is GC'd at the next __enter__ (see
        # _gc_old_sessions). __exit__ stays defined so the class remains a
        # valid context manager.
        return None

    def _gc_old_sessions(self, keep_n: int | None = None) -> None:
        """Prune sibling session dirs beyond the N most-recent.

        ``ignore_errors=True`` is essential for concurrent-GC safety —
        two harnesses GC'ing simultaneously may race on the same
        oldest dir; failure to rmtree it is benign.
        """
        if keep_n is None:
            keep_n = int(os.environ.get("AI_HATS_PIPELINE_KEEP_N", "10"))
        if not self._pipeline_root.exists():
            return
        siblings = sorted(
            (
                p for p in self._pipeline_root.iterdir()
                if p.is_dir() and p.name != self.session_id
            ),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        # Keep the (N-1) most recent; this run creates the Nth.
        for old in siblings[max(0, keep_n - 1):]:
            shutil.rmtree(old, ignore_errors=True)  # safe-delete: ok pipeline-runs-rotation

    def materialize_prompt(self, text: str | None) -> Path | None:
        """Write ``text`` to a file in the namespace; return its path.

        Returns ``None`` if ``text`` is falsy (None or empty string).
        Pipeline's ``resolve_prompt`` step then falls back to its
        ``default_text`` parameter.
        """
        if not text:
            return None
        ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")
        path = self.namespace / f"prompt-{ts}.txt"
        path.write_text(text)
        return path

    def run(self, initial: Mapping[str, Any]) -> dict[str, Any]:
        """Load the named YAML pipeline and run it against ``initial``."""
        yaml_path = core_pipeline_path(self.name)
        if yaml_path is None:
            raise FileNotFoundError(
                f"core pipeline {self.name!r} not found — ai_hats.library missing (broken install)"
            )
        pipeline = load_pipeline(yaml_path)
        return run_pipeline(
            pipeline,
            dict(initial),
            on_step=self._on_step,
            trace_values=self._trace_values,
        )

    def run_yaml(
        self, yaml_path: Path, initial: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Load a pipeline from an arbitrary path and run it.

        Same trace/values wiring as :meth:`run`, but bypasses the
        built-in name lookup. Useful for project-local YAMLs (e.g.
        ``.agent/ai-hats/pipelines/<name>.yaml``) until HATS-268
        surfaces a uniform CLI for both. Tests and the
        custom-pipeline-steps how-to also rely on this entry point.
        """
        pipeline = load_pipeline(yaml_path)
        return run_pipeline(
            pipeline,
            dict(initial),
            on_step=self._on_step,
            trace_values=self._trace_values,
        )
