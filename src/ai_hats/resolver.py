"""Library resolver — locates components across library paths."""

from __future__ import annotations

from pathlib import Path

from .models import ComponentConfig, ComponentType, resolve_namespace


def read_rule_body(rule_dir: Path) -> str:
    """Read a rule's ``rule.md`` body, or ``""`` if the file is absent.

    HATS-700: the file on disk is the single source of truth for a rule's
    deliverable text. Consumers that need a body read it on demand from the
    resolved ``source_path`` — the provider (for always-on rules, at
    prompt-build) and the v0.7 migration baseline — instead of the composer
    eager-loading every composed rule body into ``ResolvedComponent.injection``
    each session (dead work; only the 6 always-on rules ever reached a channel).
    """
    rule_md = rule_dir / "rule.md"
    return rule_md.read_text() if rule_md.exists() else ""


class LibraryResolver:
    """Resolves component references to filesystem paths across multiple library roots."""

    def __init__(self, library_paths: list[Path]) -> None:
        self.library_paths = library_paths

    def resolve(self, name: str, component_type: ComponentType) -> Path | None:
        """Resolve a component name to its directory path.

        Searches library paths in order (later paths have higher priority).
        Returns the last match found (highest priority).
        """
        fs_name = resolve_namespace(name)
        subdir = self._type_subdir(component_type)
        result = None
        for lib_path in self.library_paths:
            candidate = lib_path / subdir / fs_name
            if candidate.is_dir():
                result = candidate
        return result

    def resolve_config(self, name: str, component_type: ComponentType) -> ComponentConfig | None:
        """Resolve and load a component's config.yaml."""
        path = self.resolve(name, component_type)
        if path is None:
            return None
        config_file = path / "config.yaml"
        if not config_file.exists():
            return None
        return ComponentConfig.from_yaml(config_file)

    def resolve_rule_dir(self, name: str) -> Path | None:
        """Resolve a rule name to its directory."""
        return self.resolve(name, ComponentType.RULE)

    def resolve_skill_dir(self, name: str) -> Path | None:
        """Resolve a skill name to its directory."""
        return self.resolve(name, ComponentType.SKILL)

    def resolve_trait_config(self, name: str) -> ComponentConfig | None:
        """Resolve and load a trait's config."""
        return self.resolve_config(name, ComponentType.TRAIT)

    def resolve_role_config(self, name: str) -> ComponentConfig | None:
        """Resolve and load a role's config."""
        return self.resolve_config(name, ComponentType.ROLE)

    def rule_content(self, name: str) -> str | None:
        """Read a rule's markdown content."""
        rule_dir = self.resolve_rule_dir(name)
        if rule_dir is None:
            return None
        rule_md = rule_dir / "rule.md"
        if not rule_md.exists():
            return None
        return rule_md.read_text()

    def resolve_injection(self, name: str) -> Path | None:
        """Resolve an initial-injection prompt file via ``library_paths``.

        Searches each library root for ``initial_injections/<name>.md``.
        Last-wins, same as components — a project-local override beats
        user-global, which beats built-in (HATS-445).

        Initial injections are NOT components (no ``config.yaml``, no
        type enum), so this lives next to ``resolve_rule_dir`` rather
        than going through ``_type_subdir``.

        Returns ``None`` if no library carries the file.
        """
        result = None
        for lib_path in self.library_paths:
            candidate = lib_path / "initial_injections" / f"{name}.md"
            if candidate.is_file():
                result = candidate
        return result

    def list_components(self, component_type: ComponentType) -> list[str]:
        """List all available components of a given type."""
        subdir = self._type_subdir(component_type)
        seen = set()
        for lib_path in self.library_paths:
            base = lib_path / subdir
            if not base.exists():
                continue
            marker = {
                ComponentType.RULE: "rule.md",
                ComponentType.SKILL: "SKILL.md",
                ComponentType.TRAIT: "config.yaml",
                ComponentType.ROLE: "config.yaml",
            }[component_type]
            for item in base.rglob(marker):
                rel = item.parent.relative_to(base)
                name = str(rel).replace("/", "::")
                seen.add(name)
        return sorted(seen)

    @staticmethod
    def _type_subdir(component_type: ComponentType) -> str:
        return {
            ComponentType.RULE: "rules",
            ComponentType.SKILL: "skills",
            ComponentType.TRAIT: "traits",
            ComponentType.ROLE: "roles",
        }[component_type]
