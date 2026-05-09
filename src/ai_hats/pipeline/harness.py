"""Pipeline harness — materialize CLI input + namespace cleanup + run.

Per ADR-0002 §1 Harness contract: pipeline-core sees only ``Path`` and
flat values. The harness turns CLI-style inputs (raw text, optional
arguments) into a deterministic file-on-disk that pipeline steps read.

Each pipeline gets its own namespace under
``<project>/.gitlog/pipeline_runs/<pipeline_name>/`` for prompt files
and other harness artefacts. The namespace is cleaned (rmtree+mkdir)
on every ``__enter__`` — so a new run never sees leftovers from a
previous run that crashed before its own cleanup.

NB: parallel runs of the *same* pipeline name will race the namespace.
This is the same constraint the pre-pipeline ``_do_execute`` had —
ai-hats does not support parallel invocations of one command in one
project. Different pipelines (``human`` vs ``reflect-all``) are safe
in parallel because they have disjoint namespaces.

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
import shutil
from datetime import datetime, timezone
from importlib.resources import as_file, files
from pathlib import Path
from typing import Any, Mapping

from ..paths import traces_dir
from .loader import load_pipeline
from .pipeline import run as run_pipeline
from .trace import JsonlTraceWriter, TraceHook


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

    def __init__(self, pipeline_name: str, project_dir: Path) -> None:
        self.name = pipeline_name
        self.project_dir = project_dir
        self.namespace = (
            project_dir / ".gitlog" / "pipeline_runs" / pipeline_name
        )
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
        if self.namespace.exists():
            shutil.rmtree(self.namespace)
        self.namespace.mkdir(parents=True)
        return self

    def __exit__(self, *exc: Any) -> None:
        # Default: keep artefacts for inspection; cleanup happens at next run.
        return None

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
        res = files("ai_hats.libraries.pipelines") / f"{self.name}.yaml"
        with as_file(res) as yaml_path:
            pipeline = load_pipeline(yaml_path)
        return run_pipeline(
            pipeline,
            dict(initial),
            on_step=self._on_step,
            trace_values=self._trace_values,
        )
