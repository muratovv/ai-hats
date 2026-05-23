"""Generic sink steps for ad-hoc pipelines (preview / smoke / dry-run).

``EmitStdout`` prints a single state key's value to stdout — the
canonical terminator for read-only "what would happen" pipelines like
``preview.yaml`` (HATS-452 Phase 1). Composable with any producer that
emits a string under a known key.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Mapping

from ..step import Step, StepIO


class EmitStdout(Step):
    """Print ``state[key]`` to stdout, then halt the pipeline cleanly.

    Params:
        key (str, required): the state key whose value to print.
        format ("text" | "json", default "text"): "text" prints
            ``str(value)`` (with trailing newline); "json" serializes
            via ``json.dumps(value)`` — useful when value is a dict /
            list (e.g. ``composition_stats``).
    """

    failure_policy = "halt"

    def __init__(self, params: Mapping[str, Any] | None = None) -> None:
        if not params or "key" not in params:
            raise ValueError("EmitStdout requires params={'key': '<state key>'}")
        self._key = str(params["key"])
        self._format = str(params.get("format", "text")).lower()
        if self._format not in ("text", "json"):
            raise ValueError(
                f"EmitStdout: format must be 'text' or 'json', got {self._format!r}"
            )

    @property
    def io(self) -> StepIO:
        return StepIO(
            name="emit_stdout",
            requires=frozenset({self._key}),
            optional=frozenset(),
            produces=frozenset(),
        )

    def run(self, **kwargs: Any) -> dict[str, Any]:
        value = kwargs[self._key]
        if self._format == "json":
            sys.stdout.write(json.dumps(value, indent=2, default=str))
            sys.stdout.write("\n")
        else:
            sys.stdout.write(str(value))
            if not str(value).endswith("\n"):
                sys.stdout.write("\n")
        sys.stdout.flush()
        return {}
