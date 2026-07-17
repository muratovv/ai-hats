"""Detect skills still shipping a hook-bearing ``metadata.yaml`` (HATS-815).

After the HATS-814 cutover the engine reads hook wiring from ``SKILL.md``
frontmatter top-level ``ai_hats:``; a leftover ``metadata.yaml`` that still
carries ``git_hooks`` / ``runtime_hooks`` would hard-fail compose
(:class:`~ai_hats.models.LeftoverSidecarHooksError`). This module is the
**detection-only** companion: a pure scan that names every such skill across a
set of library roots, plus the single-sourced remedy string shared with that
compose-guard. It never rewrites or deletes — the supervisor's call is
detect + migrate by hand (the original auto-fold was dropped: a sweep found
zero hook-bearing sidecars in the wild, so the value is a proactive heads-up,
not a rewrite).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import yaml

# Hook keys the 814 cutover moved into SKILL.md frontmatter ``ai_hats:``. A
# leftover sidecar carrying any (truthy) is what trips the compose-guard.
# ``worktree`` is the HATS-823 carry block (wt_in / wt_out) — frontmatter-only
# from day one, so a leftover ``worktree:`` in metadata.yaml is the same
# silent-drop hazard the guard exists to catch.
# ``lifecycle_hooks`` / ``plan_sections`` (HATS-1023) are frontmatter-only from
# day one — a sidecar carrying them is the same silent-drop hazard.
_HOOK_KEYS = ("git_hooks", "runtime_hooks", "worktree", "lifecycle_hooks", "plan_sections")


@dataclass(frozen=True)
class LeftoverHookSidecar:
    """One skill whose ``metadata.yaml`` still carries truthy hook key(s)."""

    skill_dir: Path
    name: str
    keys: tuple[str, ...]


def leftover_sidecar_remedy(name: str, keys: Iterable[str]) -> str:
    """The single remedy line shared by the 814 compose-guard and the 815 scan.

    ``keys`` is normalised to a ``list`` so the rendering is identical whether
    the caller passes the guard's ``list`` or a finding's ``tuple`` — keeping
    the proactive WARN byte-identical to the hard-fail error message.
    """
    return (
        f"skill {name!r}: metadata.yaml still carries "
        f"hook key(s) {list(keys)} — move them to SKILL.md frontmatter "
        f"under the top-level 'ai_hats:' key and delete "
        f"metadata.yaml (HATS-814 cutover)"
    )


def _hook_keys_in(sidecar: Path) -> tuple[str, ...]:
    """Truthy hook keys present in ``sidecar``; ``()`` if absent/hookless/malformed.

    A malformed sidecar yields ``()`` (no finding) on purpose: this is a
    proactive heads-up, not a validator — the compose-guard owns the loud path.
    """
    try:
        raw = yaml.safe_load(sidecar.read_text()) or {}
    except (yaml.YAMLError, OSError):
        return ()
    if not isinstance(raw, dict):
        return ()
    return tuple(k for k in _HOOK_KEYS if raw.get(k))


def scan_leftover_hook_sidecars(
    library_roots: Iterable[Path],
) -> list[LeftoverHookSidecar]:
    """Find skills under ``<root>/skills/*/`` whose ``metadata.yaml`` keeps hooks.

    Pure read-only sweep across the resolved library layers (builtin, global,
    config, project-local). Hookless and absent sidecars are skipped — they
    compose fine under the 814 guard. A skill reached through multiple roots is
    reported once (resolved-path dedup), so symlinked / layered roots do not
    double-warn. Order is deterministic (root order, then name order).
    """
    findings: list[LeftoverHookSidecar] = []
    seen: set[Path] = set()
    for root in library_roots:
        skills_dir = Path(root) / "skills"
        if not skills_dir.is_dir():
            continue
        for skill_dir in sorted(skills_dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            sidecar = skill_dir / "metadata.yaml"
            if not sidecar.is_file():
                continue
            try:
                resolved = skill_dir.resolve()
            except OSError:
                resolved = skill_dir
            if resolved in seen:
                continue
            keys = _hook_keys_in(sidecar)
            if keys:
                seen.add(resolved)
                findings.append(
                    LeftoverHookSidecar(
                        skill_dir=skill_dir, name=skill_dir.name, keys=keys
                    )
                )
    return findings
