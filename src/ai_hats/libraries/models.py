"""Library domain schema (HATS-863, ex ``ai_hats.models``) — component configs,
rule/skill metadata, hook wiring. T18 (HATS-876) lifts this module into the
``ai-hats-library`` package.
"""

from __future__ import annotations

import difflib
import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from pydantic import ConfigDict, Field, model_validator

from ai_hats_core import ConsentPoint
from ai_hats_core import YamlModel as _YamlModel

from ..constants import (
    HOOK_PRE_TOOL_USE,
    HOOK_POST_TOOL_USE,
)
from ..frontmatter import read_frontmatter
from ..skill_sidecar import _HOOK_KEYS, leftover_sidecar_remedy


class ComponentType(str, Enum):
    RULE = "rule"
    SKILL = "skill"
    TRAIT = "trait"
    ROLE = "role"


# ----- Composition + components -----


class CheckBindingError(ValueError):
    """A declared check binding cannot install (HATS-1140, ADR-0019 D6)."""


@dataclass(frozen=True)
class AppBinding:
    """One row of ``composition.apps``, with its declarer and its place in the tree.

    ai-hats owns three keys — ``run`` (what executes), ``at`` (where) and
    ``on_error`` (how a verdict is read); ``cargo`` is every other key and is
    never read here. ``at`` is owned but not *interpreted*: ai-hats checks only
    that a row names at least one point, because a row bound to nothing is a gate
    that never fires — the silent absence this channel exists to remove. What each
    name MEANS stays the owning application's question (HATS-1545 F3).

    ``path`` is the trail of keys from the app node down to the row, so an
    application that nests (``apps.rack.<backlog>``) gets its level back and one
    that does not (``apps.wt``) gets an empty trail.
    """  # comment-length: allow — which of the three keys is interpreted is the contract

    declared_by: str
    app: str
    path: tuple[str, ...]
    run: str
    at: tuple[str, ...]
    on_error: str
    cargo: Mapping[str, Any]

    @property
    def skill(self) -> str:
        """The skill ``run`` names — its first segment."""
        return self.run.split("/", 1)[0]

    @property
    def script(self) -> str:
        """The path inside that skill's directory — everything after it."""
        return self.run.split("/", 1)[1] if "/" in self.run else ""

    def identity(self) -> tuple[str, tuple[str, ...], str, str]:
        """What makes two rows the same row (HATS-1545 R6).

        ``on_error`` is excluded because it is the one field that MERGES —
        two declarations of one row keep the stricter. Cargo is compared whole
        and opaquely, so the same script at two different points is two rows.
        """
        return (
            self.app,
            self.path,
            self.run,
            json.dumps({"at": list(self.at), **dict(self.cargo)}, sort_keys=True, default=str),
        )


def parse_app_bindings(
    apps: Mapping[str, Any], *, declared_by: str, source: Path | None = None
) -> tuple[AppBinding, ...]:
    """Flatten one component's ``composition.apps`` into rows, in document order.

    A row is recognised STRUCTURALLY — a mapping carrying ``run:`` — so the
    grammar above it belongs to the application and ai-hats never checks its
    depth (HATS-1545 R4). Flattening here is what makes provenance survive: the
    declarer is stamped on each row before any two components' rows meet, so no
    merge step can drop it (D4).
    """
    where = f"{source}: " if source is not None else ""
    if not isinstance(apps, dict):
        raise CheckBindingError(
            f"{where}'composition.apps' must be a mapping of <app>: <block>, "
            f"got {type(apps).__name__}"
        )
    rows: list[AppBinding] = []
    for app, block in apps.items():
        _walk_app_block(block, app=app, path=(), declared_by=declared_by, where=where, rows=rows)
    return tuple(rows)


