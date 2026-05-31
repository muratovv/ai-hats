"""Render the live role catalog for injection into a prompt (HATS-625).

The initial-wizard used to carry a hand-maintained list of available roles
in its injection ("Available base roles — do NOT read yaml"). That list
drifted: new roles (e.g. ``dev-web``), project-local roles, and user roles
never appeared. Instead, the wizard injection now carries the
``<available_roles>`` placeholder, expanded at prompt-build time
(:func:`expand_role_catalog`, called from each provider's
``build_session_prompt`` next to the HATS-380 ``<ai_hats_dir>`` expansion)
with the **live** catalog the resolver actually sees.

``user_facing=True`` drops engine-internal (``core``-layer) roles — the
wizard must never recommend ``judge`` / ``auditor-for-role`` / itself.
Layer is derived from the resolved role directory: a role lives at
``<libroot>/roles/<name>``, so the libroot's own name (``core`` / ``usage``
/ anything else) classifies it.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from .models import ComponentType

if TYPE_CHECKING:
    from .resolver import LibraryResolver

ROLE_CATALOG_PLACEHOLDER = "<available_roles>"


def _layer_of(role_dir: Path) -> str:
    """Classify a resolved role dir by its library layer.

    ``role_dir`` is ``<libroot>/roles/<name>``; ``<libroot>`` is the
    last-wins library path the resolver matched. The builtin layers end in
    ``.../library/core`` and ``.../library/usage``; everything else
    (``~/.ai-hats``, ``<project>/libraries``) is user/project scope.
    """
    libroot_name = role_dir.parent.parent.name
    if libroot_name == "core":
        return "core"
    if libroot_name == "usage":
        return "usage"
    return "user"


def _summary_from_injection(injection: str) -> str:
    """One-line summary from a role injection: the first ``# …`` H1.

    Role injections open with ``# ROLE: <LABEL>`` — strip the ``#`` and the
    ``ROLE:`` prefix. Fallback: first non-empty line. Empty injection → "".
    """
    lines = injection.splitlines()
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            label = stripped.lstrip("#").strip()
            if label.upper().startswith("ROLE:"):
                label = label[len("ROLE:"):].strip()
            if label:
                return label
    for line in lines:
        if line.strip():
            return line.strip()
    return ""


def render_role_catalog(resolver: "LibraryResolver", *, user_facing: bool = True) -> str:
    """Render available roles as a compact markdown list.

    One line per role: ``- **name** — summary · _priorities_``. Sorted by
    name (``list_components`` is sorted), so output is deterministic. With
    ``user_facing=True`` (the wizard case) ``core``-layer roles are omitted.
    """
    entries: list[str] = []
    for name in resolver.list_components(ComponentType.ROLE):
        role_dir = resolver.resolve(name, ComponentType.ROLE)
        if role_dir is None:
            continue
        if user_facing and _layer_of(role_dir) == "core":
            continue
        cfg = resolver.resolve_config(name, ComponentType.ROLE)
        summary = _summary_from_injection(cfg.injection) if cfg else ""
        priorities = ", ".join(cfg.priorities) if cfg and cfg.priorities else ""
        line = f"- **{name}**"
        if summary:
            line += f" — {summary}"
        if priorities:
            line += f" · _{priorities}_"
        entries.append(line)
    return "\n".join(entries)


def expand_role_catalog(text: str, project_dir: Path) -> str:
    """Replace ``<available_roles>`` with the live user-facing catalog.

    Fast no-op when the placeholder is absent — every non-wizard prompt pays
    nothing. Builds a resolver from ``project_dir`` (lazy ``Assembler`` import
    avoids a providers↔assembler import cycle).
    """
    if ROLE_CATALOG_PLACEHOLDER not in text:
        return text
    from .assembler import Assembler

    resolver = Assembler(project_dir).resolver
    catalog = render_role_catalog(resolver, user_facing=True)
    return text.replace(ROLE_CATALOG_PLACEHOLDER, catalog)
