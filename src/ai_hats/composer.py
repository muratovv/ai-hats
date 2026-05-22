"""Composition engine — recursive role assembly with deduplication."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .resolver import LibraryResolver
from .models import ComponentConfig, ComponentType, HooksConfig, OverlayConfig


@dataclass
class ResolvedComponent:
    """A fully resolved component with its source path."""

    name: str
    component_type: ComponentType
    source_path: Path
    injection: str = ""


@dataclass
class CompositionResult:
    """The flattened result of composing a role.

    `injections` is the legacy flat view (trait/role/overlay text, deduped by
    content). The structured fields below carry the same data with provenance
    for layered writers (HATS-282 canonical writer):

    - `trait_injections` — `{trait_name: text}`, deduped by text (mirror of
      `injections` dedup): a trait whose text is empty or already recorded by
      another trait/role is absent from the map.
    - `role_injection` — root role's own injection text (independent of dedup;
      always recorded if non-empty).
    - `overlay_injection` — overlay's appended text (independent of dedup;
      always recorded if non-empty).

    Rules and skills already carry provenance via `rules`/`skills` lists
    (deduped by name), so no separate maps are needed for them.
    """

    name: str
    priorities: list[str]
    rules: list[ResolvedComponent]
    skills: list[ResolvedComponent]
    hooks: HooksConfig
    injections: list[str]  # ordered injection texts
    errors: list[str] = field(default_factory=list)
    trait_injections: dict[str, str] = field(default_factory=dict)
    role_injection: str = ""
    overlay_injection: str = ""

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

    def compose(
        self,
        role_name: str,
        overlay: OverlayConfig | None = None,
        *,
        overlays: list[OverlayConfig] | None = None,
    ) -> CompositionResult:
        """Compose a role by resolving its full dependency tree.

        Overlays are applied in order; later overlays "win" on conflict
        because each ``_apply_overlay`` call mutates the same composition
        lists (remove-first-then-append, current semantic preserved within
        each layer). Within a single overlay, ``add: X`` + ``remove: X`` is
        a documented "move X to that layer's tail" reorder operation
        (HATS-421).

        Two parameter forms are supported for backwards compatibility:

        - ``compose(role, overlay=X)``       — single-overlay (legacy).
        - ``compose(role, overlays=[G, P])`` — layered (global → project).

        Passing both is an error. ``injection_append`` from each overlay is
        appended in the same order, after the role's own injection.
        """
        if overlay is not None and overlays is not None:
            raise ValueError("compose: pass either `overlay` or `overlays`, not both")
        layers: list[OverlayConfig] = (
            list(overlays) if overlays is not None else ([overlay] if overlay else [])
        )

        config = self.resolver.resolve_role_config(role_name)
        if config is None:
            return CompositionResult(
                name=role_name,
                priorities=[],
                rules=[],
                skills=[],
                hooks=HooksConfig(),
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
        trait_injections: dict[str, str] = {}
        role_injection_text = ""
        overlay_injection_text = ""
        hooks = HooksConfig()

        # Apply overlays in order — each layer's `remove` then `add` (move-to-end
        # within a layer; project-after-global means project wins cross-layer).
        for layer in layers:
            self._apply_overlay(config, layer, errors)

        # Recursively resolve traits first (depth-first, pre-order)
        self._resolve_traits(
            config.composition.traits,
            seen_injections=seen_injections,
            seen_rules=seen_rules,
            seen_skills=seen_skills,
            rules=rules,
            skills=skills,
            injections=injections,
            trait_injections=trait_injections,
            hooks=hooks,
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

        # Add role's own injection last (highest priority).
        # role_injection is recorded independently of dedup so the layered
        # writer can emit role.md even when text duplicates a trait's.
        role_injection_text = config.injection.strip()
        if role_injection_text and role_injection_text not in seen_injections:
            injections.append(role_injection_text)
            seen_injections.add(role_injection_text)

        # Append each overlay's injection_append after role's own (in layer
        # order: global before project). Each is recorded independently of
        # dedup, like role_injection. ``overlay_injection`` exposes the
        # concatenation so layered writers can emit a single overlay.md.
        appended_overlay_texts: list[str] = []
        for layer in layers:
            text = layer.injection_append.strip()
            if not text:
                continue
            if text not in seen_injections:
                injections.append(text)
                seen_injections.add(text)
            appended_overlay_texts.append(text)
        overlay_injection_text = "\n\n".join(appended_overlay_texts)

        return CompositionResult(
            name=config.name,
            priorities=config.priorities,  # Only from root role
            rules=rules,
            skills=skills,
            hooks=hooks,
            injections=injections,
            errors=errors,
            trait_injections=trait_injections,
            role_injection=role_injection_text,
            overlay_injection=overlay_injection_text,
        )

    @staticmethod
    def _apply_overlay(
        config: ComponentConfig,
        overlay: OverlayConfig,
        errors: list[str],
    ) -> None:
        """Mutate config composition lists according to overlay add/remove."""
        comp = config.composition
        # Remove (with warnings for nonexistent)
        for trait in overlay.remove_traits:
            if trait in comp.traits:
                comp.traits.remove(trait)
            else:
                errors.append(f"Overlay: cannot remove trait '{trait}' — not in base role")
        for rule in overlay.remove_rules:
            if rule in comp.rules:
                comp.rules.remove(rule)
            else:
                errors.append(f"Overlay: cannot remove rule '{rule}' — not in base role")
        for skill in overlay.remove_skills:
            if skill in comp.skills:
                comp.skills.remove(skill)
            else:
                errors.append(f"Overlay: cannot remove skill '{skill}' — not in base role")
        # Add (append; dedup handled during resolution)
        comp.traits.extend(overlay.add_traits)
        comp.rules.extend(overlay.add_rules)
        comp.skills.extend(overlay.add_skills)

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
        trait_injections: dict[str, str],
        hooks: HooksConfig,
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

            # Traits cannot include other traits
            if config.composition.traits:
                errors.append(
                    f"Trait '{trait_name}' contains sub-traits "
                    f"{config.composition.traits} — traits cannot include other traits"
                )
                continue

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

            # Add trait injection (deduped by text).
            # trait_injections mirrors the dedup: a trait whose text is empty
            # or already recorded is absent from the map.
            inj = config.injection.strip()
            if inj and inj not in seen_injections:
                injections.append(inj)
                seen_injections.add(inj)
                trait_injections[trait_name] = inj

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
            rules.append(
                ResolvedComponent(
                    name=rule_name,
                    component_type=ComponentType.RULE,
                    source_path=rule_dir,
                    injection=content or "",
                )
            )

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
            skills.append(
                ResolvedComponent(
                    name=skill_name,
                    component_type=ComponentType.SKILL,
                    source_path=skill_dir,
                    injection=injection,
                )
            )

    @staticmethod
    def _merge_hooks(target: HooksConfig, source: HooksConfig) -> None:
        """Merge source hooks into target (appending scripts)."""
        for event_name in (
            "session_start",
            "session_end",
            "task_start",
            "task_complete",
            "task_failed",
            "error",
        ):
            target_list = getattr(target, event_name)
            source_list = getattr(source, event_name)
            for script in source_list:
                if script not in target_list:
                    target_list.append(script)
