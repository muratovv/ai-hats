"""Library domain schema (HATS-863, ex ``ai_hats.models``) — component configs,
rule/skill metadata, hook wiring. T18 (HATS-876) lifts this module into the
``ai-hats-library`` package.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from pydantic import ConfigDict, Field, model_validator

from ai_hats_core import YamlModel as _YamlModel

from ..frontmatter import read_frontmatter
from ..skill_sidecar import _HOOK_KEYS, leftover_sidecar_remedy


class ComponentType(str, Enum):
    RULE = "rule"
    SKILL = "skill"
    TRAIT = "trait"
    ROLE = "role"


# ----- Composition + components -----


class Composition(_YamlModel):
    traits: list[str] = Field(default_factory=list)
    rules: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)


class ComponentConfig(_YamlModel):
    """Parsed config.yaml for a trait or role."""

    name: str = ""
    composition: Composition = Field(default_factory=Composition)
    injection: str = ""
    priorities: list[str] = Field(default_factory=list)
    source_path: Path | None = None

    @classmethod
    def from_yaml(cls, path: Path) -> ComponentConfig:
        data = yaml.safe_load(path.read_text()) or {}
        return cls.model_validate(
            {**data, "source_path": path, "name": data.get("name") or path.parent.name}
        )


class RuleMetadata(_YamlModel):
    name: str = ""
    description: str = ""
    author: str = ""
    tags: list[str] = Field(default_factory=list)

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
    "PreToolUse",
    "PostToolUse",
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
                    raise LeftoverSidecarHooksError(
                        leftover_sidecar_remedy(skill_dir.name, leaked)
                    )
        fm = read_frontmatter(skill_dir / "SKILL.md")
        ai_hats = fm.get("ai_hats")
        if not isinstance(ai_hats, dict):
            ai_hats = {}
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
