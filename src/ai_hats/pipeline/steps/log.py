"""``pre_log`` and ``post_log`` — parametrized stderr printers.

Both share the same body — only the step name and YAML-position differ.
``params.keys`` declares which state keys to print; each becomes ``optional``
in the IO contract so missing keys are silently skipped.

Values longer than ``_MAX_VALUE_REPR`` chars are truncated with a
``[+N more chars]`` marker. This is a generalized safety net against
accidentally dumping multi-KB state values (system_prompt, prompt_text,
transcripts) into stderr — fixed twice (HATS-267 system_prompt,
HATS-269 prompt_text) before being made systemic here.

failure_policy=continue — logging must never abort the surrounding flow.
"""

from __future__ import annotations

import sys
from typing import Any, Mapping

from ..step import Step, StepIO


_MAX_VALUE_REPR = 120


def _safe_repr(value: Any) -> str:
    s = repr(value)
    if len(s) <= _MAX_VALUE_REPR:
        return s
    return s[:_MAX_VALUE_REPR] + f"... [+{len(s) - _MAX_VALUE_REPR} more chars]"


class _LogStep(Step):
    failure_policy = "continue"
    _NAME: str = "log"

    def __init__(self, params: Mapping[str, Any] | None = None) -> None:
        params = params or {}
        keys = params.get("keys", [])
        if not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
            raise ValueError(f"{self._NAME}: params.keys must be list[str]")
        self.keys: tuple[str, ...] = tuple(keys)

    @property
    def io(self) -> StepIO:
        return StepIO(name=self._NAME, optional=frozenset(self.keys))

    def run(self, **inputs: Any) -> dict[str, Any]:
        print(f"[pipeline] {self._NAME} fires", file=sys.stderr)
        for k in self.keys:
            if k in inputs:
                print(f"  {k} = {_safe_repr(inputs[k])}", file=sys.stderr)
        return {}


class PreLog(_LogStep):
    _NAME = "pre_log"


class PostLog(_LogStep):
    _NAME = "post_log"
