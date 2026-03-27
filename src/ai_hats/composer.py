"""Composition engine — recursive role assembly with deduplication."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .library import LibraryResolver
from .models import ComponentConfig, ComponentType, HooksConfig, MCPServerConfig


@dataclass
class ResolvedComponent:
    """A fully resolved component with its source path."""

    name: str
    component_type: ComponentType
    source_path: Path
    injection: str = ""


@dataclass
class CompositionResult:
    """The flattened result of composing a role."""

    name: str
    priorities: list[str]
    rules: list[ResolvedComponent]
    skills: list[ResolvedComponent]
    hooks: HooksConfig
    mcp: list[MCPServerConfig]
    injections: list[str]  # ordered injection texts
    errors: list[str] = field(default_factory=list)

    @property
    def merged_injection(self) -> str:
        """Concatenate all injections in dependency-tree order."""
        return "\n\n".join(inj for inj in self.injections if inj.strip())


class Composer:
    """Recursively resolves a role's dependency tree into a flat CompositionResult.

    Non-commutative: order matters. Later components override earlier ones.
    Deduplication: same injection text is included only once.
    Priorities: taken only from the root role.
    """

    def __init__(self, resolver: LibraryResolver) -> None:
        self.resolver = resolver

    def compose(self, role_name: str) -> CompositionResult:
        """Compose a role by resolving its full dependency tree."""
        config = self.resolver.resolve_role_config(role_name)
        if config is None:
            return CompositionResult(
                name=role_name,
                priorities=[],
                rules=[],
                skills=[],
                hooks=HooksConfig(),
                mcp=[],
                injections=[],
                errors=[f"Role '{role_name}' not found"],
            )

        seen_injections: set[str] = set()
        seen_rules: set[str] = set()
        seen_skills: set[str] = set()
        errors: list[str] = []
        rules: list[ResolvedComponent] = []
        skills: list[ResolvedComponent] = []
        injections: list[str] = []
        hooks = HooksConfig()
        mcp: list[MCPServerConfig] = []

        # Recursively resolve traits first (depth-first, pre-order)
        self._resolve_traits(
            config.composition.traits,
            seen_injections=seen_injections,
            seen_rules=seen_rules,
            seen_skills=seen_skills,
            rules=rules,
            skills=skills,
            injections=injections,
            hooks=hooks,
            mcp=mcp,
            errors=errors,
            visited=set(),
        )

        # Then resolve role's own rules
        self._resolve_rules(
            config.composition.rules,
            seen_rules=seen_rules,
            rules=rules,
            errors=errors,
        )

        # Then resolve role's own skills
        self._resolve_skills(
            config.composition.skills,
            seen_skills=seen_skills,
            skills=skills,
            errors=errors,
        )

        # Merge role's own hooks (role hooks override trait hooks for same event)
        self._merge_hooks(hooks, config.composition.hooks)

        # Merge role's own MCP
        mcp.extend(config.composition.mcp)

        # Add role's own injection last (highest priority)
        if config.injection.strip() and config.injection.strip() not in seen_injections:
            injections.append(config.injection.strip())
            seen_injections.add(config.injection.strip())

        return CompositionResult(
            name=config.name,
            priorities=config.priorities,  # Only from root role
            rules=rules,
            skills=skills,
            hooks=hooks,
            mcp=mcp,
            injections=injections,
            errors=errors,
        )

    def _resolve_traits(
        self,
        trait_names: list[str],
        *,
        seen_injections: set[str],
        seen_rules: set[str],
        seen_skills: set[str],
        rules: list[ResolvedComponent],
        skills: list[ResolvedComponent],
        injections: list[str],
        hooks: HooksConfig,
        mcp: list[MCPServerConfig],
        errors: list[str],
        visited: set[str],
    ) -> None:
        for trait_name in trait_names:
            if trait_name in visited:
                continue  # Prevent cycles
            visited.add(trait_name)

            config = self.resolver.resolve_trait_config(trait_name)
            if config is None:
                errors.append(f"Trait '{trait_name}' not found")
                continue

            # Recurse into sub-traits first (depth-first)
            self._resolve_traits(
                config.composition.traits,
                seen_injections=seen_injections,
                seen_rules=seen_rules,
                seen_skills=seen_skills,
                rules=rules,
                skills=skills,
                injections=injections,
                hooks=hooks,
                mcp=mcp,
                errors=errors,
                visited=visited,
            )

            # Resolve trait's rules
            self._resolve_rules(
                config.composition.rules,
                seen_rules=seen_rules,
                rules=rules,
                errors=errors,
            )

            # Resolve trait's skills
            self._resolve_skills(
                config.composition.skills,
                seen_skills=seen_skills,
                skills=skills,
                errors=errors,
            )

            # Merge trait hooks
            self._merge_hooks(hooks, config.composition.hooks)

            # Merge trait MCP
            mcp.extend(config.composition.mcp)

            # Add trait injection (deduped)
            if config.injection.strip() and config.injection.strip() not in seen_injections:
                injections.append(config.injection.strip())
                seen_injections.add(config.injection.strip())

    def _resolve_rules(
        self,
        rule_names: list[str],
        *,
        seen_rules: set[str],
        rules: list[ResolvedComponent],
        errors: list[str],
    ) -> None:
        for rule_name in rule_names:
            if rule_name in seen_rules:
                continue
            seen_rules.add(rule_name)

            rule_dir = self.resolver.resolve_rule_dir(rule_name)
            if rule_dir is None:
                errors.append(f"Rule '{rule_name}' not found")
                continue

            content = self.resolver.rule_content(rule_name)
            rules.append(ResolvedComponent(
                name=rule_name,
                component_type=ComponentType.RULE,
                source_path=rule_dir,
                injection=content or "",
            ))

    def _resolve_skills(
        self,
        skill_names: list[str],
        *,
        seen_skills: set[str],
        skills: list[ResolvedComponent],
        errors: list[str],
    ) -> None:
        for skill_name in skill_names:
            if skill_name in seen_skills:
                continue
            seen_skills.add(skill_name)

            skill_dir = self.resolver.resolve_skill_dir(skill_name)
            if skill_dir is None:
                errors.append(f"Skill '{skill_name}' not found")
                continue

            skill_md = skill_dir / "SKILL.md"
            injection = skill_md.read_text() if skill_md.exists() else ""
            skills.append(ResolvedComponent(
                name=skill_name,
                component_type=ComponentType.SKILL,
                source_path=skill_dir,
                injection=injection,
            ))

    @staticmethod
    def _merge_hooks(target: HooksConfig, source: HooksConfig) -> None:
        """Merge source hooks into target (appending scripts)."""
        for event_name in (
            "session_start", "session_end", "task_start",
            "task_complete", "task_failed", "error",
        ):
            target_list = getattr(target, event_name)
            source_list = getattr(source, event_name)
            for script in source_list:
                if script not in target_list:
                    target_list.append(script)
