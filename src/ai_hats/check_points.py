"""Check-binding catalog and composition-time validation (HATS-1140, ADR-0019).

Loud by construction: every way a declared gate can fail to install raises
``CheckBindingError`` here, at composition, rather than reporting into
``CompositionResult.errors`` — that list is tolerated silently on the
implicit-role path (``composition_seam``), which is the fail-open this channel
exists to remove.
"""

from __future__ import annotations

import string
import sys
from collections.abc import Iterable, Sequence, Set as AbstractSet
from dataclasses import dataclass, replace
from pathlib import Path

from ai_hats_core import ResolvedCheck, ResolvedComponent

from .libraries.models import CheckBinding, CheckBindingError, resolve_namespace


@dataclass(frozen=True)
class PointSpec:
    """A catalog entry. ``allow_warn`` is ADR-0019 D4: failure policy at a
    data-protection point belongs to the catalog, not to the binding author."""

    namespace: str
    allow_warn: bool


def _static_points() -> dict[str, PointSpec]:
    from ai_hats_wt.carry import WT_TEARDOWN_EVENTS

    points = {
        "card:pre-create": PointSpec("card", allow_warn=True),
        "wt:create": PointSpec("wt", allow_warn=True),
        "wt:pre-merge": PointSpec("wt", allow_warn=False),
    }
    for event in WT_TEARDOWN_EVENTS:
        points[f"wt:teardown[{event}]"] = PointSpec("wt", allow_warn=False)
    return points


def known_points() -> dict[str, PointSpec]:
    """The full catalog (ADR-0019 D3). ``edge:`` names come from the live rack
    topology, so a forced non-topology transition is bindable too."""
    # Deferred: the integrator may import the rack, never the reverse, and this
    # keeps the cost off every compose that declares no binding.
    from ai_hats_rack import all_edge_keys, load_backlog

    points = {
        key: PointSpec("edge", allow_warn=True) for key in all_edge_keys(load_backlog().topology)
    }
    points.update(_static_points())
    return points


def resolve_checks(
    declared: Sequence[tuple[str, CheckBinding]],
    skills: Iterable[ResolvedComponent],
    *,
    removed_skills: AbstractSet[str] = frozenset(),
) -> tuple[ResolvedCheck, ...]:
    """Fan out declared rows into per-point bindings with absolute scripts."""
    if not declared:
        return ()
    catalog = known_points()
    by_name = {resolve_namespace(skill.name): skill for skill in skills}
    removed = {resolve_namespace(name) for name in removed_skills}
    resolved: dict[tuple[str, str, str], ResolvedCheck] = {}
    for declared_by, row in declared:
        skill = by_name.get(resolve_namespace(row.skill))
        if skill is None:
            _report_missing_skill(row, declared_by, removed)
            continue
        script_path = _resolve_script(row, declared_by, skill)
        for point in row.on:
            _validate_point(point, row, declared_by, catalog)
            check = ResolvedCheck(
                skill=row.skill,
                script=row.script,
                point=point,
                on_error=row.on_error,
                script_path=script_path,
                declared_by=declared_by,
            )
            key = (resolve_namespace(row.skill), row.script, point)
            resolved[key] = _stricter(resolved.get(key), check)
    return tuple(resolved.values())


def _stricter(existing: ResolvedCheck | None, incoming: ResolvedCheck) -> ResolvedCheck:
    """Dedup by (skill, script, point): the strictest ``on_error`` wins, so a
    later relaxation cannot disarm an earlier gate. The first declaration site
    keeps the slot — position and ``declared_by`` follow composition order."""
    if existing is None:
        return incoming
    if existing.on_error == "refuse" or incoming.on_error != "refuse":
        return existing
    return replace(existing, on_error="refuse")


