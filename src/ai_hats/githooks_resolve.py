"""Resolve the git-gate set for one event, live, from the composition.

Contract in ADR-0020 D3; containment (M11) and gate order (R10) are owned here
because the retired flatten-copy was providing both as side effects.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ai_hats_core import CompositionResult

from .models import SkillMetadata


@dataclass(frozen=True)
class ResolvedGate:
    """One gate script the dispatcher should execute, with its declaring skill."""

    skill: str
    script: str
    path: Path

    @property
    def sort_key(self) -> str:
        """The retired copy's filename — the order the chain used to run in."""
        return f"{self.skill}-{Path(self.script).name}"


@dataclass(frozen=True)
class Resolution:
    """Gates for one event, plus every declaration that was refused and why.

    Refusals are carried, never swallowed: a gate that does not run is a gate
    that does not run, and the dispatcher says so on stderr.
    """

    gates: tuple[ResolvedGate, ...]
    refusals: tuple[str, ...]


def _contain(candidate: Path, skill_root: Path) -> bool:
    """Is ``candidate`` inside ``skill_root`` after both sides are resolved?

    Both sides resolve first: a skill directory may itself be a symlink (the
    user library links whole skills out to other checkouts), and comparing a
    resolved candidate against an unresolved root would refuse those wrongly.
    A symlink *inside* the skill that escapes is refused — that is the check.
    """
    return candidate == skill_root or skill_root in candidate.parents


def _script_refusal(label: str, script: str, candidate: Path, root: Path | None) -> str | None:
    """Why ``candidate`` cannot run, or ``None`` when the dispatcher may exec it."""
    if root is not None and not _contain(candidate, root):
        return f"{label}: '{script}' escapes the skill directory"
    if not candidate.is_file():
        return f"{label}: '{script}' does not exist"
    if "\n" in str(candidate):
        # The dispatcher reads one path per line; a newline would split one gate in two.
        return f"{label}: '{script}' resolves to a path with a newline"
    if not os.access(candidate, os.X_OK):
        return f"{label}: '{script}' is not executable — chmod +x it"
    try:
        with candidate.open("rb") as fh:
            head = fh.read(2)
    except OSError as exc:
        return f"{label}: '{script}' cannot be read: {exc}"
    if head != b"#!":
        return f"{label}: '{script}' has no shebang ('#!') first line — it would fail to exec"
    return None


def resolve_git_gates(result: CompositionResult, event: str) -> Resolution:
    """Collect, resolve and order the gates the composed role declares for ``event``."""
    gates: list[ResolvedGate] = []
    refusals: list[str] = []

    for skill in result.skills:
        declared = SkillMetadata.from_skill_dir(skill.source_path).git_hooks.get(event)
        if not declared:
            continue
        skill_root = skill.source_path.resolve()
        for script in declared:
            candidate = (skill.source_path / script).resolve()
            if refusal := _script_refusal(skill.name, script, candidate, skill_root):
                refusals.append(refusal)
                continue
            gates.append(ResolvedGate(skill=skill.name, script=script, path=candidate))

    gates.sort(key=lambda g: g.sort_key)
    return Resolution(gates=tuple(gates), refusals=tuple(refusals))


def resolve_git_gates_from_plan(plan, event: str) -> Resolution:
    """The same gate set, read off a candidate plan's ``git`` rows."""
    from .surfaces.plan import home_of, mirror_name

    gates: list[ResolvedGate] = []
    refusals: list[str] = []
    for hook in plan.hooks.external:
        if hook.app != "git" or hook.at != event or hook.run is None:
            continue
        home = home_of(hook.run, plan.skills)
        if home is None:
            label, script, root = hook.declared_by, hook.run.path.name, None
        else:
            skill, inside = home
            label, script, root = mirror_name(skill), inside.as_posix(), skill.path.resolve()
        candidate = hook.run.path.resolve()
        if refusal := _script_refusal(label, script, candidate, root):
            refusals.append(refusal)
            continue
        gates.append(ResolvedGate(skill=label, script=script, path=candidate))
    gates.sort(key=lambda g: g.sort_key)
    return Resolution(gates=tuple(gates), refusals=tuple(refusals))
