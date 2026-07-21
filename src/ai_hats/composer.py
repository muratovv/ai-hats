"""Composition engine — recursive role assembly with deduplication."""

from __future__ import annotations

from ai_hats_core import ComponentKind, CompositionResult, ResolvedComponent

from .resolver import LibraryResolver
from .models import (
    ComponentConfig,
    OverlayConfig,
)


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

        # Apply overlays in order — each layer's `remove` then `add` (move-to-end
        # within a layer; project-after-global means project wins cross-layer).
        requested_skill_removes: set[str] = set()
        role_level_skill_removes: set[str] = set()
        for layer in layers:
            self._apply_overlay(
                config, layer, errors, requested_skill_removes, role_level_skill_removes
            )

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

        # HATS-1046: resolve deferred removals against the composed set so an
        # overlay can drop a TRAIT-brought skill. A skill re-added to the role's
        # own list (remove+add reorder, HATS-421) is exempt; an unknown errors.
        effective_removes = requested_skill_removes - set(config.composition.skills)
        if effective_removes:
            removed_names: set[str] = set()
            kept: list[ResolvedComponent] = []
            for skill in skills:
                if skill.name in effective_removes:
                    removed_names.add(skill.name)
                else:
                    kept.append(skill)
            skills = kept
            for name in effective_removes - removed_names - role_level_skill_removes:
                errors.append(
                    f"Overlay: cannot remove skill '{name}' — "
                    f"not in the role or any composed trait"
                )

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
        requested_skill_removes: set[str],
        role_level_skill_removes: set[str],
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
        # Skill removals are deferred: a trait may bring the skill later, so the
        # verdict is resolved post-resolution in compose() (HATS-1046).
        for skill in overlay.remove_skills:
            requested_skill_removes.add(skill)
            if skill in comp.skills:
                comp.skills.remove(skill)
                role_level_skill_removes.add(skill)
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

            # HATS-700: do NOT eager-load the rule.md body here. Only the 6
            # always-on rules reach the prompt; the provider reads their body on
            # demand from ``source_path`` (read_rule_body). Non-always-on bodies
            # are intentionally undelivered (trait/role summaries are the
            # delivery channel). Loading every composed rule body per session was
            # ~16 KB reaching no channel. Symmetric to HATS-706 (skills).
            rules.append(
                ResolvedComponent(
                    name=rule_name,
                    component_type=ComponentKind.RULE,
                    source_path=rule_dir,
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

            # HATS-706: do NOT eager-load the SKILL.md body here. The only
            # consumer of a skill's body is reflect mode, which reads it on
            # demand from ``source_path``; ``_extract_frontmatter_description``
            # reads its own (single) copy for the Agy index. Loading the
            # full body for every skill on every compose was dead work for
            # every non-reflect session.
            skills.append(
                ResolvedComponent(
                    name=skill_name,
                    component_type=ComponentKind.SKILL,
                    source_path=skill_dir,
                )
            )


# HATS-865: collect_runtime_hooks / collect_worktree_hooks /
# resolve_skill_script moved to the neutral leaf ``hook_collection`` so runtime
# bricks reach them without importing the composition layer.