def _walk_app_block(
    node: Any,
    *,
    app: str,
    path: tuple[str, ...],
    declared_by: str,
    where: str,
    rows: list[AppBinding],
) -> None:
    label = f"{where}composition.apps.{'.'.join((app, *path))}"
    if isinstance(node, list):
        for index, item in enumerate(node):
            if not isinstance(item, dict):
                raise CheckBindingError(
                    f"{label}[{index}]: a row must be a mapping carrying 'run:', "
                    f"got {type(item).__name__}"
                )
            rows.append(_app_row(item, app=app, path=path, declared_by=declared_by, label=label))
        return
    if isinstance(node, dict):
        if "run" in node:
            rows.append(_app_row(node, app=app, path=path, declared_by=declared_by, label=label))
            return
        for key, child in node.items():
            _walk_app_block(
                child,
                app=app,
                path=(*path, str(key)),
                declared_by=declared_by,
                where=where,
                rows=rows,
            )
        return
    raise CheckBindingError(
        f"{label}: expected a row, a list of rows, or a mapping of further keys, "
        f"got {type(node).__name__} — a scalar here declares no gate and would fire nothing"
    )


def _app_row(
    row: Mapping[str, Any], *, app: str, path: tuple[str, ...], declared_by: str, label: str
) -> AppBinding:
    run = row.get("run")
    if not isinstance(run, str) or not run.strip():
        raise CheckBindingError(f"{label}: 'run:' must be a non-empty '<skill>/<script>' string")
    on_error = row.get("on_error", "refuse")
    if on_error not in ("refuse", "warn"):
        raise CheckBindingError(
            f"{label}: 'on_error:' must be 'refuse' or 'warn', got {on_error!r}"
        )
    at = row.get("at")
    if isinstance(at, str):
        at = [at]
    if not isinstance(at, list) or not at or not all(isinstance(p, str) and p.strip() for p in at):
        raise CheckBindingError(
            f"{label}: 'at:' must name at least one point (a string or a list of strings); "
            f"got {at!r} — a row bound to nothing is a gate that never fires. What each name "
            f"means is {app!r}'s question, but that a row names one is not"
        )
    cargo = {key: value for key, value in row.items() if key not in ("run", "on_error", "at")}
    return AppBinding(
        declared_by=declared_by,
        app=app,
        path=path,
        run=run.strip(),
        at=tuple(p.strip() for p in at),
        on_error=on_error,
        cargo=cargo,
    )


def parse_consent_points(
    consent: Mapping[str, Any], *, declared_by: str, source: Path | None = None
) -> tuple[ConsentPoint, ...]:
    """Flatten one component's ``composition.consent`` into points.

    The leaf is a LIST OF POINT NAMES, not a row: nothing is spawned here, so
    there is no script, no ``on_error`` and no cargo — only where this role
    wants to be asked.
    """
    where = f"{source}: " if source is not None else ""
    if not isinstance(consent, dict):
        raise CheckBindingError(
            f"{where}'composition.consent' must be a mapping of <app>: <block>, "
            f"got {type(consent).__name__}"
        )
    points: list[ConsentPoint] = []
    for app, block in consent.items():
        _walk_consent_block(
            block, app=str(app), path=(), declared_by=declared_by, where=where, points=points
        )
    return tuple(points)


def _walk_consent_block(
    node: Any,
    *,
    app: str,
    path: tuple[str, ...],
    declared_by: str,
    where: str,
    points: list[ConsentPoint],
) -> None:
    label = f"{where}composition.consent.{'.'.join((app, *path))}"
    if isinstance(node, str):
        node = [node]
    if isinstance(node, list):
        if not node or not all(isinstance(p, str) and p.strip() for p in node):
            raise CheckBindingError(
                f"{label}: expected at least one point name; got {node!r} — declaring "
                f"consent on nothing asks nobody anything"
            )
        points.extend(
            ConsentPoint(declared_by=declared_by, app=app, path=path, point=p.strip()) for p in node
        )
        return
    if isinstance(node, dict):
        for key, child in node.items():
            _walk_consent_block(
                child,
                app=app,
                path=(*path, str(key)),
                declared_by=declared_by,
                where=where,
                points=points,
            )
        return
    raise CheckBindingError(
        f"{label}: expected a point name, a list of them, or a mapping of further keys, "
        f"got {type(node).__name__}"
    )


