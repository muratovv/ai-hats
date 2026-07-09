"""Token cost estimation for composed roles and traits."""

from __future__ import annotations

from dataclasses import dataclass, field

from .composer import Composer
from .frontmatter import FrontmatterError, parse_frontmatter


@dataclass
class ComponentCost:
    name: str
    category: str  # "injection", "rule", "skill"
    tokens: int  # full-text tokens
    chars: int
    # HATS-957: rule/injection bodies are inlined in the base prompt (always-on);
    # a skill keeps only its name+description resident, its body loads on demand.
    always_on_tokens: int = 0
    on_demand_tokens: int = 0


@dataclass
class CostBreakdown:
    components: list[ComponentCost]
    total_tokens: int
    exact: bool  # True if SDK was used
    errors: list[str] = field(default_factory=list)

    @property
    def always_on_tokens(self) -> int:
        """Tokens resident in the base prompt every turn (injection + rule
        bodies + each skill's name/description). HATS-957."""
        return sum(c.always_on_tokens for c in self.components)

    @property
    def on_demand_tokens(self) -> int:
        """Tokens loaded only when a skill is invoked (its body). HATS-957."""
        return sum(c.on_demand_tokens for c in self.components)


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


def _skill_always_on_text(skill_name: str, skill_md_text: str) -> str:
    """The always-on slice of a skill: what stays resident in the base prompt —
    its name + frontmatter ``description`` (the available-skills entry the model
    reads to know when to trigger). The body loads on demand. HATS-957.

    Malformed frontmatter falls back to the name alone rather than crash the
    cost analysis (this is a reporting path, not the loud composition path)."""
    try:
        desc = (parse_frontmatter(skill_md_text).get("description") or "").strip()
    except FrontmatterError:
        desc = ""
    return f"{skill_name}: {desc}" if desc else skill_name


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
    # (category, name, full_text, always_on_text)
    components: list[tuple[str, str, str, str]] = []
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
                components.append(("rule", rule_name, content, content))

        # Role's own skills
        for skill_name in config.composition.skills:
            if skill_name in visited_skills:
                continue
            visited_skills.add(skill_name)
            skill_dir = composer.resolver.resolve_skill_dir(skill_name)
            if skill_dir:
                skill_md = skill_dir / "SKILL.md"
                if skill_md.exists():
                    text = skill_md.read_text()
                    components.append(
                        ("skill", skill_name, text, _skill_always_on_text(skill_name, text))
                    )

        # Role injection
        if config.injection.strip():
            inj = config.injection.strip()
            components.append(("injection", name, inj, inj))

    return _build_breakdown(components, errors, exact)


def _build_breakdown(
    components: list[tuple[str, str, str, str]],
    errors: list[str],
    exact: bool,
) -> CostBreakdown:
    """Count tokens and split each component into always-on vs on-demand.

    HATS-957 model: rule + injection are inlined in the base prompt
    (always_on = full, on_demand = 0); a skill keeps only its name+description
    resident (always_on), its body loads on demand (on_demand = full − always_on).
    A skill needs two texts counted (full + its always-on slice), every other
    component only one; positions are recorded so a single flat count serves all
    and the same method (SDK or approx) applies to both slices."""
    texts: list[str] = []
    slots: list[tuple[str, str, str, int, int | None]] = []
    for category, comp_name, full, ao_text in components:
        full_i = len(texts)
        texts.append(full)
        ao_i: int | None = None
        if category == "skill":
            ao_i = len(texts)
            texts.append(ao_text)
        slots.append((category, comp_name, full, full_i, ao_i))

    counts = count_tokens_sdk(texts) if exact else None
    used_sdk = counts is not None
    if counts is None:
        counts = [count_tokens_approx(t) for t in texts]

    result_components = []
    for category, comp_name, full, full_i, ao_i in slots:
        full_tok = counts[full_i]
        if ao_i is not None:  # skill: name+description resident, body on demand
            always_on = min(counts[ao_i], full_tok)
            on_demand = full_tok - always_on
        else:  # rule / injection — inlined in the base prompt
            always_on, on_demand = full_tok, 0
        result_components.append(ComponentCost(
            name=comp_name,
            category=category,
            tokens=full_tok,
            chars=len(full),
            always_on_tokens=always_on,
            on_demand_tokens=on_demand,
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
    components: list[tuple[str, str, str, str]],
    errors: list[str],
    visited_rules: set[str],
    visited_skills: set[str],
) -> None:
    """Collect components from a single trait.

    Each entry is ``(category, name, full_text, always_on_text)`` — see
    :func:`_build_breakdown` for the always-on vs on-demand split (HATS-957)."""
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
            components.append(("rule", rule_name, content, content))

    # Trait's skills
    for skill_name in config.composition.skills:
        if skill_name in visited_skills:
            continue
        visited_skills.add(skill_name)
        skill_dir = composer.resolver.resolve_skill_dir(skill_name)
        if skill_dir:
            skill_md = skill_dir / "SKILL.md"
            if skill_md.exists():
                text = skill_md.read_text()
                components.append(
                    ("skill", skill_name, text, _skill_always_on_text(skill_name, text))
                )

    # Trait injection
    if config.injection.strip():
        inj = config.injection.strip()
        components.append(("injection", trait_name, inj, inj))
