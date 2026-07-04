"""User-level config schema (``UserConfig`` — global customizations layer)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml
from pydantic import ConfigDict, Field, ValidationError, model_validator

from ai_hats_core import YamlModel as _YamlModel
from ai_hats_core import atomic_write_text

from .overlay import OverlayConfig
from .project import _format_project_config_error

logger = logging.getLogger(__name__)


class UserConfigError(ValueError):
    """Raised when ~/.ai-hats/customizations.yaml fails schema validation."""


class UserConfig(_YamlModel):
    """~/.ai-hats/customizations.yaml — user-level role customizations (HATS-421).

    Symmetric to ``ProjectConfig.customizations`` but lives in the user's home
    directory and applies to every project the user opens. Same ``OverlayConfig``
    schema per role.

    Merge order at compose time: built-in role composition → user (this)
    → project. Project applied last wins on conflict. Within each layer, the
    composer's ``_apply_overlay`` runs ``remove`` then ``append`` — so
    ``add: X`` + ``remove: X`` inside a single layer is a first-class
    "move X to that layer's tail" reorder operation.

    Schema_version is intentionally shared with ``ProjectConfig`` (currently 4):
    bumping the project schema bumps this one too, so migration healers update
    both files in lockstep.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 4
    customizations: dict[str, OverlayConfig] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _coerce_customizations(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("customizations"):
            data["customizations"] = {
                role: OverlayConfig.from_dict(overlay) if isinstance(overlay, dict) else overlay
                for role, overlay in data["customizations"].items()
            }
        return data

    @classmethod
    def default_path(cls) -> Path:
        """Canonical location: ``<user_home>/.ai-hats/customizations.yaml``.

        ``user_home`` honours the ``AI_HATS_USER_HOME`` env override
        (HATS-532) so e2e tests can isolate the global-layer file
        without overriding ``HOME`` (which would break claude auth).
        """
        from ..paths import user_home
        return user_home() / ".ai-hats" / "customizations.yaml"

    @classmethod
    def from_yaml(cls, path: Path) -> UserConfig:
        """Load the user customization file.

        - Missing file → empty ``UserConfig`` (silent default, symmetric to a
          fresh project before ``ai-hats config customize`` has been run).
        - Malformed yaml or schema violation → ``UserConfigError`` with the
          path up front so the user knows which file to fix.
        """
        if not path.exists():
            return cls()
        try:
            data = yaml.safe_load(path.read_text()) or {}
        except yaml.YAMLError as e:
            raise UserConfigError(f"Invalid {path}:\n  - yaml parse error: {e}") from e
        if not isinstance(data, dict):
            raise UserConfigError(
                f"Invalid {path}:\n  - top-level value must be a mapping, got {type(data).__name__}"
            )
        try:
            return cls.model_validate(data)
        except ValidationError as e:
            raise UserConfigError(_format_project_config_error(path, e)) from e

    def overlay_for(self, role_name: str) -> OverlayConfig | None:
        """Return the overlay for ``role_name`` or ``None`` if absent/empty.

        Used by the assembler to compose the global layer; dormant roles
        (customized here but not active in the current project) trivially
        resolve to ``None`` and are ignored downstream.
        """
        overlay = self.customizations.get(role_name)
        if overlay is None or overlay.is_empty:
            return None
        return overlay

    def to_dict(self) -> dict[str, Any]:
        live = {
            name: overlay.to_dict()
            for name, overlay in self.customizations.items()
            if not overlay.is_empty
        }
        d: dict[str, Any] = {"schema_version": self.schema_version}
        if live:
            d["customizations"] = live
        return d

    def save(self, path: Path) -> None:
        """Persist the file. If every overlay is empty, delete the file
        instead of writing an empty stub (keeps ``~/.ai-hats/`` tidy).
        """
        live = any(not overlay.is_empty for overlay in self.customizations.values())
        if not live:
            if path.exists():
                # File is empty by contract — no recovery value in a
                # snapshot. Whitelist with reason.
                path.unlink()  # safe-delete: ok empty-config
            return
        atomic_write_text(
            path, yaml.dump(self.to_dict(), default_flow_style=False, allow_unicode=True)
        )
