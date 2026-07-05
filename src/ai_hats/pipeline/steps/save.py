"""``save_artifact`` step — write a state value to a templated path.

The path template supports ``{ts}`` plus any state key (e.g.
``{target_role}``). Placeholder names found in the template (other
than ``ts``) are declared as ``requires`` so the pipeline core
projects them through to ``run``.

If the template embeds the framework path placeholder ``<ai_hats_dir>``
(HATS-380 / HATS-395), the step also requires ``project_dir`` and
expands the placeholder via :func:`expand_path_placeholders` before
the ``.format(...)`` call. Without this expansion the literal string
``<ai_hats_dir>`` would survive into the filesystem path and create
a bogus directory in the project root.
"""

from __future__ import annotations

import string
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ...placeholders import PLACEHOLDER, expand_path_placeholders
from ..step import Step, StepIO


def _parse_template_keys(template: str) -> frozenset[str]:
    """Return named placeholders in ``template`` (excluding ``ts``)."""
    return frozenset(
        name
        for _, name, _, _ in string.Formatter().parse(template)
        if name and name != "ts"
    )


class SaveArtifact(Step):
    failure_policy = "halt"
    _NAME = "save_artifact"

    def __init__(self, params: Mapping[str, Any]) -> None:
        for required in ("key", "out_path_template"):
            if required not in params:
                raise ValueError(f"{self._NAME}: missing param {required!r}")
        self.key: str = params["key"]
        self.out_path_template: str = params["out_path_template"]
        self._template_keys = _parse_template_keys(self.out_path_template)
        self._needs_project_dir = PLACEHOLDER in self.out_path_template

    @property
    def io(self) -> StepIO:
        requires = frozenset({self.key}) | self._template_keys
        if self._needs_project_dir:
            requires = requires | frozenset({"project_dir"})
        return StepIO(
            name=self._NAME,
            requires=requires,
            produces=frozenset({"saved_path"}),
        )

    def run(self, **inputs: Any) -> dict[str, Any]:
        content = inputs[self.key]
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
        template = self.out_path_template
        if self._needs_project_dir:
            template = expand_path_placeholders(template, inputs["project_dir"])
        path = Path(template.format(ts=ts, **inputs))
        if self._needs_project_dir and not path.is_absolute():
            # ``<ai_hats_dir>`` expands to a *project-relative* path when the
            # ai-hats dir lives under ``project_dir`` (the default). Anchor it
            # to ``project_dir`` so the write never depends on the process CWD
            # (HATS-671: a CWD/project_dir mismatch — as in tests passing
            # ``project_dir=tmp_path`` — otherwise leaked the artefact into the
            # real repo's gitignored ``sessions/`` dir). An absolute expansion
            # (``AI_HATS_DIR`` set out-of-tree, HATS-380/395) is left untouched.
            path = Path(inputs["project_dir"]) / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content if isinstance(content, str) else str(content))
        return {"saved_path": path}
