"""Placeholder expansion for prompt content reaching the agent.

Library skill / role / rule bodies embed literal ``<ai_hats_dir>/...`` paths
for documentation clarity. Without expansion, the LLM sometimes obeys the
placeholder verbatim and writes artefacts under a literal directory named
``<ai_hats_dir>/`` in the project root (HATS-380).

Expansion happens at every writer layer (each "last gate" before the
prompt or path reaches the agent / filesystem):

- The canonical-dir writer in :mod:`ai_hats.assembler`.
- :meth:`ai_hats.providers.GeminiProvider.build_session_prompt` and
  :meth:`ai_hats.providers.ClaudeProvider.build_session_prompt` (the
  per-session composed prompt) plus the plugin-dir materialization in
  :mod:`ai_hats.plugin_dir` (HATS-380 parity for SKILL.md content).
- :meth:`ai_hats.runtime.SubAgentRunner._build_meta_prompt`.
- The pipeline ``save_artifact`` step
  (:class:`ai_hats.pipeline.steps.save.SaveArtifact`, HATS-395) —
  the path template is expanded before ``.format(...)`` is applied
  so the literal placeholder never reaches ``Path()``.
"""

from __future__ import annotations

from pathlib import Path

from .paths import ai_hats_dir

PLACEHOLDER = "<ai_hats_dir>"


def expand_path_placeholders(text: str, project_dir: Path) -> str:
    """Replace ``<ai_hats_dir>`` with the project-relative ai-hats dir.

    Falls back to the absolute POSIX path when the resolved dir is not
    inside ``project_dir`` (e.g. ``AI_HATS_DIR`` env set to an absolute
    out-of-tree location).
    """
    if PLACEHOLDER not in text:
        return text
    base = ai_hats_dir(project_dir)
    try:
        rel = base.relative_to(project_dir).as_posix()
    except ValueError:
        rel = base.as_posix()
    return text.replace(PLACEHOLDER, rel)
