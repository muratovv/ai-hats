"""``extract_marker`` step — pull text between two markers from a transcript.

Used for ``BEGIN_REFLECT_SESSION_RETRO``/``END_REFLECT_SESSION_RETRO`` and
``BEGIN_JUDGE``/``END_JUDGE`` blocks. failure_policy=continue: a missing
marker yields an empty string rather than aborting the pipeline. The
downstream ``save_artifact`` (or any consumer) decides what to do with
empty content.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ..step import Step, StepIO


class ExtractMarker(Step):
    failure_policy = "continue"
    _NAME = "extract_marker"

    def __init__(self, params: Mapping[str, Any]) -> None:
        for required in ("start", "end", "out_key"):
            if required not in params:
                raise ValueError(f"{self._NAME}: missing param {required!r}")
        self.start: str = params["start"]
        self.end: str = params["end"]
        self.out_key: str = params["out_key"]
        if not self.out_key.isidentifier():
            raise ValueError(
                f"{self._NAME}: out_key {self.out_key!r} is not a valid identifier"
            )

    @property
    def io(self) -> StepIO:
        return StepIO(
            name=self._NAME,
            requires=frozenset({"transcript_path"}),
            produces=frozenset({self.out_key}),
        )

    def run(self, *, transcript_path: Path, **_: Any) -> dict[str, Any]:
        try:
            text = Path(transcript_path).read_text()
        except OSError:
            return {self.out_key: ""}
        s = text.find(self.start)
        if s < 0:
            return {self.out_key: ""}
        e = text.find(self.end, s + len(self.start))
        if e < 0:
            return {self.out_key: ""}
        return {self.out_key: text[s + len(self.start) : e].strip()}