def _resolve_script(row: CheckBinding, declared_by: str, skill: ResolvedComponent) -> Path:
    """Resolve the script and prove it can actually run (ADR-0019 D6).

    Containment first: an unresolved join lets ``../../../x.sh`` — and a bare
    absolute path — read anything on disk, which is the live hole in
    ``collect_lifecycle_hooks``.
    """
    label = f"checks: {declared_by!r} binds {row.skill}/{row.script}"
    skill_dir = skill.source_path.resolve()
    script_path = (skill_dir / row.script).resolve()
    if not script_path.is_relative_to(skill_dir):
        raise CheckBindingError(
            f"{label}: {script_path} is outside the skill's directory {skill_dir} — "
            f"a binding may only run scripts the declaring skill ships"
        )
    if not script_path.is_file():
        raise CheckBindingError(f"{label}: script not found at {script_path}")
    data = script_path.read_bytes()
    if not data.strip():
        raise CheckBindingError(f"{label}: script is empty — a no-op gate is a broken gate")
    if not data.startswith(b"#!"):
        raise CheckBindingError(
            f"{label}: script has no shebang ('#!') first line — it would fail to exec"
        )
    if not script_path.stat().st_mode & 0o111:
        raise CheckBindingError(
            f"{label}: script is not executable — the session skill mirror copies modes "
            f"verbatim, so a non-executable script is dead at every bound point"
        )
    return script_path


def _report_missing_skill(row: CheckBinding, declared_by: str, removed: AbstractSet[str]) -> None:
    """A recorded removal is a warn; anything else is a typo and is loud."""
    label = f"checks: {declared_by!r} binds {row.skill}/{row.script}"
    if resolve_namespace(row.skill) not in removed:
        raise CheckBindingError(
            f"{label}, but the composition composes no skill {row.skill!r} — "
            f"a binding never pulls the skill in (ADR-0019 D2); compose it or fix the name"
        )
    print(
        f"WARN: {label}, but an overlay removed that skill — dropping the "
        f"binding and continuing; the gate will NOT fire",
        file=sys.stderr,
    )


def _validate_point(
    point: str,
    row: CheckBinding,
    declared_by: str,
    catalog: dict[str, PointSpec],
) -> None:
    label = f"checks: {declared_by!r} binds {row.skill}/{row.script}"
    spec = catalog.get(point)
    if spec is None:
        raise CheckBindingError(
            f"{label} to unknown point {point!r} — a typo here is a gate that "
            f"never installs; known namespaces: card:, edge:, wt:"
        )
    if row.on_error == "warn" and not spec.allow_warn:
        raise CheckBindingError(
            f"{label} to {point!r} with on_error: warn — that point protects data, "
            f"so its failure policy is fixed at 'refuse' (ADR-0019 D4)"
        )


#: Characters a binding component keeps verbatim in a log name.
_LITERAL = frozenset(string.ascii_letters + string.digits + "._-")


def _escaped(part: str) -> str:
    """One binding component as a filename-safe token, REVERSIBLY.

    A skill name carries a namespace separator and a script is a relative path,
    so both must lose their slashes; replacing them would collapse ``a/b.sh``
    and ``a-b.sh`` onto one name, which is the truncation defect again. ``/``
    therefore becomes ``+`` (readable) and every other non-literal byte becomes
    ``%XX`` — including ``+`` and ``%`` themselves, so the mapping decodes and
    two different components can never produce the same token. ``~`` is
    non-literal too, which is what makes it a safe joiner.
    """  # comment-length: allow — why it escapes rather than replaces is the fix
    out = []
    for char in part:
        if char in _LITERAL:
            out.append(char)
        elif char == "/":
            out.append("+")
        else:
            out.extend(f"%{byte:02X}" for byte in char.encode())
    return "".join(out)


def check_log_token(check: ResolvedCheck) -> str:
    """One binding's dedup identity as a filename-safe token (HATS-1137).

    ONE function for every point that logs. ``run_hook`` truncates the log it is
    handed, so a name built from anything coarser than ``(skill, script)`` lets
    a second binding wipe the first one's file while the first one's reason goes
    on pointing at it. HATS-1540 reintroduced exactly that at ``wt:pre-merge``
    by naming the log after the script's basename; sharing this is what stops
    the next point from doing it again.
    """  # comment-length: allow — the defect recurred once already
    return f"{_escaped(resolve_namespace(check.skill))}~{_escaped(check.script)}"


__all__ = [
    "CheckBindingError",
    "PointSpec",
    "check_log_token",
    "known_points",
    "resolve_checks",
]
