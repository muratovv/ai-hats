"""Common pydantic base for YAML-round-trippable models (HATS-862, ex ``ai_hats.models._YamlModel``)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class YamlModel(BaseModel):
    """Common base for YAML-round-trippable models.

    Defaults to ``extra="ignore"`` (silently drop unknown keys). Subclasses
    override when needed (e.g. TaskCard needs ``extras`` round-trip).
    Serialization uses ``mode="json"`` via ``to_dict()`` to coerce enums/Paths
    to primitives suitable for ``yaml.safe_dump``.
    """

    model_config = ConfigDict(extra="ignore")

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None):  # pragma: no cover - trivial
        return cls.model_validate(data or {})
