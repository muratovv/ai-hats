"""Artifact-builder core: categorised session artifact assembly (ADR-0018)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from .materialization import ApplyMaterializer, Materializer

if TYPE_CHECKING:
    from .session_run import SessionRun

#: Stands in for a value only the launch can produce (pid, uuid, trace path, a
#: bound port). Lives here so a provider can spell it without importing dry_run.
AT_LAUNCH = "<assigned at launch>"


class ArtifactCategory(str, Enum):
    CONTEXT = "context"
    SKILLS = "skills"
    HOOKS = "hooks"
    SETTINGS = "settings"


class DeliveryMode(str, Enum):
    CACHE_FLAG = "cache_flag"
    SDK_OPTION = "sdk_option"
    NATIVE_ROOT = "native_root"
    INLINE = "inline"


class RunMode(str, Enum):
    HITL = "hitl"
    AUTOMATE = "automate"


@dataclass(frozen=True)
class SessionPolicy:
    context: bool = True
    hooks: bool = True
    settings: bool = True

    def is_enabled(self, category: ArtifactCategory) -> bool:
        if category == ArtifactCategory.CONTEXT:
            return self.context
        if category == ArtifactCategory.HOOKS:
            return self.hooks
        if category == ArtifactCategory.SETTINGS:
            return self.settings
        return True


def assemble_launch_command(
    provider,
    *,
    extra_args: list[str] | None,
    session_args: list[str],
    provider_session_id: str,
) -> list[str]:
    """The one place a HITL launch argv is assembled (HATS-1211 R8).

    Shared by ``WrapRunner`` and ``--dry-run`` so the reported command cannot be
    a reconstruction of the launched one.
    """
    extra = list(extra_args or [])
    cmd = provider.get_cli_command(extra)
    cmd.extend(session_args)
    is_resume = any(f in extra for f in ("--resume", "--continue", "-c"))
    return provider.get_cli_launch_args(cmd, provider_session_id, is_resume)


def _withheld_from_child() -> dict[str, str]:
    """The approvals a sub-agent does not inherit from the session that spawned it.

    An approval is scoped to the session it was given in — the export "pre-approves
    the whole session" (`rule_pause_before_shared_state_write`) — and a sub-agent is
    a different session: own id, own dir, own composition (HATS-1743).

    Two sources, and only the second one holds the line: the named roster keeps the
    launch record byte-identical on every machine, while the shape test over the LIVE
    environment catches a flag no roster knows about — one added after this was
    written, or one belonging to a project that merely consumes ai-hats.

    Blanked rather than dropped: on the SDK road the transport builds the child's
    environment and an overlay can only overwrite a key, never remove it.
    """
    from .constants import BYPASS_FLAGS_NOT_INHERITED, withheld_from_subagent

    ambient = (name for name in os.environ if withheld_from_subagent(name))
    return {name: "" for name in (*BYPASS_FLAGS_NOT_INHERITED, *ambient)}


def assemble_launch_env(
    provider,
    project_dir: Path,
    session_dir: Path,
    *,
    session_id: str,
    trace_path: str,
    role: str,
    root_pid: str,
    extra_env: dict[str, str],
    run_mode: RunMode,
    claim: bool = True,
) -> dict[str, str]:
    """Everything ai-hats ADDS to the child's environment (HATS-1548).

    Sibling of :func:`assemble_launch_command`, and for the same reason: the
    launch merged six sources inline while the report merged two of them, so
    ``--dry-run`` and ``role_materialization.json`` both under-reported the
    session — including ``AI_HATS_SESSION_ID``, the variable that decides how a
    bound check resolves. Inherited ``os.environ`` stays out: the child gets it
    whatever ai-hats does, and listing it would bury what the launch contributes.
    """  # comment-length: allow — the omission it fixes was invisible for a reason
    from ai_hats_observe.session import session_env

    from .constants import ENV_ROOT_PID
    from .paths import session_cache_dir
    from .session_identity import SessionIdentity

    # HATS-1594: the ONE place a session's identity is produced. Gates running in
    # the processes this launches used to re-derive it from ai-hats.yaml, which
    # does not hold it whenever --role/-p override.
    identity = SessionIdentity(
        id=session_id,
        role=role,
        provider=provider.name,
        project_dir=project_dir,
        session_dir=session_dir,
        # Resolved where the provider object is in hand, so no consumer takes a
        # second surface lookup that could answer differently.
        skills_root=str(provider.session_skills_root(project_dir, session_id) or ""),
        # HATS-1735: the consent store's home, published so a stdlib hook never
        # has to re-derive a hashed path.
        session_cache_dir=str(session_cache_dir(project_dir, session_id)),
    )
    # ``claim`` separates a report from a launch: only the launch may take a
    # resource (cline binds a hub port). Same keys either way — a key set that
    # depended on the mode would be the reporting defect, moved (HATS-1554).
    withheld = _withheld_from_child() if run_mode is RunMode.AUTOMATE else {}
    return {
        **withheld,
        **session_env(session_id, trace_path),
        **provider.get_env(session_dir, project_dir),
        **(provider.claim_launch_env(session_dir, project_dir) if claim else {}),
        **extra_env,
        # Last on purpose: the scalars are projections of the envelope, so the
        # identity overrides anything upstream spelled differently.
        **identity.to_env(),
        ENV_ROOT_PID: root_pid,
    }


@dataclass(frozen=True)
class AutomateLaunch:
    """What a sub-agent run consists of: the argv, and the prompt bytes inside it.

    The two are returned together because for a CLI surface they are the same
    thing — the whole prompt is one argv token — and deriving one separately
    from the other is exactly how they drifted (HATS-1552).
    """

    launch: list[str]
    prompt: str


def assemble_meta_prompt(
    project_dir: Path,
    *,
    role_context: str,
    task: str,
    ticket_id: str,
) -> str:
    """The prompt bytes a CLI sub-agent is launched with (HATS-1552).

    Sibling of :func:`assemble_launch_command`. The dry-run held a second,
    tidier version of this that dropped ``WORKING_DIRECTORY`` and both ticket
    sections — and since agy and cline take the whole prompt as one argv token,
    the reported command was not the command.
    """
    from .linked_context import ticket_sections
    from .paths import tasks_dir

    ticket_context, linked_context = ticket_sections(
        tasks_root=tasks_dir(project_dir), ticket_id=ticket_id
    )
    sections = []
    if role_context:
        sections.append(role_context)
    # HATS-1479: a surface whose tool picks its own cwd otherwise resolves the
    # project to whatever absolute path the prompt happens to name.
    sections.append(
        "# WORKING_DIRECTORY\n"
        f"{project_dir.resolve().as_posix()}\n\n"
        "This is the project every path and CLI call below refers to. Run "
        "each command with this directory as its working directory — `rack` "
        "resolves its backlog by walking up from where it runs, so a command "
        "started elsewhere reads and writes a different project."
    )
    if ticket_context:
        sections.append(f"# TICKET_CONTEXT\n{ticket_context}")
    if linked_context:
        sections.append(f"# LINKED_CONTEXT\n{linked_context}")
    if task:
        sections.append(f"# TASK\n{task}")
    return "\n\n".join(sections)


def consumed_session_id(cmd: list[str], provider_session_id: str) -> str:
    """The id this session may claim as its identity — ``""`` when unclaimed.

    HATS-1397: only a surface that puts the id on its own command line will
    write a transcript under it. agy deletes it, cline inherits the base
    no-op, and claude omits it on ``--resume``. Recording it regardless names
    a session that exists nowhere, which also hides the trace-recovery path.
    """
    return provider_session_id if provider_session_id in cmd else ""


@dataclass
class BuiltArtifacts:
    cli_args: list[str] = field(
        default_factory=list
    )  # HITL: --system-prompt-file/--plugin-dir/--settings
    extra_env: dict[str, str] = field(default_factory=dict)
    sdk_options: dict = field(
        default_factory=dict
    )  # Automate: {"settings":..., "setting_sources":[]}
    materialized: list[Path] = field(default_factory=list)  # for tests/audit
    full_content: str | None = None  # composed prompt bytes (meta_prompt.txt)
    # HATS-1211: every session write goes through here; a PlanMaterializer turns
    # the whole build into a dry-run. Appended last — positional ctor stays safe.
    port: Materializer = field(default_factory=ApplyMaterializer)
    # HATS-1207: policy rides here so per-category handlers read it without a
    # published signature change (ADR-0018 §1). Same rule — append last.
    policy: SessionPolicy = field(default_factory=SessionPolicy)
    resources: SessionRun | None = None
    notices: list[str] = field(default_factory=list)
