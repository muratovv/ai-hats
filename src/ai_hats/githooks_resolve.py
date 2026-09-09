"""Resolve the git-gate set for one event, live, from the composition.

Contract in ADR-0020 D3; containment (M11) and gate order (R10) are owned here
because the retired flatten-copy was providing both as side effects — see
HATS-1337.
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
            if not _contain(candidate, skill_root):
                refusals.append(f"{skill.name}: '{script}' escapes the skill directory")
                continue
            if not candidate.is_file():
                refusals.append(f"{skill.name}: '{script}' does not exist")
                continue
            if "\n" in str(candidate):
                # The dispatcher reads one path per line; a newline would split
                # one gate into two unrunnable halves.
                refusals.append(f"{skill.name}: '{script}' resolves to a path with a newline")
                continue
            # Only our own gates reached execve unchecked; the
            # neighbours on this chain have always checked.
            if not os.access(candidate, os.X_OK):
                refusals.append(f"{skill.name}: '{script}' is not executable — chmod +x it")
                continue
            try:
                with candidate.open("rb") as fh:
                    head = fh.read(2)
            except OSError as exc:
                refusals.append(f"{skill.name}: '{script}' cannot be read: {exc}")
                continue
            if head != b"#!":
                refusals.append(
                    f"{skill.name}: '{script}' has no shebang ('#!') first line — "
                    "it would fail to exec"
                )
                continue
            gates.append(ResolvedGate(skill=skill.name, script=script, path=candidate))

    gates.sort(key=lambda g: g.sort_key)
    return Resolution(gates=tuple(gates), refusals=tuple(refusals))