class Composition(_YamlModel):
    # HATS-1152: guards construction paths that bypass ``from_yaml``; the
    # user-facing channel for a yaml typo is the pre-strip WARN below.
    model_config = ConfigDict(extra="forbid")

    traits: list[str] = Field(default_factory=list)
    rules: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    #: Per-application declaration blocks, carried verbatim (HATS-1545 R1). The
    #: value is opaque: ai-hats knows no app's grammar, only that a mapping with
    #: ``run:`` inside it is a row.
    apps: dict[str, Any] = Field(default_factory=dict)
    #: Where this role needs the supervisor's explicit approval, per application
    #: and in that application's own point grammar (HATS-1682). A DECLARATION,
    #: not a binding: nothing is spawned here, so it stays separate from ``apps``
    #: — the executor is in-process, where a move's argv, actor and force are.
    consent: dict[str, Any] = Field(default_factory=dict)


class ComponentKeyError(ValueError):
    """A component config carries a key no reader can act on (HATS-1545 R2)."""


def load_component_yaml(path: Path) -> dict[str, Any]:
    """Parse a component config, refusing keys that would be lost in silence.

    ``yaml.safe_load`` keeps the LAST of two identical keys and says nothing
    (measured), so a second ``composition:`` — or a second app under it — erases
    a declared gate with no diagnostic anywhere. That is the exact silence this
    channel exists to remove, and unlike every other defect here it cannot be
    warned about after the fact: the losing declaration is already gone from the
    structure by the time any reader sees it. So it is the one class that
    refuses rather than warns (HATS-1545 R2).

    Composed, then constructed from the same node tree: the audit reads the
    parse events, which is where a duplicate is still observable.
    """  # comment-length: allow — why this class refuses where others warn
    loader = yaml.SafeLoader(path.read_text())
    try:
        node = loader.get_single_node()
        if node is None:
            return {}
        _reject_duplicate_keys(node, path)
        _refuse_retired_checks_key(_composition_node(node), path)
        _reject_non_string_keys(_apps_node(node), path)
        return loader.construct_document(node) or {}
    finally:
        loader.dispose()


def _reject_duplicate_keys(node: yaml.Node, path: Path, trail: tuple[str, ...] = ()) -> None:
    """Refuse a repeated mapping key anywhere in the document, naming its trail."""
    if not isinstance(node, yaml.MappingNode):
        if isinstance(node, yaml.SequenceNode):
            for index, item in enumerate(node.value):
                _reject_duplicate_keys(item, path, (*trail, str(index)))
        return
    seen: set[str] = set()
    for key_node, value_node in node.value:
        key = str(getattr(key_node, "value", key_node))
        where = ".".join((*trail, key)) or key
        if key in seen:
            raise ComponentKeyError(
                f"{path}: duplicate key {key!r} under {'.'.join(trail) or '<document root>'} — "
                f"YAML keeps only the last one, so the earlier {where!r} would be dropped "
                f"with no diagnostic; give it a distinct key or merge the two blocks by hand"
            )
        seen.add(key)
        _reject_duplicate_keys(value_node, path, (*trail, key))


def _child_node(node: yaml.Node | None, key: str) -> yaml.Node | None:
    """The value node under ``key``, if ``node`` is a mapping that has one."""
    if not isinstance(node, yaml.MappingNode):
        return None
    for key_node, value_node in node.value:
        if getattr(key_node, "value", None) == key:
            return value_node
    return None


def _composition_node(root: yaml.Node) -> yaml.Node | None:
    """The ``composition:`` value node, if the document has one."""
    return _child_node(root, "composition")


def _apps_node(root: yaml.Node) -> yaml.Node | None:
    """The ``composition.apps:`` value node — the only OPEN registry here."""
    return _child_node(_composition_node(root), "apps")


