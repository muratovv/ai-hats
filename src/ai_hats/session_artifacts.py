"""Artifact-builder core: categorised session artifact assembly (ADR-0018)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .materialization import ApplyMaterializer, Materializer


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


@dataclass
class BuiltArtifacts:
    cli_args: list[str] = field(default_factory=list)  # HITL: --system-prompt-file/--plugin-dir/--settings
    extra_env: dict[str, str] = field(default_factory=dict)
    sdk_options: dict = field(default_factory=dict)  # Automate: {"settings":..., "setting_sources":[]}
    materialized: list[Path] = field(default_factory=list)  # for tests/audit
    full_content: str | None = None  # composed prompt bytes (meta_prompt.txt)
    # HATS-1211: every session write goes through here; a PlanMaterializer turns
    # the whole build into a dry-run.
    port: Materializer = field(default_factory=ApplyMaterializer)
    # HATS-1207: policy rides BuiltArtifacts so per-category handlers can read it
    policy: SessionPolicy = field(default_factory=SessionPolicy)


def compose_role_context_sections(result, project_dir: Path) -> str:
    """Compose the # SYSTEM_ROLE and # CONSTRAINTS sections for AUTOMATE context (HATS-1207)."""
    from .placeholders import expand_path_placeholders

    sections = []
    merged = expand_path_placeholders(result.merged_injection, project_dir)
    sections.append(f"# SYSTEM_ROLE\n{merged}")
    if result.priorities:
        constraints = "\n".join(f"- {p}" for p in result.priorities)
        sections.append(f"# CONSTRAINTS\n{constraints}")
    return "\n\n".join(sections)
