"""HATS-871 / T11 — advisory aggregate skill-consistency reporter (ADR-0014).

Composes ALL skill/rule sources (builtin library + engine ``ai_hats.skills``
sources + overlays) and REPORTS — never blocks — cross-cutting issues no single
package sees: **dangling** refs, **cross-package** refs (resolve, but couple two
packages), **duplicate** names across engine packages, **overlap** candidates.
Report-only (exit 0); the enforcing gate is a later task.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .frontmatter import FrontmatterError, read_frontmatter
from .rule_delivery import _SEE_RULE  # reuse the shipped ``see rule `X`` convention

# ``see skill `X` `` — mirrors ``_SEE_RULE`` (requires ``see``, so "the skill
# `description` field" prose is not a dangling ref); known-skill coupling written
# as ``skill `X` `` / ``**X**`` is caught by ``_MENTION``.
_SEE_SKILL = re.compile(r"see\s+skills?\s+`([a-z0-9_][a-z0-9_-]*)`", re.IGNORECASE)
# A bold ``**name**`` or backtick ``` `name` ``` mention — counted only when it
# resolves to a KNOWN component, so prose coupling (e.g. "the sibling
# **backlog-manager**") is caught without flagging arbitrary words.
_MENTION = re.compile(r"(?:\*\*|`)([a-z0-9_][a-z0-9_-]*)(?:\*\*|`)")
_OVERLAP_RATIO = 0.80

_PACKAGE_MARKERS = {
    "ai_hats_tracker": "tracker",
    "ai_hats_library": "library",
    "ai_hats_wt": "wt",
    "ai_hats_observe": "observe",
    "ai_hats_core": "core",
}


def _package_of(path: Path) -> str:
    """Best-effort package label for a component path (else ``project`` overlay)."""
    parts = set(path.parts)
    for marker, label in _PACKAGE_MARKERS.items():
        if marker in parts:
            return label
    return "project"


@dataclass(frozen=True)
class Ref:
    kind: str  # "rule" | "skill"
    target: str
    referrer: str
    referrer_pkg: str
    target_pkg: str | None  # None ⇒ dangling
    resolves: bool


@dataclass
class AggregateReport:
    dangling: list[Ref] = field(default_factory=list)
    cross_package: list[Ref] = field(default_factory=list)
    duplicates: list[tuple[str, list[str]]] = field(default_factory=list)  # name, packages
    overlaps: list[tuple[str, str, float]] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not (self.dangling or self.cross_package or self.duplicates or self.overlaps)


def _index(library_paths: list[Path]) -> tuple[dict, dict, dict]:
    """Discoverable components across all roots (last-wins), plus per-name pkgs."""
    skills: dict[str, str] = {}
    rules: dict[str, str] = {}
    pkgs: dict[str, set[str]] = {}
    for root in library_paths:
        for sub, marker, table in (("skills", "SKILL.md", skills), ("rules", "rule.md", rules)):
            base = root / sub
            if not base.is_dir():
                continue
            for md in base.rglob(marker):
                name = str(md.parent.relative_to(base)).replace("/", "::")
                pkg = _package_of(md)
                table[name] = pkg  # last-wins: a later root overrides
                pkgs.setdefault(f"{sub}:{name}", set()).add(pkg)
    return skills, rules, pkgs


def _refs_in(text: str, referrer: Path, referrer_pkg: str, skills: dict, rules: dict) -> list[Ref]:
    found: dict[tuple[str, str], Ref] = {}

    def add(kind: str, target: str, table: dict) -> None:
        key = (kind, target)
        if key in found:  # one coupling per (kind, target) per referrer file
            return
        target_pkg = table.get(target)
        found[key] = Ref(kind, target, str(referrer), referrer_pkg, target_pkg, target_pkg is not None)

    # 1. Strict ``see rule/skill `X` `` — authoritative; the ONLY source of
    #    dangling findings (an unknown target here is a real broken pointer).
    for pattern, kind, table in ((_SEE_RULE, "rule", rules), (_SEE_SKILL, "skill", skills)):
        for m in pattern.finditer(text):
            add(kind, m.group(1), table)
    # 2. Bold/backtick mentions that resolve to a KNOWN component — catches
    #    prose coupling like ``**backlog-manager**``; unknown tokens are ignored
    #    (never dangling) so ordinary prose is not flagged.
    for m in _MENTION.finditer(text):
        tok = m.group(1)
        if tok in skills:
            add("skill", tok, skills)
        elif tok in rules:
            add("rule", tok, rules)
    return list(found.values())


def _composition_refs(cfg: Path, referrer_pkg: str, skills: dict, rules: dict) -> list[Ref]:
    """Refs from a trait/role ``config.yaml`` composition (``skills:``/``rules:``)."""
    try:
        data = yaml.safe_load(cfg.read_text()) or {}
    except yaml.YAMLError:
        return []
    if not isinstance(data, dict):
        return []
    composition = data.get("composition") if isinstance(data.get("composition"), dict) else data
    found: list[Ref] = []
    for key, kind, table in (("skills", "skill", skills), ("rules", "rule", rules)):
        for target in composition.get(key, []) or []:
            if not isinstance(target, str):
                continue
            target_pkg = table.get(target)
            found.append(
                Ref(kind, target, str(cfg), referrer_pkg, target_pkg, target_pkg is not None)
            )
    return found


def _overlaps(library_paths: list[Path]) -> list[tuple[str, str, float]]:
    descs: dict[str, str] = {}
    for root in library_paths:
        base = root / "skills"
        if not base.is_dir():
            continue
        for md in base.rglob("SKILL.md"):
            name = str(md.parent.relative_to(base)).replace("/", "::")
            try:
                fm = read_frontmatter(md)
            except FrontmatterError:
                continue
            desc = (fm or {}).get("description")
            if isinstance(desc, str) and desc.strip():
                descs[name] = desc.strip()
    names = sorted(descs)
    out: list[tuple[str, str, float]] = []
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            ratio = difflib.SequenceMatcher(None, descs[a], descs[b]).ratio()
            if ratio >= _OVERLAP_RATIO:
                out.append((a, b, round(ratio, 2)))
    return out


def aggregate_report(library_paths: list[Path]) -> AggregateReport:
    """Compose all sources and collect advisory consistency findings."""
    skills, rules, pkgs = _index(library_paths)
    report = AggregateReport()

    for root in library_paths:
        if not root.is_dir():
            continue
        bodies = [*(root / "skills").rglob("SKILL.md"), *(root / "rules").rglob("rule.md")]
        for md in bodies:
            _bucket(_refs_in(md.read_text(), md, _package_of(md), skills, rules), report)
        for cfg in root.rglob("config.yaml"):
            pkg = _package_of(cfg)
            _bucket(_composition_refs(cfg, pkg, skills, rules), report)
            # Injection prose in a config carries couplings too ("see `X` skill").
            _bucket(_refs_in(cfg.read_text(), cfg, pkg, skills, rules), report)

    # A component may be referenced many ways from one file; report each
    # (referrer, kind, target) coupling once.
    report.dangling = list(dict.fromkeys(report.dangling))
    report.cross_package = list(dict.fromkeys(report.cross_package))

    for key, seen in sorted(pkgs.items()):
        engines = seen - {"project"}
        if len(engines) >= 2:
            report.duplicates.append((key, sorted(engines)))

    report.overlaps = _overlaps(library_paths)
    return report


def _bucket(refs: list[Ref], report: AggregateReport) -> None:
    for ref in refs:
        if not ref.resolves:
            report.dangling.append(ref)
        elif ref.target_pkg != ref.referrer_pkg:
            report.cross_package.append(ref)


def _render(report: AggregateReport) -> str:
    lines = ["# ai-hats aggregate skill-consistency report (advisory)"]
    lines.append(f"\n## Dangling refs ({len(report.dangling)})")
    for r in report.dangling:
        lines.append(f"  {r.referrer}: {r.kind} `{r.target}` — not discoverable")
    lines.append(f"\n## Cross-package refs ({len(report.cross_package)})")
    for r in report.cross_package:
        lines.append(f"  {r.referrer_pkg} → {r.target_pkg}: {r.kind} `{r.target}` ({r.referrer})")
    lines.append(f"\n## Duplicate names across engine packages ({len(report.duplicates)})")
    for name, packages in report.duplicates:
        lines.append(f"  {name} shipped by {packages}")
    lines.append(f"\n## Overlap candidates ({len(report.overlaps)})")
    for a, b, ratio in report.overlaps:
        lines.append(f"  {a} ~ {b} (description ratio {ratio})")
    return "\n".join(lines)


def _main(argv: list[str] | None = None) -> int:
    """Advisory: always exit 0. Prints the report over the shipped tier.

    Scope = builtin library layers + engine ``ai_hats.skills`` sources (the
    shipped, cross-package surface). Deliberately avoids the composition layer
    (no ``Assembler``) so this maintenance tool stays a leaf of the integrator.
    """
    from .paths import builtin_library_layers
    from .skill_sources import skill_source_roots

    paths = list(builtin_library_layers()) + skill_source_roots()
    print(_render(aggregate_report(paths)))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