def _refuse_retired_checks_key(composition: yaml.Node | None, path: Path) -> None:
    """Name the retirement of ``checks:`` instead of letting it read as no gates.

    Falling through to the strip-unknown WARN would drop a declared gate and
    carry on — the silence this channel exists to remove. So the retired key gets
    its own refusal, and it says where the rows moved (supervisor ruling
    2026-08-10; HATS-1545 F2).
    """
    if _child_node(composition, "checks") is None:
        return
    raise ComponentKeyError(
        f"{path}: 'composition.checks:' was retired in HATS-1545 — its rows now live under "
        f"'composition.apps.<app>', where the application owns the grammar below its own key. "
        f"Move each row: the skill/script pair becomes 'run: <skill>/<script>', 'on:' becomes "
        f"'at:' (YAML 1.1 reads a bare 'on' as True), and a rack row names the backlog it gates "
        f"— apps.rack.<backlog>. A wt row keeps its points bare: apps.wt with at: [pre-merge]. "
        f"Refusing rather than dropping it, because a gate that vanishes quietly is the defect "
        f"this channel exists to remove"
    )


def _reject_non_string_keys(
    node: yaml.Node | None, path: Path, trail: tuple[str, ...] = ()
) -> None:
    """Refuse a key YAML resolves to something other than a string.

    Scoped to ``composition.apps`` because that subtree is the OPEN registry: an
    app's block is carried verbatim, so no remap can fix a key after the fact.
    ``on:`` is the live case — YAML 1.1 resolves it to ``True``, and ``on``/``yes``
    then collapse into one cell (measured). The old channel patched that up for
    one known field; under an opaque block ai-hats does not know which key is
    significant, so the trap is removed by refusing the spelling instead.
    """  # comment-length: allow — why a remap is impossible here is the decision
    if isinstance(node, yaml.SequenceNode):
        for index, item in enumerate(node.value):
            _reject_non_string_keys(item, path, (*trail, str(index)))
        return
    if not isinstance(node, yaml.MappingNode):
        return
    for key_node, value_node in node.value:
        if key_node.tag != "tag:yaml.org,2002:str":
            where = ".".join(("composition", "apps", *trail))
            raise ComponentKeyError(
                f"{path}: under {where}, the key {key_node.value!r} is not a string — YAML 1.1 "
                f"reads it as {key_node.tag.rpartition(':')[2]}, so it can never match the key a "
                f"reader looks for; quote it (\"{key_node.value}\") or rename it (e.g. 'on' -> 'at')"
            )
        _reject_non_string_keys(value_node, path, (*trail, str(key_node.value)))


class ComponentConfig(_YamlModel):
    """Parsed config.yaml for a trait or role."""

    name: str = ""
    composition: Composition = Field(default_factory=Composition)
    injection: str = ""
    priorities: list[str] = Field(default_factory=list)
    source_path: Path | None = None

    @classmethod
    def from_yaml(cls, path: Path) -> ComponentConfig:
        data = load_component_yaml(path)
        cls._strip_unknown_composition_keys(data, path)
        return cls.model_validate(
            {**data, "source_path": path, "name": data.get("name") or path.parent.name}
        )

    @staticmethod
    def _strip_unknown_composition_keys(data: dict[str, Any], path: Path) -> None:
        """Pop keys under ``composition:`` no field owns; one stderr WARN each.

        HATS-1152 under the HATS-581 policy: strip, so an OLDER binary survives a
        config a NEWER one wrote — but never silently, because a mistyped binding
        is a gate that never installs. Unlike ``ProjectConfig``, the popped value
        is not stashed for round-trip: library configs have no ``save()`` path.

        Channel is plain stderr, mirroring ``ProjectConfig._strip_unknown_fields``
        — fires at yaml-load and must be visible regardless of log level.
        """
        composition = data.get("composition")
        if not isinstance(composition, dict):
            return
        known = sorted(Composition.model_fields)
        for key in [k for k in composition if k not in known]:
            composition.pop(key)
            close = difflib.get_close_matches(key, known, n=1)
            suggestion = f" — did you mean {close[0]!r}?" if close else ""
            print(
                f"WARN: {path}: dropping unknown key {key!r} under 'composition:'"
                f"{suggestion} (known: {', '.join(known)})",
                file=sys.stderr,
            )


