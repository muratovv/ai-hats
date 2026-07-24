"""Artifact-builder core: categorised session artifact assembly (ADR-0018)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class ArtifactCategory(str, Enum):
    CONTEXT = "context"
    SKILLS = "skills"
    HOOKS = "hooks"


class DeliveryMode(str, Enum):
    CACHE_FLAG = "cache_flag"
    SDK_OPTION = "sdk_option"
    NATIVE_ROOT = "native_root"


@dataclass(frozen=True)
class SessionPolicy:
    context: bool = True
    hooks: bool = True


@dataclass
class BuiltArtifacts:
    cli_args: list[str] = field(default_factory=list)  # HITL: --system-prompt-file/--plugin-dir/--settings
    extra_env: dict[str, str] = field(default_factory=dict)
    sdk_options: dict = field(default_factory=dict)  # Automate: {"settings":..., "setting_sources":[]}
    materialized: list[Path] = field(default_factory=list)  # for tests/audit
    full_content: str | None = None  # composed prompt bytes (meta_prompt.txt)
