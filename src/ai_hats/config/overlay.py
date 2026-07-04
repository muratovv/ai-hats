"""Per-role customization overlay schema (add/remove/reorder components)."""

from __future__ import annotations

from typing import Any

from pydantic import Field, computed_field

from ai_hats_core import YamlModel as _YamlModel

class OverlayConfig(_YamlModel):
    """Per-role customization overlay (add/remove components).

    Wire format nests add/remove sections (``add: {traits: [...], ...}``) while
    the in-memory shape is flat. ``from_dict`` / ``to_dict`` bridge the two.

    **Move-to-end reorder semantic (HATS-421).** Within a single overlay,
    putting the same name in BOTH ``add: [X]`` and ``remove: [X]`` is a
    first-class operation meaning "remove X from its current position and
    re-append it to the layer's tail". The composer applies ``remove`` then
    ``append`` per layer (see ``Composer._apply_overlay``), so this round-trip
    produces a reorder rather than cancelling out. Use it when injection
    order or dedup priority matters.

    Layered semantics (composer applies overlays sequentially, global then
    project): a name removed by global can be re-added by project; a name
    added by global can be removed by project. Project always wins because
    it is applied last.
    """

    add_traits: list[str] = Field(default_factory=list)
    add_rules: list[str] = Field(default_factory=list)
    add_skills: list[str] = Field(default_factory=list)
    remove_traits: list[str] = Field(default_factory=list)
    remove_rules: list[str] = Field(default_factory=list)
    remove_skills: list[str] = Field(default_factory=list)
    injection_append: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> OverlayConfig:
        if not data:
            return cls()
        add = data.get("add") or {}
        remove = data.get("remove") or {}
        return cls(
            add_traits=add.get("traits", []),
            add_rules=add.get("rules", []),
            add_skills=add.get("skills", []),
            remove_traits=remove.get("traits", []),
            remove_rules=remove.get("rules", []),
            remove_skills=remove.get("skills", []),
            injection_append=data.get("injection_append", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        add = {
            k: v
            for k, v in (
                ("traits", self.add_traits),
                ("rules", self.add_rules),
                ("skills", self.add_skills),
            )
            if v
        }
        if add:
            d["add"] = add
        remove = {
            k: v
            for k, v in (
                ("traits", self.remove_traits),
                ("rules", self.remove_rules),
                ("skills", self.remove_skills),
            )
            if v
        }
        if remove:
            d["remove"] = remove
        if self.injection_append:
            d["injection_append"] = self.injection_append
        return d

    @computed_field
    @property
    def is_empty(self) -> bool:
        return not any(
            [
                self.add_traits,
                self.add_rules,
                self.add_skills,
                self.remove_traits,
                self.remove_rules,
                self.remove_skills,
                self.injection_append,
            ]
        )