class RuleMetadata(_YamlModel):
    name: str = ""
    description: str = ""
    author: str = ""
    tags: list[str] = Field(default_factory=list)
    delivery: str | None = None

    @classmethod
    def from_yaml(cls, path: Path) -> RuleMetadata:
        if not path.exists():
            return cls()
        return cls.model_validate(yaml.safe_load(path.read_text()) or {})


# Git hook events recognized by the framework. Skills declare their hooks
# under one of these keys in metadata.yaml's `git_hooks:` block. The keys
# match git's actual hook filenames so the dispatcher path is unambiguous.
GIT_HOOK_EVENTS: tuple[str, ...] = (
    "pre-commit",
    "prepare-commit-msg",
    "commit-msg",
    "post-commit",
    "pre-push",
    "pre-rebase",
    # Drift-introducing events — a merge / pull / branch checkout rewrites
    # tracked files, leaving the (untracked, generated) .githooks/ stale. Still
    # VALID hook events a skill may declare; HATS-833 removed the self-heal that
    # used them (healing is now session-start only via HooksManager.sync_hooks).
    "post-merge",
    "post-checkout",
)


# Provider runtime-hook events recognized by the framework (HATS-597).
# Skills declare hooks under one of these keys in metadata.yaml's
# `runtime_hooks:` block. Names match Claude Code's native hook event names
# so the provider can wire them into `.claude/settings.json` verbatim. v1
# implements PreToolUse + PostToolUse; the set is open — adding an event is a
# one-line data change here plus provider support, no structural edit.
RUNTIME_HOOK_EVENTS: tuple[str, ...] = (
    HOOK_PRE_TOOL_USE,
    HOOK_POST_TOOL_USE,
)


