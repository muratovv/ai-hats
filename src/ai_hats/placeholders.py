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


# HATS-1051: single-token injection of the backlog FSM edge set into skill
# bodies. One token, one need — NOT a generic templating engine. Rendered from
# the live FSM so the in-prompt edge set is authoritative and never rots into a
# hand-maintained table. Applied at the SAME materialization gates as the
# ``<ai_hats_dir>`` placeholder (plugin-dir + skills-dir SKILL.md writers), so
# it is layer-agnostic: an arm-dir / overridden skill (last-wins) gets the
# rendered table just like the built-in.
FSM_EDGES_TOKEN = "{{backlog_fsm_edges}}"


def render_backlog_fsm_edges() -> str:
    """Render this backlog's FSM edge set as a compact markdown table.

    Source of truth: ``TaskState.valid_transitions()`` (topology parity with
    rack ``fsm.yaml`` is a kernel contract) — no cross-package dependency on
    ``ai-hats-rack``. Post-HATS-1042 the source swaps to the resolved
    ``backlog.yaml``; the token and this renderer stay, only the lookup moves.
    """
    from ai_hats.models import TaskState

    transitions = TaskState.valid_transitions()
    lines = [
        "| From state | Legal transitions |",
        "| ---------- | ----------------- |",
    ]
    for state in TaskState:
        targets = transitions.get(state, [])
        if targets:
            cell = ", ".join(f"`{target.value}`" for target in targets)
        else:
            cell = "_(terminal — no outgoing edges)_"
        lines.append(f"| `{state.value}` | {cell} |")
    return "\n".join(lines)


def expand_fsm_edges_token(text: str) -> str:
    """Replace ``{{backlog_fsm_edges}}`` with the rendered FSM edge table.

    Absent token → no-op (a skill that carries no token is returned unchanged).
    Idempotent: after substitution the token is gone, so a second pass is a
    no-op. The renderer is called only when the token is present, so skills
    without it never pay the model import.
    """
    if FSM_EDGES_TOKEN not in text:
        return text
    return text.replace(FSM_EDGES_TOKEN, render_backlog_fsm_edges())
