"""ClaudeAgentOptions builder — Phase 1 of HATS-474 SDK migration.

Pure-ish factory mapping an ai-hats ``CompositionResult`` plus per-call
inputs to a :class:`claude_agent_sdk.ClaudeAgentOptions` object. The
builder is sync and side-effect-free apart from skill materialization
(reused as-is from :class:`ClaudeProvider`, which writes to the
per-session cache dir keyed by ``session_id``; cleaned at session_end
by ``runtime._cleanup_session_cache``).

Reused by:

- ``SubAgentRunner._run_attempt`` (Phase 2 — one-shot cutover)
- ``SubAgentRunner.session()`` (Phase 3 — multi-turn first-class)
- e2e helpers (Phase 4 — `tests/e2e/_helpers/live.py`)

**Behaviour change** (documented in plan ``HATS-474``): the legacy
sub-agent path built its prompt via ``_build_meta_prompt`` which omitted
``ALWAYS_ON_RULES`` and the ``AVAILABLE SKILLS`` index. The new builder
reuses :meth:`ClaudeProvider.build_system_prompt` so HITL (WrapRunner)
and Automate (SubAgentRunner) paths get the same composition surface.
Sub-agents now see the always-on safety rules and a skill discovery
index they previously lacked.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from claude_agent_sdk import ClaudeAgentOptions
    from claude_agent_sdk.types import SdkPluginConfig, SystemPromptPreset

    from .composer import CompositionResult


# ---------------------------------------------------------------------------
# Helpers — kept module-level so unit tests can pin behaviour separately
# from ``build_options`` glue.
# ---------------------------------------------------------------------------


def _build_system_prompt(
    composition_result: "CompositionResult",
    project_dir: Path,
) -> "SystemPromptPreset":
    """Return the ``system_prompt`` payload as the SDK's preset+append shape.

    Reuses :meth:`ClaudeProvider.build_system_prompt` so the structured
    sections (PRIORITIES, merged role injection, always-on RULES,
    AVAILABLE SKILLS index) match HITL exactly. The ``<ai_hats_dir>``
    placeholder is expanded here so the agent never sees the literal
    token in its system prompt.
    """
    from .placeholders import expand_path_placeholders
    from .providers import ClaudeProvider

    text = ClaudeProvider().build_system_prompt(composition_result)
    text = expand_path_placeholders(text, project_dir)
    return {"type": "preset", "preset": "claude_code", "append": text}


def _build_plugins(
    composition_result: "CompositionResult",
    project_dir: Path,
    session_id: str,
) -> list["SdkPluginConfig"]:
    """Materialize composed skills as a single SDK plugin entry.

    Returns ``[]`` when the composition has no skills, otherwise one
    ``SdkPluginConfig`` of ``type='local'`` pointing at the per-session
    plugin-dir. Disk layout matches what
    :meth:`ClaudeProvider.materialize_runtime_skills` produces today;
    cleanup is owned by ``_cleanup_session_cache`` at session_end.

    Defensive: if the provider's helper drifts from the
    ``["--plugin-dir", "<path>"]`` two-element shape, the function bails
    to ``[]`` rather than emitting a malformed plugin entry.
    """
    if not composition_result.skills:
        return []

    from .providers import ClaudeProvider

    skill_args = ClaudeProvider().materialize_runtime_skills(
        project_dir, composition_result, session_id,
    )
    if len(skill_args) >= 2 and skill_args[0] == "--plugin-dir":
        return [{"type": "local", "path": skill_args[1]}]
    return []


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def build_options(
    composition_result: "CompositionResult",
    *,
    project_dir: Path,
    session_id: str,
    work_dir: Path | None = None,
    claude_session_id: str | None = None,
    model: str = "",
    mcp_config: str | Path | None = None,
    settings: str | None = None,
    extra_env: dict[str, str] | None = None,
    max_budget_usd: float | None = None,
    max_turns: int | None = None,
    resume: str | None = None,
    fork_session: bool = False,
    permission_mode: str | None = None,
    allowed_tools: list[str] | None = None,
) -> "ClaudeAgentOptions":
    """Build a :class:`ClaudeAgentOptions` from composition + per-call inputs.

    Parameters
    ----------
    composition_result:
        Output of :func:`materialize.compose_for_role` (or :meth:`Composer
        .compose`). Drives system_prompt + plugins.
    project_dir:
        Project root — used for placeholder expansion in the system prompt
        and as the default ``cwd`` if ``work_dir`` is None.
    session_id:
        ai-hats session id (date-prefixed, our convention). Used to key
        the per-session cache dir where the plugin-dir is materialized.
        **NOT** the same as the SDK's ``session_id`` field (see
        ``claude_session_id``).
    work_dir:
        Working directory the agent runs in. Defaults to ``project_dir``.
        Sub-agent path passes the worktree dir here.
    claude_session_id:
        Optional UUID to pre-assign as the SDK / Claude Code session id.
        When None, the SDK assigns one — the caller must capture it from
        the first ``ResultMessage`` for resume continuity.
    model, mcp_config, settings, extra_env, max_budget_usd, max_turns,
    resume, fork_session, permission_mode, allowed_tools:
        Direct passthrough fields. Set to None / falsy to omit and rely
        on SDK defaults.

    Returns
    -------
    ClaudeAgentOptions
        Ready to pass to ``ClaudeSDKClient(options=...)``.

    Notes
    -----
    The SDK import is deferred to call time so framework imports stay
    cheap and deployments running only the Gemini provider don't pull
    the SDK transitively at every CLI invocation.
    """
    from claude_agent_sdk import ClaudeAgentOptions

    kwargs: dict = {
        "system_prompt": _build_system_prompt(composition_result, project_dir),
        "plugins": _build_plugins(composition_result, project_dir, session_id),
        "cwd": str(work_dir if work_dir is not None else project_dir),
    }
    if claude_session_id is not None:
        kwargs["session_id"] = claude_session_id
    if model:
        kwargs["model"] = model
    if mcp_config is not None:
        kwargs["mcp_servers"] = (
            str(mcp_config) if isinstance(mcp_config, Path) else mcp_config
        )
    if settings is not None:
        kwargs["settings"] = settings
    if extra_env:
        kwargs["env"] = dict(extra_env)
    if max_budget_usd is not None:
        kwargs["max_budget_usd"] = max_budget_usd
    if max_turns is not None:
        kwargs["max_turns"] = max_turns
    if resume is not None:
        kwargs["resume"] = resume
    if fork_session:
        kwargs["fork_session"] = True
    if permission_mode is not None:
        kwargs["permission_mode"] = permission_mode
    if allowed_tools is not None:
        kwargs["allowed_tools"] = list(allowed_tools)

    return ClaudeAgentOptions(**kwargs)


def build_first_user_message(
    *,
    ticket_context: str = "",
    task: str = "",
    project_state: str = "",
) -> str:
    """Compose the first user message for a sub-agent session.

    ``PROJECT_STATE`` goes here (not in ``system_prompt``) because it is
    per-task runtime context rather than per-role composition.
    ``TICKET_CONTEXT`` and ``TASK`` follow in that order. Empty inputs
    are skipped; an all-empty result returns the empty string — callers
    decide whether to skip sending a first turn at all.

    Reserved for Phase 2/3 callers (``SubAgentRunner._run_attempt`` and
    ``SubAgentRunner.session()``). Defined here so the structure is
    auditable from the foundation phase and integration tests can pin
    section ordering before the migration commit lands.
    """
    sections: list[str] = []
    if project_state:
        sections.append(f"# PROJECT_STATE\n{project_state}")
    if ticket_context:
        sections.append(f"# TICKET_CONTEXT\n{ticket_context}")
    if task:
        sections.append(f"# TASK\n{task}")
    return "\n\n".join(sections)