class RuntimeHook(_YamlModel):
    """A single provider runtime hook declared by a skill (HATS-597).

    Unlike ``git_hooks`` (a bare ``list[str]`` of script paths), a runtime
    hook carries two fields — the provider tool ``matcher`` and the ``script``
    path relative to the skill directory — so it is modeled as a typed record
    rather than positional dict access (project default: strict typed
    contracts > loose dict access). Frozen so collected hooks are safe to pass
    around and dedupe.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    matcher: str
    script: str


class LeftoverSidecarHooksError(RuntimeError):
    """A skill still ships a ``metadata.yaml`` carrying hook keys after the
    frontmatter cutover (HATS-814).

    The engine reads ``git_hooks`` / ``runtime_hooks`` from ``SKILL.md``
    frontmatter top-level ``ai_hats:`` now; a leftover hook-bearing sidecar would
    be *silently ignored* — a guard that stops materializing is a security
    regression. We fail loud instead, naming the skill + keys + remedy.
    """


class SkillMetadata(_YamlModel):
    """Skill hook wiring, read from ``SKILL.md`` frontmatter top-level ``ai_hats:``.

    `git_hooks` lets a skill declare scripts that should be installed into
    the project's `.githooks/<event>.d/` during composition. Keys are git
    hook event names (see GIT_HOOK_EVENTS); values are lists of script
    paths relative to the skill directory.

    `runtime_hooks` (HATS-597) lets a skill declare provider runtime hooks
    (e.g. Claude Code PreToolUse / PostToolUse). Keys are runtime hook event
    names (see RUNTIME_HOOK_EVENTS); values are lists of RuntimeHook records
    `{matcher, script}`. The assembler materializes the scripts and the
    provider wires them into the native hook channel.

    `triggers` / `skip` (HATS-264): activation hints used to render the
    canonical `routing.md` trigger→skill table. Each item is a short phrase
    describing user intent or a context where this skill applies (or, for
    `skip`, where it should be passed over). Both are optional; skills with
    empty `triggers` are omitted from routing.md but still appear in
    `skills_index.md`.

    `worktree` (HATS-823) rides **opaque** — a raw dict per the ADR-0014 §2
    boundary rule (library never imports wt types); the integrator parses it
    via ``ai_hats_wt.carry.parse_worktree_carry`` at compose time (HATS-863).
    """

    name: str = ""
    description: str = ""
    author: str = ""
    tags: list[str] = Field(default_factory=list)
    pattern: str = ""
    git_hooks: dict[str, list[str]] = Field(default_factory=dict)
    runtime_hooks: dict[str, list[RuntimeHook]] = Field(default_factory=dict)
    worktree: dict[str, Any] = Field(default_factory=dict)
    triggers: list[str] = Field(default_factory=list)
    skip: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalize_git_hooks(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        raw = data.get("git_hooks") or {}
        if not isinstance(raw, dict):
            data["git_hooks"] = {}
            return data
        normalized: dict[str, list[str]] = {}
        for ev, scripts in raw.items():
            if not isinstance(scripts, list):
                continue
            key = str(ev).replace("_", "-")
            if key in GIT_HOOK_EVENTS:
                normalized[key] = [str(s) for s in scripts]
            # Unknown events silently skipped — surfaces upstream via tests.
        data["git_hooks"] = normalized
        return data

    @model_validator(mode="before")
    @classmethod
    def _normalize_runtime_hooks(cls, data: Any) -> Any:
        """Parse + validate the ``runtime_hooks:`` block (HATS-597).

        Unlike ``git_hooks`` (which silently skips unknown events), runtime
        hooks **fail loud** on an unknown event or a malformed row: a dropped
        runtime hook can be a silent safety hole (a guard that never fires),
        so a config typo must surface at load, naming the skill + event.
        """
        if not isinstance(data, dict):
            return data
        raw = data.get("runtime_hooks")
        if not raw:
            data["runtime_hooks"] = {}
            return data
        skill_name = data.get("name", "<unknown>")
        if not isinstance(raw, dict):
            raise ValueError(
                f"skill {skill_name!r}: runtime_hooks must be a mapping of "
                f"event -> [{{matcher, script}}], got {type(raw).__name__}"
            )
        normalized: dict[str, list[dict[str, str]]] = {}
        for ev, rows in raw.items():
            if ev not in RUNTIME_HOOK_EVENTS:
                raise ValueError(
                    f"skill {skill_name!r}: unknown runtime_hooks event {ev!r} "
                    f"(allowed: {', '.join(RUNTIME_HOOK_EVENTS)})"
                )
            if not isinstance(rows, list):
                raise ValueError(
                    f"skill {skill_name!r}: runtime_hooks[{ev!r}] must be a list "
                    f"of {{matcher, script}} entries, got {type(rows).__name__}"
                )
            parsed: list[dict[str, str]] = []
            seen_matchers: set[str] = set()
            for row in rows:
                if not isinstance(row, dict) or "matcher" not in row or "script" not in row:
                    raise ValueError(
                        f"skill {skill_name!r}: runtime_hooks[{ev!r}] entry must "
                        f"have both 'matcher' and 'script' — got {row!r}"
                    )
                matcher = str(row["matcher"])
                # The provider keys a managed settings.json entry by
                # (event, skill, matcher); a duplicate matcher in one event
                # would collapse onto a single entry and silently drop a hook
                # (the exact safety hole this validator exists to prevent).
                # v1: one script per (event, matcher) — fail loud instead.
                if matcher in seen_matchers:
                    raise ValueError(
                        f"skill {skill_name!r}: runtime_hooks[{ev!r}] declares "
                        f"matcher {matcher!r} more than once — only one script "
                        f"per (event, matcher) is supported"
                    )
                seen_matchers.add(matcher)
                parsed.append({"matcher": matcher, "script": str(row["script"])})
            normalized[ev] = parsed

        # Materialized filename is ``<skill>-<basename>`` (managed_runtime_hook_
        # filename), so two DISTINCT scripts sharing a basename would overwrite
        # each other on disk and cross-wire their settings entries. The same
        # script reused across events is fine (one file, several entries).
        basename_source: dict[str, str] = {}
        for rows in normalized.values():
            for row in rows:
                base = Path(row["script"]).name
                prior = basename_source.get(base)
                if prior is not None and prior != row["script"]:
                    raise ValueError(
                        f"skill {skill_name!r}: runtime_hooks scripts {prior!r} "
                        f"and {row['script']!r} share basename {base!r} — they "
                        f"would collide on the materialized filename; give them "
                        f"distinct basenames"
                    )
                basename_source[base] = row["script"]

        data["runtime_hooks"] = normalized
        return data

    @classmethod
    def from_yaml(cls, path: Path) -> SkillMetadata:
        if not path.exists():
            return cls()
        return cls.model_validate(yaml.safe_load(path.read_text()) or {})

    @classmethod
    def from_skill_dir(cls, skill_dir: Path) -> SkillMetadata:
        """Build from ``SKILL.md`` frontmatter top-level ``ai_hats:`` (HATS-814).

        Hook wiring lives under a top-level ``ai_hats:`` frontmatter key
        (governance: ``ai_hats`` = framework hook wiring ONLY, never prose).
        It is NOT nested under ``metadata:`` — the Agent-Skills ``metadata``
        field is a flat ``map<string,string>`` (agnix rejects nested values
        there), and ``metadata:`` is not even a Claude Code frontmatter field.
        The harness strips frontmatter and ignores unknown keys, so this key
        has zero context cost. Malformed frontmatter propagates
        ``FrontmatterError`` — a silent drop on the hook path is a security hole.

        **Cutover guard:** a leftover ``metadata.yaml`` carrying truthy hook
        keys raises :class:`LeftoverSidecarHooksError`. A hookless leftover
        sidecar is tolerated (ignored) — external libraries the engine cannot
        atomically rewrite must keep composing.
        """
        sidecar = skill_dir / "metadata.yaml"
        if sidecar.is_file():
            try:
                raw = yaml.safe_load(sidecar.read_text()) or {}
            except yaml.YAMLError:
                raw = {}
            if isinstance(raw, dict):
                leaked = [k for k in _HOOK_KEYS if raw.get(k)]
                if leaked:
                    # Remedy single-sourced with the HATS-815 bump diagnostic.
                    raise LeftoverSidecarHooksError(leftover_sidecar_remedy(skill_dir.name, leaked))
        fm = read_frontmatter(skill_dir / "SKILL.md")
        ai_hats = fm.get("ai_hats")
        if not isinstance(ai_hats, dict):
            ai_hats = {}
        if ai_hats.get("plan_sections"):
            # Tombstone (HATS-1160 / HATS-1149 A): consumer plan_sections channel
            # deleted — fail LOUD, never no-op; a dropped section weakens the gate.
            raise ValueError(
                f"skill {skill_dir.name!r}: the plan_sections: channel was removed "
                f"(HATS-1160, HATS-1149 decision A) and no longer extends the "
                f"plan-gate. Remove the declaration, or re-introduce the channel "
                f"as an integrator-side live-collect."
            )
        if ai_hats.get("lifecycle_hooks"):
            # Tombstone (HATS-1147, ADR-0019 D8): consumer lifecycle_hooks channel
            # deleted — fail LOUD, never no-op; a dropped edge gate is a gate that
            # never installs (the HYP-078 hole this retirement exists to close).
            raise ValueError(
                f"skill {skill_dir.name!r}: the lifecycle_hooks: channel was removed "
                f"(HATS-1147, ADR-0019 D8) and no longer gates rack FSM edges. "
                f"Remove the declaration, or re-introduce the channel as an "
                f"integrator-side live-collect."
            )
        name = fm.get("name")
        return cls.model_validate(
            {
                "name": name if isinstance(name, str) else "",
                "git_hooks": ai_hats.get("git_hooks") or {},
                "runtime_hooks": ai_hats.get("runtime_hooks") or {},
                "worktree": ai_hats.get("worktree") or {},
            }
        )


def resolve_namespace(name: str) -> str:
    """Convert namespace notation (dev::python) to filesystem path (dev/python)."""
    return name.replace("::", "/")
