"""Token cost estimation for composed roles and traits."""

from __future__ import annotations

from dataclasses import dataclass, field

from .composer import Composer


@dataclass
class ComponentCost:
    name: str
    category: str  # "injection", "rule", "skill"
    tokens: int
    chars: int


@dataclass
class CostBreakdown:
    components: list[ComponentCost]
    total_tokens: int
    exact: bool  # True if SDK was used
    errors: list[str] = field(default_factory=list)


def count_tokens_approx(text: str) -> int:
    return len(text) // 4


def count_tokens_sdk(texts: list[str], model: str = "claude-sonnet-4-5-20241022") -> list[int] | None:
    """Count tokens via Anthropic SDK. Returns None if unavailable."""
    try:
        import anthropic

        client = anthropic.Anthropic()
        counts = []
        for text in texts:
            if not text.strip():
                counts.append(0)
                continue
            result = client.messages.count_tokens(
                model=model,
                messages=[{"role": "user", "content": text}],
            )
            counts.append(result.input_tokens)
        return counts
    except Exception:
        return None


def analyze_composition(
    composer: Composer,
    name: str,
    as_trait: bool = False,
    exact: bool = True,
) -> CostBreakdown:
    """Analyze token costs for a role or trait.

    Walks the config tree to produce per-component breakdown.
    Uses Anthropic SDK for exact counts, falls back to len//4.
    """
    components: list[tuple[str, str, str]] = []  # (category, name, text)
    errors: list[str] = []

    if as_trait:
        _collect_trait(composer, name, components, errors, visited_rules=set(), visited_skills=set())
    else:
        config = composer.resolver.resolve_role_config(name)
        if config is None:
            return CostBreakdown(
                components=[], total_tokens=0, exact=False, errors=[f"Role '{name}' not found"],
            )

        visited_rules: set[str] = set()
        visited_skills: set[str] = set()

        # Traits
        for trait_name in config.composition.traits:
            _collect_trait(composer, trait_name, components, errors, visited_rules, visited_skills)

        # Role's own rules
        for rule_name in config.composition.rules:
            if rule_name in visited_rules:
                continue
            visited_rules.add(rule_name)
            content = composer.resolver.rule_content(rule_name)
            if content:
                components.append(("rule", rule_name, content))

        # Role's own skills
        for skill_name in config.composition.skills:
            if skill_name in visited_skills:
                continue
            visited_skills.add(skill_name)
            skill_dir = composer.resolver.resolve_skill_dir(skill_name)
            if skill_dir:
                skill_md = skill_dir / "SKILL.md"
                if skill_md.exists():
                    components.append(("skill", skill_name, skill_md.read_text()))

        # Role injection
        if config.injection.strip():
            components.append(("injection", name, config.injection.strip()))

    # Count tokens
    texts = [text for _, _, text in components]
    sdk_counts = count_tokens_sdk(texts) if exact else None
    used_sdk = sdk_counts is not None

    if sdk_counts is None:
        sdk_counts = [count_tokens_approx(t) for t in texts]

    result_components = []
    for (category, comp_name, text), tokens in zip(components, sdk_counts):
        result_components.append(ComponentCost(
            name=comp_name,
            category=category,
            tokens=tokens,
            chars=len(text),
        ))

    return CostBreakdown(
        components=result_components,
        total_tokens=sum(c.tokens for c in result_components),
        exact=used_sdk,
        errors=errors,
    )


def _collect_trait(
    composer: Composer,
    trait_name: str,
    components: list[tuple[str, str, str]],
    errors: list[str],
    visited_rules: set[str],
    visited_skills: set[str],
) -> None:
    """Collect components from a single trait."""
    config = composer.resolver.resolve_trait_config(trait_name)
    if config is None:
        errors.append(f"Trait '{trait_name}' not found")
        return

    # Trait's rules
    for rule_name in config.composition.rules:
        if rule_name in visited_rules:
            continue
        visited_rules.add(rule_name)
        content = composer.resolver.rule_content(rule_name)
        if content:
            components.append(("rule", rule_name, content))

    # Trait's skills
    for skill_name in config.composition.skills:
        if skill_name in visited_skills:
            continue
        visited_skills.add(skill_name)
        skill_dir = composer.resolver.resolve_skill_dir(skill_name)
        if skill_dir:
            skill_md = skill_dir / "SKILL.md"
            if skill_md.exists():
                components.append(("skill", skill_name, skill_md.read_text()))

    # Trait injection
    if config.injection.strip():
        components.append(("injection", trait_name, config.injection.strip()))
