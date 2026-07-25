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


@dataclass
class BuiltArtifacts:
    cli_args: list[str] = field(default_factory=list)  # HITL: --system-prompt-file/--plugin-dir/--settings
    extra_env: dict[str, str] = field(default_factory=dict)
    sdk_options: dict = field(default_factory=dict)  # Automate: {"settings":..., "setting_sources":[]}
    materialized: list[Path] = field(default_factory=list)  # for tests/audit
    full_content: str | None = None  # composed prompt bytes (meta_prompt.txt)
    # HATS-1211: every session write goes through here; a PlanMaterializer turns
    # the whole build into a dry-run. Appended last — positional ctor stays safe.
    port: Materializer = field(default_factory=ApplyMaterializer)
