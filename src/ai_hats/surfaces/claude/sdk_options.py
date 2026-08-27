"""ClaudeAgentOptions builder — Phase 1 of HATS-474 SDK migration.

Pure-ish factory mapping an ai-hats ``CompositionResult`` plus per-call
inputs to a :class:`claude_agent_sdk.ClaudeAgentOptions` object. The
builder is sync and side-effect-free apart from skill materialization
(reused as-is from :class:`ClaudeSurface`, which writes to the
per-session cache dir keyed by ``session_id``; cleaned at session_end
by ``runtime._cleanup_session_cache``).

Reused by:

- ``SubAgentRunner._run_attempt`` (one-shot SDK path)

**Behaviour change** (documented in plan ``HATS-474``): the legacy
sub-agent path built its prompt with a builder that omitted rule bodies. The
new builder reuses
:meth:`ClaudeSurface.build_system_prompt` so HITL (WrapRunner) and
Automate (SubAgentRunner) paths get the same composition surface, and
sub-agents now see safety rules they previously lacked.

Skill discovery is NOT carried by the system prompt for Claude (HATS-701):
:func:`_build_plugins` materializes the composed skills as a native SDK plugin —
the same ``--plugin-dir`` registry HITL uses — which already lists every skill
with its full description, so a text index would be a 2-3x duplicate.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from claude_agent_sdk import ClaudeAgentOptions
    from claude_agent_sdk.types import SdkPluginConfig, SystemPromptPreset

    from ai_hats_core import CompositionResult

    from .. import Surface
    from ai_hats.session_artifacts import BuiltArtifacts


# ---------------------------------------------------------------------------
# Helpers — kept module-level so unit tests can pin behaviour separately
# from ``build_options`` glue.
# ---------------------------------------------------------------------------


def _build_system_prompt(
    composition_result: "CompositionResult",
    project_dir: Path,
    provider: "Surface",
) -> "SystemPromptPreset":
    """Return the ``system_prompt`` payload as the SDK's preset+append shape.

    Reuses :meth:`Surface.build_system_prompt` (the runner's injected
    provider instance — HATS-865) so the structured sections (PRIORITIES,
    merged role injection, always-on RULES) match HITL exactly. No skill index
    rides along — Claude discovers skills via the materialized SDK plugin
    (HATS-701); see :func:`_build_plugins`. The ``<ai_hats_dir>``
    placeholder is expanded here so the agent never sees the literal token.
    """
    from ai_hats.placeholders import expand_path_placeholders

    text = provider.build_system_prompt(composition_result)
    text = expand_path_placeholders(text, project_dir)
    return {"type": "preset", "preset": "claude_code", "append": text}


def _build_plugins(
    composition_result: "CompositionResult",
    project_dir: Path,
    session_id: str,
    provider: "Surface",
) -> list["SdkPluginConfig"]:
    """Materialize composed skills as a single SDK plugin entry.

    Returns ``[]`` when the composition has no skills, otherwise one
    ``SdkPluginConfig`` of ``type='local'`` pointing at the per-session
    plugin-dir. Disk layout matches what
    :meth:`ClaudeSurface.materialize_runtime_skills` produces today;
    cleanup is owned by ``_cleanup_session_cache`` at session_end.

    Defensive: if the provider's helper drifts from the
    ``["--plugin-dir", "<path>"]`` two-element shape, the function bails
    to ``[]`` rather than emitting a malformed plugin entry.
    """
    if not composition_result.skills:
        return []

    skill_args = provider.materialize_runtime_skills(
        project_dir,
        composition_result,
        session_id,
    )
    if len(skill_args) >= 2 and skill_args[0] == "--plugin-dir":
        return [{"type": "local", "path": skill_args[1]}]
    return []


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


_UNSET = object()


def build_options(
    composition_result: "CompositionResult",
    *,
    provider: "Surface",
    project_dir: Path,
    session_id: str,
    work_dir: Path | None = None,
    claude_session_id: str | None = None,
    model: str = "",
    mcp_config: str | Path | None = None,
    settings: str | None = None,
    setting_sources: list[str] | None = None,
    extra_env: dict[str, str] | None = None,
    max_budget_usd: float | None = None,
    max_turns: int | None = None,
    resume: str | None = None,
    fork_session: bool = False,
    permission_mode: str | None = None,
    allowed_tools: list[str] | None = None,
    system_prompt: Any = _UNSET,
    plugins: Any = _UNSET,
) -> "ClaudeAgentOptions":
    """Build a :class:`ClaudeAgentOptions` from composition + per-call inputs."""
    from claude_agent_sdk import ClaudeAgentOptions

    eff_system_prompt = (
        _build_system_prompt(composition_result, project_dir, provider)
        if system_prompt is _UNSET
        else system_prompt
    )
    eff_plugins = (
        _build_plugins(composition_result, project_dir, session_id, provider)
        if plugins is _UNSET
        else plugins
    )

    kwargs: dict = {
        "system_prompt": eff_system_prompt,
        "plugins": eff_plugins,
        "cwd": str(work_dir if work_dir is not None else project_dir),
    }
    if claude_session_id is not None:
        kwargs["session_id"] = claude_session_id
    if model:
        kwargs["model"] = model
    if mcp_config is not None:
        kwargs["mcp_servers"] = str(mcp_config) if isinstance(mcp_config, Path) else mcp_config
    if settings is not None:
        kwargs["settings"] = settings
    if setting_sources is not None:
        kwargs["setting_sources"] = setting_sources
    env_dict = dict(extra_env) if extra_env else {}
    from ai_hats.paths import session_cache_dir

    from ai_hats.skills_dir import inject_skill_paths_to_env

    plugin_skills_dir = session_cache_dir(project_dir, session_id) / "plugin" / "skills"
    inject_skill_paths_to_env(env_dict, composition_result.skills, plugin_skills_dir)
    if env_dict:
        kwargs["env"] = env_dict

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


def automate_options(
    composition_result: "CompositionResult",
    *,
    provider: "Surface",
    project_dir: Path,
    session_id: str,
    artifacts: "BuiltArtifacts",
    work_dir: Path | None,
    model: str,
    env: dict[str, str],
) -> "ClaudeAgentOptions":
    """The options a sub-agent is launched with — engine and report share this.

    ``system_prompt`` and ``plugins`` are taken from the artifacts the builder
    already produced, so nothing here materializes anything: a report that wrote
    to disk would not be a dry-run (HATS-1552).
    """
    return build_options(
        composition_result,
        provider=provider,
        project_dir=project_dir,
        session_id=session_id,
        work_dir=work_dir,
        model=model or "",
        settings=artifacts.sdk_options.get("settings"),
        setting_sources=artifacts.sdk_options.get("setting_sources"),
        extra_env=env,
        system_prompt=artifacts.sdk_options.get("system_prompt"),
        plugins=artifacts.sdk_options.get("plugins"),
    )


def describe_options(options: "ClaudeAgentOptions") -> list[str]:
    """``k=v`` for every option ai-hats set, measured against the SDK's defaults.

    ``env`` is rendered as key names — the report hides env values everywhere
    else, and naming them here would be the same leak by another route. ``cwd``
    is omitted: it is the worktree, which does not exist when the record is
    written, and ``SessionReport.cwd`` carries that sentinel already.
    """  # comment-length: allow — both omissions are deliberate and easy to "fix" wrongly
    import dataclasses

    from claude_agent_sdk import ClaudeAgentOptions

    stock = ClaudeAgentOptions()
    described = []
    for field in dataclasses.fields(options):
        value = getattr(options, field.name)
        if field.name == "cwd" or value == getattr(stock, field.name):
            continue
        described.append(f"{field.name}={sorted(value) if field.name == 'env' else value}")
    return sorted(described)


def build_first_user_message(
    *,
    ticket_context: str = "",
    task: str = "",
    linked_context: str = "",
) -> str:
    """Compose the first user message for a sub-agent session.

    ``TICKET_CONTEXT``, ``LINKED_CONTEXT`` (HATS-689), ``TASK`` — in that
    order. Per-task runtime context rides the first user turn; the per-role
    composition rides ``system_prompt``. Empty sections are skipped, all-empty
    returns ``""``. A fourth section, ``PROJECT_STATE``, was dropped in
    HATS-681 and its parameter in HATS-1100.

    ``LINKED_CONTEXT`` carries the cards of the ticket's directly-linked
    tasks, assembled by ``linked_context.load_linked_context``. This is the
    live Claude channel for that section; the CLI surfaces mirror it in
    ``session_artifacts.assemble_meta_prompt``.

    Callers reach this through :func:`assemble_first_user_message`, which is
    what loads the sections — going direct is how the engine ended up sending
    a one-line stand-in for the card (HATS-1552).
    """
    sections: list[str] = []
    if ticket_context:
        sections.append(f"# TICKET_CONTEXT\n{ticket_context}")
    if linked_context:
        sections.append(f"# LINKED_CONTEXT\n{linked_context}")
    if task:
        sections.append(f"# TASK\n{task}")
    return "\n\n".join(sections)


def assemble_first_user_message(project_dir: Path, *, task: str, ticket_id: str) -> str:
    """The SDK's first user turn — one expression for the engine and the audit.

    HATS-1552: the engine sent ``Ticket: <id>`` while the saved audit rendered
    the whole card plus ``LINKED_CONTEXT``, so ``meta_prompt.txt`` named a
    message the SDK had never received.
    """
    from ai_hats.linked_context import ticket_sections
    from ai_hats.paths import tasks_dir

    ticket_context, linked_context = ticket_sections(
        tasks_root=tasks_dir(project_dir), ticket_id=ticket_id
    )
    return build_first_user_message(
        ticket_context=ticket_context,
        linked_context=linked_context,
        task=task,
    )


def render_sdk_prompt_audit(
    artifacts: "BuiltArtifacts",
    project_dir: Path,
    *,
    task: str,
    ticket_id: str,
) -> str:
    """Human-readable record of the two things the SDK is actually handed."""
    sys_opt = artifacts.sdk_options.get("system_prompt")
    system_text = sys_opt.get("append", "") if isinstance(sys_opt, dict) else (sys_opt or "")
    initial_message = assemble_first_user_message(project_dir, task=task, ticket_id=ticket_id)
    return (
        "==== SDK system_prompt (preset=claude_code, append) ====\n"
        f"{system_text}\n"
        "\n"
        "==== SDK first user message ====\n"
        f"{initial_message}\n"
    )
