"""What a session is launched with, beside the plan: its policy, its run mode,
the sub-agent's first turn, and what a child may not inherit."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum

from ai_hats_core.layout import ProjectLayout
from typing import Mapping

#: Stands in for a value only the launch can produce (pid, uuid, trace path, a
#: bound port). Lives here so a provider can spell it without importing the dry-run.
AT_LAUNCH = "<assigned at launch>"


class ArtifactCategory(str, Enum):
    CONTEXT = "context"
    SKILLS = "skills"
    HOOKS = "hooks"
    SETTINGS = "settings"


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


def withheld_from_child() -> dict[str, str]:
    """The approvals a sub-agent does not inherit from the session that spawned it.

    An approval is scoped to the session it was given in — the export "pre-approves
    the whole session" (`rule_pause_before_shared_state_write`) — and a sub-agent is
    a different session: own id, own dir, own composition.

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


def assemble_brief(layout: ProjectLayout, *, task: str, ticket_id: str) -> str:
    """The sub-agent's first turn — TICKET_CONTEXT, LINKED_CONTEXT, TASK — assembled
    where the ticket id is known, so a launch never resolves one."""
    from .linked_context import ticket_sections

    ticket_context, linked_context = ticket_sections(
        tasks_root=layout.tracker.tasks_dir, ticket_id=ticket_id
    )
    sections = []
    if ticket_context:
        sections.append(f"# TICKET_CONTEXT\n{ticket_context}")
    if linked_context:
        sections.append(f"# LINKED_CONTEXT\n{linked_context}")
    if task:
        sections.append(f"# TASK\n{task}")
    return "\n\n".join(sections)


def working_directory_section(layout: ProjectLayout) -> str:
    """A surface whose tool picks its own cwd otherwise resolves the project
    to whatever absolute path the prompt happens to name."""
    return (
        "# WORKING_DIRECTORY\n"
        f"{layout.root.resolve().as_posix()}\n\n"
        "This is the project every path and CLI call below refers to. Run "
        "each command with this directory as its working directory — `rack` "
        "resolves its backlog by walking up from where it runs, so a command "
        "started elsewhere reads and writes a different project."
    )


def consumed_session_id(cmd: list[str], provider_session_id: str) -> str:
    """The id this session may claim as its identity — ``""`` when unclaimed.

    Only a surface that puts the id on its own command line will
    write a transcript under it. agy deletes it, cline inherits the base
    no-op, and claude omits it on ``--resume``. Recording it regardless names
    a session that exists nowhere, which also hides the trace-recovery path.
    """
    return provider_session_id if provider_session_id in cmd else ""


@dataclass
class CollectedMetrics:
    """The ``MetricsSink`` a real run hands the surface: keep what it reports.

    The surface names its own keys; where they land is decided here, in
    ``_finalize_sub_agent``'s ``extra_metrics``.
    """

    values: dict[str, object] = field(default_factory=dict)

    def record(self, values: Mapping[str, object]) -> None:
        self.values.update(values)
