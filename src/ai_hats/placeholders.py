"""Placeholder expansion for prompt content reaching the agent.

Library skill / role / rule bodies embed literal ``<ai_hats_dir>/...`` paths
for documentation clarity. Without expansion, the LLM sometimes obeys the
placeholder verbatim and writes artefacts under a literal directory named
``<ai_hats_dir>/`` in the project root (HATS-380).

Expansion happens at every writer layer (each "last gate" before the
prompt or path reaches the agent / filesystem):

- The canonical-dir writer in :mod:`ai_hats.assembler`.
- :meth:`ai_hats.surfaces.agy.provider.AgySurface.build_session_prompt` and
  :meth:`ai_hats.surfaces.claude.provider.ClaudeSurface.build_session_prompt` (the
  per-session composed prompt) plus the plugin-dir materialization in
  :mod:`ai_hats.plugin_dir` (HATS-380 parity for SKILL.md content).
- :func:`ai_hats.session_artifacts.assemble_meta_prompt`.
- The pipeline ``save_artifact`` step
  (:class:`ai_hats.pipeline.steps.save.SaveArtifact`, HATS-395) —
  the path template is expanded before ``.format(...)`` is applied
  so the literal placeholder never reaches ``Path()``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .paths import ai_hats_dir

logger = logging.getLogger(__name__)

PLACEHOLDER = "<ai_hats_dir>"

# HATS-1479: absolute anchor for a sub-agent whose tool cwd is not the project.
PROJECT_DIR_PLACEHOLDER = "<project_dir>"


def expand_path_placeholders(text: str, project_dir: Path) -> str:
    """Replace ``<ai_hats_dir>`` (relative) and ``<project_dir>`` (absolute).

    ``<ai_hats_dir>`` falls back to the absolute POSIX path when the resolved
    dir is not inside ``project_dir`` (e.g. ``AI_HATS_DIR`` env set to an
    absolute out-of-tree location).
    """
    if PROJECT_DIR_PLACEHOLDER in text:
        text = text.replace(PROJECT_DIR_PLACEHOLDER, project_dir.resolve().as_posix())
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
FSM_EDGES_TOKEN = "{{backlog_fsm_edges}}"  # noqa: S105 — a placeholder name, not a secret


#: Rendered in place of the table when the project's backlog will not load. Names
#: the loud channel: every rack verb fails on that same file (HATS-1257).
FSM_EDGES_UNAVAILABLE = (
    "_FSM edge table unavailable: this project's `backlog.yaml` did not load. "
    "Run `rack ls` to see the error._"
)


def render_backlog_fsm_edges(project_dir: Path) -> str:
    """Render this project's backlog FSM edge set as a compact markdown table.

    Source of truth: the definition rack itself resolves for the tasks catalog —
    a catalog's own ``backlog.yaml`` wins, packaged default otherwise. The
    prompt table and the CLI's refusal are then two views of ONE file rather
    than a mirror kept in sync by a test (HATS-1257 completes HATS-1042).

    An edge carrying a ``name:`` is annotated with it — that name is typeable in
    place of the target state (``rack transition <ID> reclaim``).
    """
    defn = _resolve_backlog(project_dir)
    if defn is None:
        return FSM_EDGES_UNAVAILABLE
    topology = defn.topology
    lines = [
        "| From state | Legal transitions |",
        "| ---------- | ----------------- |",
    ]
    for state in topology.states:
        cell = ", ".join(_edge_cell(defn, state, to) for to in topology.edges.get(state, ()))
        lines.append(f"| `{state}` | {cell or '_(terminal — no outgoing edges)_'} |")
    return "\n".join(lines)


def _edge_cell(defn, from_state: str, to_state: str) -> str:
    name = defn.edge_names.get((from_state, to_state))
    return f"`{to_state}` ({name})" if name else f"`{to_state}`"


def _resolve_backlog(project_dir: Path):
    """The project's tasks-backlog definition, or ``None`` when it will not load.

    Degrades rather than raising: the prompt must still render for the agent who
    would fix the broken file, and the failure is already loud on every rack
    verb. Broad catch on purpose — nothing is swallowed (warning + marker).
    """
    from ai_hats_rack.definition import resolve_definition

    from .paths import tasks_dir

    try:
        return resolve_definition(tasks_dir(project_dir), project_dir=project_dir)
    except Exception:  # noqa: BLE001 — see docstring
        logger.warning(
            "Could not resolve the backlog definition for %s; the FSM edge table "
            "is omitted from the composed prompt. Run `rack ls` for the error.",
            project_dir,
            exc_info=True,
        )
        return None


def expand_fsm_edges_token(text: str, project_dir: Path) -> str:
    """Replace ``{{backlog_fsm_edges}}`` with the rendered FSM edge table.

    Absent token → no-op (a skill that carries no token is returned unchanged).
    Idempotent: after substitution the token is gone, so a second pass is a
    no-op. The renderer is called only when the token is present, so skills
    without it never pay the resolution.
    """
    if FSM_EDGES_TOKEN not in text:
        return text
    return text.replace(FSM_EDGES_TOKEN, render_backlog_fsm_edges(project_dir))
