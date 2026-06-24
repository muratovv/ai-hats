"""Core data models for ai-hats components (Pydantic v2)."""

from __future__ import annotations

import json
import logging
import re
import sys
import warnings
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    ValidationError,
    computed_field,
    field_validator,
    model_validator,
)

from .frontmatter import read_frontmatter
from .skill_sidecar import _HOOK_KEYS, leftover_sidecar_remedy
from .utils.atomic_io import atomic_write_text

logger = logging.getLogger(__name__)


# ----- Enums -----


class ComponentType(str, Enum):
    RULE = "rule"
    SKILL = "skill"
    TRAIT = "trait"
    ROLE = "role"


class TaskState(str, Enum):
    BRAINSTORM = "brainstorm"
    PLAN = "plan"
    EXECUTE = "execute"
    DOCUMENT = "document"
    REVIEW = "review"
    DONE = "done"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @staticmethod
    def valid_transitions() -> dict[TaskState, list[TaskState]]:
        # CANCELLED is reachable from every non-terminal state — it's the
        # administrative-close exit (won't-fix / duplicate / obsolete) so the
        # task doesn't have to walk the full lifecycle just to be closed.
        return {
            TaskState.BRAINSTORM: [TaskState.PLAN, TaskState.BLOCKED, TaskState.CANCELLED],
            TaskState.PLAN: [TaskState.EXECUTE, TaskState.BLOCKED, TaskState.CANCELLED],
            TaskState.EXECUTE: [
                TaskState.DOCUMENT,
                TaskState.BLOCKED,
                TaskState.FAILED,
                TaskState.CANCELLED,
            ],
            TaskState.DOCUMENT: [TaskState.REVIEW, TaskState.BLOCKED, TaskState.CANCELLED],
            TaskState.REVIEW: [TaskState.DONE, TaskState.FAILED, TaskState.CANCELLED],
            TaskState.BLOCKED: [
                TaskState.BRAINSTORM,
                TaskState.PLAN,
                TaskState.EXECUTE,
                TaskState.DOCUMENT,
                TaskState.CANCELLED,
            ],
            TaskState.FAILED: [TaskState.BRAINSTORM, TaskState.CANCELLED],
            # DONE → EXECUTE: reopen path for epic close-out / forgotten scope
            # (HATS-328). Side effects (clear completed_at, log reopen) live in
            # state.StateManager.transition.
            TaskState.DONE: [TaskState.EXECUTE],
            TaskState.CANCELLED: [],
        }

    def can_transition_to(self, target: TaskState) -> bool:
        return target in self.valid_transitions()[self]


class FeedbackPolicy(str, Enum):
    OFF = "off"
    ALWAYS = "always"
    SMART = "smart"
    HINT = "hint"


class Channel(str, Enum):
    """Harness source channel (HATS-764). Maps an audience to an install spec.

    - ``local``  — ai-hats dev: editable install of a working tree; moving target.
    - ``edge``   — own repos: ``git+https://<repo>@<branch-HEAD-sha>``; moving target.
    - ``stable`` — end users: ``ai-hats==<latest-tag>`` from PyPI; pinned, semver-monotonic.
    """

    LOCAL = "local"
    EDGE = "edge"
    STABLE = "stable"


# ----- Base -----


class _YamlModel(BaseModel):
    """Common base for YAML-round-trippable models.

    Defaults to ``extra="ignore"`` (silently drop unknown keys). Subclasses
    override when needed (e.g. TaskCard needs ``extras`` round-trip).
    Serialization uses ``mode="json"`` via ``to_dict()`` to coerce enums/Paths
    to primitives suitable for ``yaml.safe_dump``.
    """

    model_config = ConfigDict(extra="ignore")

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None):  # pragma: no cover - trivial
        return cls.model_validate(data or {})


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
    # HATS-593: drift-introducing events. A merge / pull / branch checkout
    # rewrites tracked files, leaving the (untracked, generated) .githooks/
    # stale. These hooks re-materialize the hook surface at the moment drift
    # is introduced via `ai-hats self sync-hooks`.
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


# Worktree teardown events a ``wt_out`` hook can bind to (HATS-823, ADR-0012).
# ``wt_in`` fires once after ``git worktree add``; ``wt_out`` fires before
# ``_remove_worktree`` on each of these routes (all also cover the HATS-596
# already-merged short-circuit). An empty / unset ``on`` defaults to all routes.
WT_TEARDOWN_EVENTS: tuple[str, ...] = ("merge", "discard", "cleanup")


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


class WorktreeHook(_YamlModel):
    """A single worktree lifecycle hook declared by a skill (HATS-823, ADR-0012).

    ``script`` is a path relative to the skill directory. ``on`` lists the
    teardown events a ``wt_out`` hook fires on (subset of
    :data:`WT_TEARDOWN_EVENTS`; empty/unset is normalized to *all* routes by
    :meth:`SkillMetadata._normalize_worktree`). ``on`` is always empty for
    ``wt_in`` (it fires once after ``git worktree add``).

    Frozen + ``extra="forbid"``: a malformed row is a silent data-loss hole (a
    ``wt_out`` drain that never runs), so the leaf fails loud — same posture as
    :class:`RuntimeHook`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    script: str
    on: tuple[str, ...] = ()


class WorktreeCarry(_YamlModel):
    """Worktree lifecycle hooks a skill declares (HATS-823, ADR-0012).

    The container is **forward-compatible**: unknown keys are ignored with a
    WARN at parse time (a newer skill declaring a future carry kind must not
    hard-fail composition on an older engine — ADR-0012 Revisions #3), in
    contrast to the fail-loud leaves. Frozen so collected carry is safe to pass
    around and persist into worktree state.
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    wt_in: tuple[WorktreeHook, ...] = ()
    wt_out: tuple[WorktreeHook, ...] = ()

    def is_empty(self) -> bool:
        return not self.wt_in and not self.wt_out


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
    """

    name: str = ""
    description: str = ""
    author: str = ""
    tags: list[str] = Field(default_factory=list)
    pattern: str = ""
    git_hooks: dict[str, list[str]] = Field(default_factory=dict)
    runtime_hooks: dict[str, list[RuntimeHook]] = Field(default_factory=dict)
    worktree: WorktreeCarry = Field(default_factory=WorktreeCarry)
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

    @model_validator(mode="before")
    @classmethod
    def _normalize_worktree(cls, data: Any) -> Any:
        """Parse + validate the ``worktree:`` carry block (HATS-823, ADR-0012).

        Leaf rows fail loud (a dropped ``wt_out`` hook is the data-loss hole this
        mechanism closes); the *container* tolerates unknown keys with a WARN
        (forward-compat — Revisions #3). ``wt_out`` ``on`` is validated against
        :data:`WT_TEARDOWN_EVENTS` and defaults to all routes when unset;
        ``wt_in`` ``on`` is meaningless (fires once at create) so it is dropped
        with a WARN.
        """
        if not isinstance(data, dict):
            return data
        raw = data.get("worktree")
        if not raw:
            data["worktree"] = {}
            return data
        skill_name = data.get("name", "<unknown>")
        if not isinstance(raw, dict):
            raise ValueError(
                f"skill {skill_name!r}: worktree must be a mapping with "
                f"wt_in / wt_out, got {type(raw).__name__}"
            )
        known = {"wt_in", "wt_out"}
        unknown = [k for k in raw if k not in known]
        if unknown:
            warnings.warn(
                f"skill {skill_name!r}: unknown worktree carry key(s) "
                f"{', '.join(map(repr, unknown))} ignored (known: wt_in, wt_out)"
                f" — update ai-hats if this is a newer carry kind",
                stacklevel=2,
            )
        normalized: dict[str, list[dict[str, Any]]] = {}
        for kind in ("wt_in", "wt_out"):
            rows = raw.get(kind)
            if rows is None:
                continue
            if not isinstance(rows, list):
                raise ValueError(
                    f"skill {skill_name!r}: worktree[{kind!r}] must be a list of "
                    f"{{script, on?}} entries, got {type(rows).__name__}"
                )
            parsed: list[dict[str, Any]] = []
            for row in rows:
                if isinstance(row, dict) and True in row and "on" not in row:
                    # PyYAML (YAML 1.1) parses the bare key ``on:`` as boolean
                    # True. The ADR schema spells the field ``on:``, so restore
                    # the string key (the well-known ``on:`` YAML trap, also hit
                    # by GitHub Actions workflows).
                    on_val = row[True]
                    row = {k: v for k, v in row.items() if k is not True}
                    row["on"] = on_val
                if not isinstance(row, dict) or "script" not in row:
                    raise ValueError(
                        f"skill {skill_name!r}: worktree[{kind!r}] entry must "
                        f"have a 'script' — got {row!r}"
                    )
                on_raw = row.get("on", [])
                if not isinstance(on_raw, list):
                    raise ValueError(
                        f"skill {skill_name!r}: worktree[{kind!r}] 'on' must be a "
                        f"list of teardown events, got {type(on_raw).__name__}"
                    )
                on = tuple(str(e) for e in on_raw)
                if kind == "wt_in":
                    if on:
                        warnings.warn(
                            f"skill {skill_name!r}: worktree['wt_in'] entry has "
                            f"'on' {list(on)} — ignored (wt_in fires once at "
                            f"create)",
                            stacklevel=2,
                        )
                    on = ()
                else:  # wt_out
                    bad = [e for e in on if e not in WT_TEARDOWN_EVENTS]
                    if bad:
                        raise ValueError(
                            f"skill {skill_name!r}: worktree['wt_out'] 'on' has "
                            f"unknown event(s) {bad} (allowed: "
                            f"{', '.join(WT_TEARDOWN_EVENTS)})"
                        )
                    if not on:
                        on = WT_TEARDOWN_EVENTS
                parsed.append({"script": str(row["script"]), "on": on})
            normalized[kind] = parsed
        data["worktree"] = normalized
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


# ----- Overlays + feedback config -----


class OverlayConfig(_YamlModel):
    """Per-role customization overlay (add/remove components).

    Wire format nests add/remove sections (``add: {traits: [...], ...}``) while
    the in-memory shape is flat. ``from_dict`` / ``to_dict`` bridge the two.

    **Move-to-end reorder semantic (HATS-421).** Within a single overlay,
    putting the same name in BOTH ``add: [X]`` and ``remove: [X]`` is a
    first-class operation meaning "remove X from its current position and
    re-append it to the layer's tail". The composer applies ``remove`` then
    ``append`` per layer (see ``Composer._apply_overlay``), so this round-trip
    produces a reorder rather than cancelling out. Use it when injection
    order or dedup priority matters.

    Layered semantics (composer applies overlays sequentially, global then
    project): a name removed by global can be re-added by project; a name
    added by global can be removed by project. Project always wins because
    it is applied last.
    """

    add_traits: list[str] = Field(default_factory=list)
    add_rules: list[str] = Field(default_factory=list)
    add_skills: list[str] = Field(default_factory=list)
    remove_traits: list[str] = Field(default_factory=list)
    remove_rules: list[str] = Field(default_factory=list)
    remove_skills: list[str] = Field(default_factory=list)
    injection_append: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> OverlayConfig:
        if not data:
            return cls()
        add = data.get("add") or {}
        remove = data.get("remove") or {}
        return cls(
            add_traits=add.get("traits", []),
            add_rules=add.get("rules", []),
            add_skills=add.get("skills", []),
            remove_traits=remove.get("traits", []),
            remove_rules=remove.get("rules", []),
            remove_skills=remove.get("skills", []),
            injection_append=data.get("injection_append", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        add = {
            k: v
            for k, v in (
                ("traits", self.add_traits),
                ("rules", self.add_rules),
                ("skills", self.add_skills),
            )
            if v
        }
        if add:
            d["add"] = add
        remove = {
            k: v
            for k, v in (
                ("traits", self.remove_traits),
                ("rules", self.remove_rules),
                ("skills", self.remove_skills),
            )
            if v
        }
        if remove:
            d["remove"] = remove
        if self.injection_append:
            d["injection_append"] = self.injection_append
        return d

    @computed_field
    @property
    def is_empty(self) -> bool:
        return not any(
            [
                self.add_traits,
                self.add_rules,
                self.add_skills,
                self.remove_traits,
                self.remove_rules,
                self.remove_skills,
                self.injection_append,
            ]
        )


class SmartThreshold(_YamlModel):
    min_turns: int = 5
    min_tool_calls: int = 10


class SessionRetroConfig(_YamlModel):
    policy: FeedbackPolicy = FeedbackPolicy.SMART
    smart_threshold: SmartThreshold = Field(default_factory=SmartThreshold)
    background: bool = True
    # Optional model override for the single session-reviewer LLM call (HATS-252).
    # When None, the provider CLI's default model is used.
    review_model: str | None = None
    # Deprecated alias retained for back-compat with pre-HATS-252 ai-hats.yaml
    # files (`reflect_model:`). When `review_model` is unset and this field is
    # present, the validator copies it across and emits a DeprecationWarning.
    reflect_model: str | None = None

    @model_validator(mode="after")
    def _alias_reflect_model(self) -> "SessionRetroConfig":
        if self.review_model is None and self.reflect_model is not None:
            import warnings

            warnings.warn(
                "feedback.session_retro.reflect_model is deprecated; rename to review_model.",
                DeprecationWarning,
                stacklevel=2,
            )
            self.review_model = self.reflect_model
        return self


class FeedbackConfig(_YamlModel):
    session_retro: SessionRetroConfig = Field(default_factory=SessionRetroConfig)

    @property
    def is_default(self) -> bool:
        return self == FeedbackConfig()


class HarnessConfig(_YamlModel):
    """Harness source — where ``ai-hats self update`` pulls ai-hats from (HATS-764).

    - ``channel`` — ``local`` | ``edge`` | ``stable`` (default ``stable``). An
      unknown value fails loud via the :class:`Channel` enum.
    - ``repo`` — edge-only override of the upstream repo URL
      (precedence ``AI_HATS_REPO_URL`` env > this field > default upstream https).
    - ``path`` — local-only editable source path (defaults to the project root).

    Inherits ``extra="ignore"`` from :class:`_YamlModel` (NOT ``forbid``): a
    newer ai-hats may add a nested ``harness`` sub-field, and an older binary
    must drop it rather than crash (forward-compat — the top-level strip in
    :meth:`ProjectConfig._strip_unknown_fields` only reaches the outer
    ``harness`` key, never nested ones). The drop is WARNed (below), mirroring
    the top-level strip so a vanished field is observable, not silent.
    """

    channel: Channel = Channel.STABLE
    repo: str | None = None
    path: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _warn_unknown_keys(cls, data: Any) -> Any:
        """WARN (stderr) on an unknown nested key before ``extra="ignore"`` drops
        it — keeps the forward-compat behaviour observable, consistent with
        ``ProjectConfig._strip_unknown_fields`` (HATS-764 review)."""
        if isinstance(data, dict):
            for key in sorted(set(data) - set(cls.model_fields)):
                print(
                    f"WARN: ai-hats.yaml harness: dropping unknown field {key!r} "
                    "(not in this ai-hats version's schema — written by a newer "
                    "ai-hats? run 'ai-hats self update' to use it).",
                    file=sys.stderr,
                )
        return data

    @property
    def is_default(self) -> bool:
        return self == HarnessConfig()

    def to_dict(self) -> dict[str, Any]:
        # Omit None repo/path so a plain `channel: edge` block stays minimal.
        d: dict[str, Any] = {"channel": self.channel.value}
        if self.repo is not None:
            d["repo"] = self.repo
        if self.path is not None:
            d["path"] = self.path
        return d


# ----- ProjectConfig -----


# HATS-408: yaml keys that landed in some v0.6 projects but were never
# wired (or were reverted) before the v0.7 cut. Stripped *before* pydantic
# strict validation so `extra="forbid"` does not fail-loud on every command
# the user runs on a v0.6 project. Add new ghosts here; never remove — this
# is the migration scar tissue, not a feature flag list.
_DEPRECATED_PROJECT_FIELDS: frozenset[str] = frozenset({
    "imports_order",  # HATS-290 planned but reverted; ghost in some v0.6 yamls.
})


# HATS-792: highest ai-hats.yaml ``schema_version`` this binary understands.
# Migrations in ``from_yaml`` run upward ONLY to this version; a yaml whose
# ``schema_version`` exceeds it was written by a NEWER ai-hats whose format we
# cannot safely interpret OR round-trip. Rather than silently treat it as v4
# (and risk clobbering future fields on the next ``save()``), ``from_yaml``
# fails loud with a remediation pointer (``ai-hats self update``). Bump this in
# lockstep with the migration chain + the ``to_dict`` ``schema_version`` literal.
KNOWN_SCHEMA_VERSION = 4


class ProjectConfigError(ValueError):
    """Raised when ai-hats.yaml fails schema validation."""


class ProjectConfig(_YamlModel):
    """ai-hats.yaml — unified project configuration.

    Sections:
      - Project: provider, library_paths, ai_hats_dir
      - Role: active_role, default_role, customizations
      - Feedback: session_retro
      - Harness: harness (channel local|edge|stable, repo, path — HATS-764)
      - Meta: schema_version (4 = current)
    """

    # Reject unknown keys so typos in ai-hats.yaml fail loudly instead of silently dropping.
    model_config = ConfigDict(extra="forbid")

    provider: str = "gemini"
    default_role: str = ""
    active_role: str = ""
    schema_version: int = 4
    # HATS-471/469: monotonic counter for one-shot migrations replayed
    # at install-time refresh paths (``Assembler.init`` and the
    # ``do_bump`` CLI pipeline; both invoke ``_refresh(install_time=True)``
    # which calls ``migrations.run_pending``). Orthogonal to
    # ``schema_version`` (which describes yaml format). The registry in
    # ``ai_hats/migrations.py`` runs entries with
    # ``m.step > migration_step``; after each successful entry the
    # counter advances and persists. Greenfield init seeds it to the
    # latest registry step; existing projects seed to 0 and replay the
    # whole registry once (idempotent by invariant of every migration
    # function).
    migration_step: int = 0
    # HATS-316: where ai-hats keeps its managed artefacts. Migration (v3→v4)
    # and `ai-hats init` write this field to disk explicitly so users see it.
    # The class-level default is a bootstrap safety net for `ProjectConfig()`
    # calls without a yaml file (tests, scratch); `from_yaml` enforces that v4
    # yaml on disk contains the field explicitly.
    ai_hats_dir: str = ".agent/ai-hats"
    # HATS-334: optional override for ai-hats venv location. None → default
    # `<ai_hats_dir>/.venv` (managed by ai-hats). Set to a relative or
    # absolute path to point ai-hats at a user-owned venv. Read by
    # `paths.venv_path()` and by the bash launcher (HATS-339) via grep.
    venv_path: str | None = None
    library_paths: list[str] = Field(default_factory=list)
    customizations: dict[str, OverlayConfig] = Field(default_factory=dict)
    feedback: FeedbackConfig = Field(default_factory=FeedbackConfig)
    manage_gitignore: bool = True
    task_prefix: str = "TASK"
    # HATS-764: harness source (channel local|edge|stable). Optional — an
    # ai-hats.yaml with no `harness:` block loads as the `stable` default and
    # saves byte-clean (omitted from to_dict when default). Drives `self update`
    # source + downgrade-guard selection.
    harness: HarnessConfig = Field(default_factory=HarnessConfig)

    # HATS-792: same-version unknown TOP-LEVEL keys, preserved for round-trip.
    # Mirrors the TaskCard ``extras`` pattern (capture-on-load, merge-on-dump),
    # but ProjectConfig is ``extra="forbid"`` and pre-strips unknown keys in the
    # classmethod ``from_yaml`` BEFORE ``model_validate`` runs — so the stash
    # cannot be a validated field (it would re-trip ``forbid``). Instead the
    # popped keys live on a ``PrivateAttr`` set after validation, and
    # ``to_dict`` merges them back so an OLDER binary preserves (not drops) a
    # field a NEWER binary wrote, while the HATS-581 stderr WARN still fires.
    # Only populated when ``schema_version <= KNOWN_SCHEMA_VERSION`` (a newer
    # schema fails loud in ``from_yaml`` and never reaches this preserve seam).
    _extra: dict[str, Any] = PrivateAttr(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _coerce_customizations(cls, data: Any) -> Any:
        """Customizations arrive as nested dicts; route each through OverlayConfig.from_dict."""
        if isinstance(data, dict) and data.get("customizations"):
            data["customizations"] = {
                role: OverlayConfig.from_dict(overlay) if isinstance(overlay, dict) else overlay
                for role, overlay in data["customizations"].items()
            }
        return data

    @field_validator("provider")
    @classmethod
    def _validate_provider(cls, value: str) -> str:
        # Lazy import to avoid models <-> providers cycle (providers -> composer -> models).
        from .providers import PROVIDERS

        if value not in PROVIDERS:
            allowed = ", ".join(sorted(PROVIDERS))
            raise ValueError(f"unknown provider {value!r} — allowed: {allowed}")
        return value

    @field_validator("ai_hats_dir")
    @classmethod
    def _validate_ai_hats_dir(cls, value: str) -> str:
        from .paths import normalize_ai_hats_dir

        return normalize_ai_hats_dir(value)

    @field_validator("venv_path")
    @classmethod
    def _validate_venv_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        from .paths import normalize_venv_path

        return normalize_venv_path(value)

    @classmethod
    def from_yaml(cls, path: Path) -> ProjectConfig:
        if not path.exists():
            return cls()
        data = yaml.safe_load(path.read_text()) or {}
        # HATS-792: fail loud on a schema_version this binary cannot understand.
        # Migrations below run upward ONLY to KNOWN_SCHEMA_VERSION; a higher
        # value was written by a NEWER ai-hats. Silently treating it as v4 would
        # both misread its (unknown) format AND risk clobbering future fields on
        # the next save() — so refuse to operate and point at the recovery path.
        on_disk_version = data.get("schema_version", 1)
        if isinstance(on_disk_version, int) and on_disk_version > KNOWN_SCHEMA_VERSION:
            raise ProjectConfigError(
                f"{path}: schema_version {on_disk_version} is newer than this "
                f"ai-hats (knows <={KNOWN_SCHEMA_VERSION}) — run 'ai-hats self update'."
            )
        if data.get("schema_version", 1) < 2:
            data = _migrate_v1_to_v2(path, data)
        if data.get("schema_version", 1) < 3:
            data = _migrate_v2_to_v3(data)
        if data.get("schema_version", 1) < 4:
            data = _migrate_v3_to_v4(path, data)
        # HATS-316: v4 yaml must contain ai_hats_dir explicitly. The pydantic
        # default is a bootstrap-only safety net for `ProjectConfig()` without
        # a yaml; on-disk yaml is strict so the path stays visible to users.
        if "ai_hats_dir" not in data:
            raise ProjectConfigError(
                f"Invalid {path}:\n  - ai_hats_dir: field required "
                "(add 'ai_hats_dir: .agent/ai-hats' to ai-hats.yaml)"
            )
        # HATS-408: drop known-deprecated ghosts BEFORE strict pydantic
        # validation so v0.6 projects do not crash every ai-hats command
        # before the inline v0.6 → v0.7 migration (HATS-415, runs in
        # ``Assembler.bump``) gets a chance. Mutates ``data`` in-place
        # (the healed shape is what we'd want to persist on a save anyway).
        cls._strip_deprecated_fields(data, path)
        # HATS-581: forward-compat — drop unknown keys (warn, don't crash) so
        # an OLDER binary survives a yaml a NEWER binary wrote (e.g.
        # ``migration_step``, added without a schema_version bump). Runs AFTER
        # the deprecated strip so known ghosts keep their specific message.
        # HATS-792: the popped keys are returned so they can be PRESERVED (not
        # silently lost) on the next save — same-version round-trip.
        extra = cls._strip_unknown_fields(data, path)
        # HATS-408: heal empty default_role from active_role on load. Any
        # ai-hats command that needs an "effective role" already falls back
        # to (active_role or default_role); persisting the heal makes the
        # downstream contract — default_role is the source of truth — true.
        cls._heal_default_role(data, path)
        try:
            cfg = cls.model_validate(data)
        except ValidationError as e:
            raise ProjectConfigError(_format_project_config_error(path, e)) from e
        # HATS-792: stash the popped unknown top-level keys so to_dict can
        # round-trip them. Set after validation because the PrivateAttr stash
        # is not a model field (extra="forbid" would re-trip on it).
        cfg._extra = extra
        return cfg

    @staticmethod
    def _strip_deprecated_fields(data: dict[str, Any], path: Path) -> None:
        """Remove known-deprecated keys from `data` in place; one stderr WARN
        per stripped field. Idempotent — silent if no deprecated keys present.

        Channel: plain stderr (not `logging`) — fires at yaml-load, before
        any logging config is in place, and must be user-visible regardless
        of log level. Format mirrors `print(..., file=sys.stderr)` used by
        HATS-407 cleanup paths.
        """
        for field in sorted(_DEPRECATED_PROJECT_FIELDS):
            if field in data:
                data.pop(field)
                print(
                    f"WARN: {path}: dropping deprecated field {field!r} "
                    f"(no longer supported; remove from yaml to silence).",
                    file=sys.stderr,
                )

    @classmethod
    def _strip_unknown_fields(cls, data: dict[str, Any], path: Path) -> dict[str, Any]:
        """Pop keys not in the model schema; one stderr WARN per key. Returns
        the popped ``{key: value}`` map so the caller can PRESERVE them.

        HATS-581 forward-compat seam. A NEWER ai-hats may add a field to
        ai-hats.yaml without bumping ``schema_version`` (``migration_step``
        did exactly this — orthogonal to schema_version by design). An OLDER
        binary that doesn't know the field must not hard-crash on it: strip
        it (so ``extra="forbid"`` validation succeeds) with a visible WARN.

        HATS-792: the stripped values are no longer thrown away — they are
        returned and stashed on ``_extra`` so ``to_dict`` round-trips them.
        Read→write therefore preserves the unknown field's key+value instead of
        dropping it on the next ``save()``. The WARN is RETAINED (HATS-581): the
        vanish-from-the-typed-model is still observable; what changes is that the
        bytes survive a save. (A genuinely newer SCHEMA fails loud earlier in
        ``from_yaml`` and never reaches this same-version preserve seam.)

        Must run AFTER ``_strip_deprecated_fields`` so known ghosts keep their
        specific message instead of falling through to the generic one here.
        ``extra="forbid"`` stays as a backstop for nested models / direct
        ``model_validate`` callers; after this pre-strip it is unreachable
        from ``from_yaml``.

        Channel: plain stderr (fires at yaml-load, before logging config).
        """
        extra: dict[str, Any] = {}
        for field in sorted(set(data) - set(cls.model_fields)):
            extra[field] = data.pop(field)
            print(
                f"WARN: {path}: dropping unknown field {field!r} "
                "(not in this ai-hats version's schema — written by a newer "
                "ai-hats? run 'ai-hats self update' to use it).",
                file=sys.stderr,
            )
        return extra

    @staticmethod
    def _heal_default_role(data: dict[str, Any], path: Path) -> None:
        """If `default_role` is empty/missing and `active_role` is set, copy
        `active_role` into `default_role`. Single stderr WARN per heal.
        Idempotent — silent if both already set or both already empty.
        """
        active = data.get("active_role") or ""
        default = data.get("default_role") or ""
        if active and not default:
            data["default_role"] = active
            print(
                f"WARN: {path}: healed default_role := active_role "
                f"({active!r}).",
                file=sys.stderr,
            )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "schema_version": 4,
            "provider": self.provider,
            # HATS-316: ai_hats_dir is unconditionally serialized so users
            # see the configurable path in their ai-hats.yaml.
            "ai_hats_dir": self.ai_hats_dir,
            # HATS-471: migration_step is unconditionally serialized once
            # any save fires — same as schema_version. Greenfield init
            # seeds it to ``migrations.latest_step()``; the registry
            # runner persists subsequent advances. Existing pre-HATS-471
            # projects load with the pydantic default 0 (no field in
            # yaml) and pick up the field on the next save.
            "migration_step": self.migration_step,
            "library_paths": self.library_paths,
            "active_role": self.active_role,
            "default_role": self.default_role,
        }
        # HATS-334: venv_path is opt-in — omitted from yaml when None so
        # existing files without the field stay clean and the field appears
        # only when the user actually picks an override.
        if self.venv_path is not None:
            d["venv_path"] = self.venv_path
        live_customs = {
            name: overlay.to_dict()
            for name, overlay in self.customizations.items()
            if not overlay.is_empty
        }
        if live_customs:
            d["customizations"] = live_customs
        if not self.feedback.is_default:
            d["feedback"] = self.feedback.to_dict()
        if not self.manage_gitignore:
            d["manage_gitignore"] = False
        if self.task_prefix != "TASK":
            d["task_prefix"] = self.task_prefix
        # HATS-764: harness is opt-in — omitted when default (stable, no
        # repo/path) so existing yamls without the block stay byte-clean.
        if not self.harness.is_default:
            d["harness"] = self.harness.to_dict()
        # HATS-792: round-trip same-version unknown top-level keys captured on
        # load (mirrors TaskCard.to_dict). Known fields take precedence on an
        # accidental collision; _extra should never hold a known key since
        # _strip_unknown_fields only pops set(data) - set(model_fields), but we
        # defend against direct mutation of _extra. Default-empty for any
        # instance built without from_yaml, so harness/byte-clean is unaffected.
        for k, v in self._extra.items():
            if k not in type(self).model_fields:
                d.setdefault(k, v)
        return d

    def save(self, path: Path) -> None:
        # HATS-792 downgrade-clobber guard: refuse to overwrite an on-disk
        # ai-hats.yaml whose schema_version is newer than this binary knows.
        # from_yaml already fails loud on such a file, so the normal
        # load→mutate→save flow never reaches here with a future config; this
        # guards the bypass paths that construct a ProjectConfig WITHOUT loading
        # the existing file first (e.g. a fresh ProjectConfig().save(path) over
        # a future file, or a re-init) — an old binary must not silently stomp a
        # future config it cannot represent. Best-effort: a malformed/unreadable
        # existing file is left to the normal load path to diagnose.
        if path.exists():
            try:
                existing = yaml.safe_load(path.read_text()) or {}
            except yaml.YAMLError:
                existing = {}
            on_disk_version = existing.get("schema_version", 1) if isinstance(existing, dict) else 1
            if isinstance(on_disk_version, int) and on_disk_version > KNOWN_SCHEMA_VERSION:
                raise ProjectConfigError(
                    f"{path}: refusing to overwrite — on-disk schema_version "
                    f"{on_disk_version} is newer than this ai-hats (knows "
                    f"<={KNOWN_SCHEMA_VERSION}) — run 'ai-hats self update'."
                )
        atomic_write_text(
            path, yaml.dump(self.to_dict(), default_flow_style=False, allow_unicode=True)
        )

    @staticmethod
    def validate_task_prefix(prefix: str) -> str:
        """Normalize and validate a task-id prefix. Raises ValueError if invalid."""
        import re as _re

        if not isinstance(prefix, str):
            raise ValueError("task_prefix must be a string")
        cleaned = prefix.strip()
        if not _re.fullmatch(r"[A-Z][A-Z0-9]*", cleaned):
            raise ValueError(
                f"Invalid task_prefix: {prefix!r}. "
                "Must match [A-Z][A-Z0-9]* (uppercase letter/digit, starts with letter)."
            )
        return cleaned

    @classmethod
    def resolve_task_prefix(cls, project_dir: Path, config_path: Path) -> str:
        """Return the task-id prefix for `project_dir`, persisting an auto-detected
        value for legacy projects so we only pay the detection cost once.

        Precedence:
          1. Explicit `task_prefix` in ai-hats.yaml.
          2. Auto-detect from existing `.agent/backlog/tasks/<PREFIX>-NNN/` dirs —
             persisted to yaml if yaml exists, so subsequent runs are O(1).
          3. Default "TASK" for greenfield projects.
        """
        raw: dict[str, Any] = {}
        if config_path.exists():
            raw = yaml.safe_load(config_path.read_text()) or {}
            if isinstance(raw.get("task_prefix"), str) and raw["task_prefix"].strip():
                return raw["task_prefix"].strip()

        detected = cls._detect_prefix_from_tasks(project_dir)
        if detected and config_path.exists():
            # Persist the detected prefix so legacy repos don't re-detect every call.
            raw["task_prefix"] = detected
            atomic_write_text(
                config_path, yaml.dump(raw, default_flow_style=False, allow_unicode=True)
            )
            return detected
        if detected:
            return detected
        return "TASK"

    @staticmethod
    def _detect_prefix_from_tasks(project_dir: Path) -> str | None:
        """Return the common prefix of existing task dirs, or None if ambiguous/empty."""
        import re as _re

        from .paths import tasks_dir as _tasks_dir

        tasks_dir = _tasks_dir(project_dir)
        if not tasks_dir.is_dir():
            return None
        prefixes: set[str] = set()
        for d in tasks_dir.iterdir():
            if not d.is_dir():
                continue
            m = _re.match(r"^([A-Z][A-Z0-9]*)-\d+$", d.name)
            if m:
                prefixes.add(m.group(1))
        if len(prefixes) == 1:
            return prefixes.pop()
        return None


class UserConfigError(ValueError):
    """Raised when ~/.ai-hats/customizations.yaml fails schema validation."""


class UserConfig(_YamlModel):
    """~/.ai-hats/customizations.yaml — user-level role customizations (HATS-421).

    Symmetric to ``ProjectConfig.customizations`` but lives in the user's home
    directory and applies to every project the user opens. Same ``OverlayConfig``
    schema per role.

    Merge order at compose time: built-in role composition → user (this)
    → project. Project applied last wins on conflict. Within each layer, the
    composer's ``_apply_overlay`` runs ``remove`` then ``append`` — so
    ``add: X`` + ``remove: X`` inside a single layer is a first-class
    "move X to that layer's tail" reorder operation.

    Schema_version is intentionally shared with ``ProjectConfig`` (currently 4):
    bumping the project schema bumps this one too, so migration healers update
    both files in lockstep.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 4
    customizations: dict[str, OverlayConfig] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _coerce_customizations(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("customizations"):
            data["customizations"] = {
                role: OverlayConfig.from_dict(overlay) if isinstance(overlay, dict) else overlay
                for role, overlay in data["customizations"].items()
            }
        return data

    @classmethod
    def default_path(cls) -> Path:
        """Canonical location: ``<user_home>/.ai-hats/customizations.yaml``.

        ``user_home`` honours the ``AI_HATS_USER_HOME`` env override
        (HATS-532) so e2e tests can isolate the global-layer file
        without overriding ``HOME`` (which would break claude auth).
        """
        from .paths import user_home
        return user_home() / ".ai-hats" / "customizations.yaml"

    @classmethod
    def from_yaml(cls, path: Path) -> UserConfig:
        """Load the user customization file.

        - Missing file → empty ``UserConfig`` (silent default, symmetric to a
          fresh project before ``ai-hats config customize`` has been run).
        - Malformed yaml or schema violation → ``UserConfigError`` with the
          path up front so the user knows which file to fix.
        """
        if not path.exists():
            return cls()
        try:
            data = yaml.safe_load(path.read_text()) or {}
        except yaml.YAMLError as e:
            raise UserConfigError(f"Invalid {path}:\n  - yaml parse error: {e}") from e
        if not isinstance(data, dict):
            raise UserConfigError(
                f"Invalid {path}:\n  - top-level value must be a mapping, got {type(data).__name__}"
            )
        try:
            return cls.model_validate(data)
        except ValidationError as e:
            raise UserConfigError(_format_project_config_error(path, e)) from e

    def overlay_for(self, role_name: str) -> OverlayConfig | None:
        """Return the overlay for ``role_name`` or ``None`` if absent/empty.

        Used by the assembler to compose the global layer; dormant roles
        (customized here but not active in the current project) trivially
        resolve to ``None`` and are ignored downstream.
        """
        overlay = self.customizations.get(role_name)
        if overlay is None or overlay.is_empty:
            return None
        return overlay

    def to_dict(self) -> dict[str, Any]:
        live = {
            name: overlay.to_dict()
            for name, overlay in self.customizations.items()
            if not overlay.is_empty
        }
        d: dict[str, Any] = {"schema_version": self.schema_version}
        if live:
            d["customizations"] = live
        return d

    def save(self, path: Path) -> None:
        """Persist the file. If every overlay is empty, delete the file
        instead of writing an empty stub (keeps ``~/.ai-hats/`` tidy).
        """
        live = any(not overlay.is_empty for overlay in self.customizations.values())
        if not live:
            if path.exists():
                # File is empty by contract — no recovery value in a
                # snapshot. Whitelist with reason.
                path.unlink()  # safe-delete: ok empty-config
            return
        atomic_write_text(
            path, yaml.dump(self.to_dict(), default_flow_style=False, allow_unicode=True)
        )


def _format_project_config_error(path: Path, err: ValidationError) -> str:
    """Render a Pydantic ValidationError as a concise, actionable message.

    One issue per line, prefixed with the offending key path. Keeps the file
    path up-front so users know which ai-hats.yaml is broken.
    """
    lines = [f"Invalid {path}:"]
    for issue in err.errors():
        loc = ".".join(str(p) for p in issue["loc"]) or "<root>"
        if issue["type"] == "extra_forbidden":
            lines.append(f"  - unknown key {loc!r}")
        else:
            lines.append(f"  - {loc}: {issue['msg']}")
    return "\n".join(lines)


def _migrate_v1_to_v2(yaml_path: Path, data: dict[str, Any]) -> dict[str, Any]:
    """Auto-migrate schema v1 → v2: merge profile.json into ai-hats.yaml.

    Runs once when a v1 ai-hats.yaml is loaded. Merges active_role, provider,
    and feedback from adjacent profile.json (if present), writes the unified
    YAML, and renames profile.json to profile.json.bak.
    """
    profile_path = yaml_path.parent / "profile.json"
    if profile_path.exists():
        try:
            profile = json.loads(profile_path.read_text())
            if profile.get("provider"):
                data["provider"] = profile["provider"]
            data["active_role"] = profile.get("active_role", "")
            if "feedback" in profile:
                data["feedback"] = profile["feedback"]
            profile_path.rename(profile_path.with_suffix(".json.bak"))
        except (json.JSONDecodeError, OSError):
            pass  # corrupt profile.json — skip, use YAML as-is

    data["schema_version"] = 2
    atomic_write_text(yaml_path, yaml.dump(data, default_flow_style=False, allow_unicode=True))
    logger.info("Migrated profile.json → ai-hats.yaml (schema v2)")
    return data


def _migrate_v2_to_v3(data: dict[str, Any]) -> dict[str, Any]:
    """Auto-migrate schema v2 → v3 (HATS-285).

    v3 introduces the layered canonical layout (.agent/ai-hats/) and the
    `./CLAUDE.md` scaffold-as-asset. The yaml itself only needs a version
    bump — the filesystem cleanup (stripping the legacy uppercase block
    from `./CLAUDE.md`) lives in `Assembler._migrate_claude_md_to_v3`,
    which runs at the start of `init`/`set_role`/`bump`.
    """
    data["schema_version"] = 3
    return data


def _migrate_v3_to_v4(yaml_path: Path, data: dict[str, Any]) -> dict[str, Any]:
    """Auto-migrate schema v3 → v4 (HATS-316).

    v4 introduces the unified `<ai_hats_dir>` layout: all framework-managed
    artefacts (sessions/, tracker/, library/, STATE.md, ...) live under a
    single configurable root. This migration writes the canonical default
    `.agent/ai-hats` to disk explicitly so users see the configurable path
    in their `ai-hats.yaml`. Actual file moves happen in HATS-312/313/314.
    """
    if "ai_hats_dir" not in data:
        data["ai_hats_dir"] = ".agent/ai-hats"
    data["schema_version"] = 4
    atomic_write_text(yaml_path, yaml.dump(data, default_flow_style=False, allow_unicode=True))
    logger.info("Migrated ai-hats.yaml to schema v4 (added ai_hats_dir)")
    return data


# ----- Task cards -----


class WorkLogEntry(_YamlModel):
    """Single work log entry with timestamp and session tracking."""

    timestamp: str = ""
    message: str = ""


_DIGEST_LEN = 12
_DIGEST_RE = re.compile(rf"^[0-9a-f]{{{_DIGEST_LEN}}}$")


class Attachment(_YamlModel):
    """Manifest entry for a file under tasks/<ID>/attachments/.

    The on-disk blob lives in the task's ``attachments/`` directory; this
    record carries the metadata. ``digest`` is the first 12 hex chars of the
    blob's SHA-256 — full hash would balloon ``task.yaml`` and tax every
    agent that loads the card. 48 bits gives a birthday-collision bound
    around 2^24 attachments, vastly beyond any realistic per-task scale.
    """

    name: str = ""
    digest: str = ""
    added: str = ""
    note: str = ""

    @field_validator("digest")
    @classmethod
    def _check_digest(cls, value: str) -> str:
        if value and not _DIGEST_RE.match(value):
            raise ValueError(
                f"digest must be {_DIGEST_LEN} lowercase hex chars (got {value!r})"
            )
        return value


class TaskCard(_YamlModel):
    """YAML task card for state machine.

    Unknown YAML keys are captured into ``extras`` and round-tripped verbatim
    on save. This guards against silent data loss when callers add new fields
    (e.g. ``acceptance_criteria``) that aren't part of the typed schema.
    """

    #: typed fields recognized by from_dict / to_dict; everything else → extras
    _KNOWN_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "id",
            "title",
            "state",
            "description",
            "priority",
            "assignee",
            "reviewer",
            "role",
            "parent_task",
            "subtasks",
            "depends_on",
            "related",
            "see_also",
            "folded_into",
            "tags",
            "work_log",
            "attachments",
            "final_state",
            "resolution",
            "created",
            "updated",
            "completed_at",
        }
    )

    id: str
    title: str
    state: TaskState = TaskState.BRAINSTORM
    description: str = ""
    priority: str = "medium"
    assignee: str = ""
    reviewer: str = "user"
    role: str = ""
    parent_task: str = ""
    subtasks: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    related: list[str] = Field(default_factory=list)
    see_also: list[str] = Field(default_factory=list)
    folded_into: str = ""
    tags: list[str] = Field(default_factory=list)
    work_log: list[WorkLogEntry] = Field(default_factory=list)
    attachments: list[Attachment] = Field(default_factory=list)
    final_state: str = ""
    resolution: str = ""
    created: str = ""
    updated: str = ""
    completed_at: str = ""
    extras: dict[str, Any] = Field(default_factory=dict)

    @field_validator("work_log", mode="before")
    @classmethod
    def _coerce_work_log(cls, value: Any) -> Any:
        """Older task cards wrote work_log as bare strings (``"date: msg"``).

        Coerce those to WorkLogEntry shape so historical cards still load.
        """
        if not isinstance(value, list):
            return value
        return [
            v if isinstance(v, (dict, WorkLogEntry)) else {"timestamp": "", "message": str(v)}
            for v in value
        ]

    @field_validator("created", "updated", "completed_at", mode="before")
    @classmethod
    def _stringify_timestamp(cls, value: Any) -> Any:
        """YAML literals like ``2026-04-06`` (no quotes) parse as ``datetime.date``.

        Historical cards rely on this; keep the field type as ``str`` but
        stringify non-str inputs on load.
        """
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        return value.isoformat() if hasattr(value, "isoformat") else str(value)

    @model_validator(mode="before")
    @classmethod
    def _capture_extras(cls, data: Any) -> Any:
        """Move non-schema keys into ``extras`` before field parsing."""
        if not isinstance(data, dict):
            return data
        captured = {k: v for k, v in data.items() if k not in cls._KNOWN_FIELDS and k != "extras"}
        if captured:
            # Merge with any explicit extras passed in (explicit wins).
            merged = {**captured, **(data.get("extras") or {})}
            data = {k: v for k, v in data.items() if k in cls._KNOWN_FIELDS or k == "extras"}
            data["extras"] = merged
        return data

    def transition_to(self, new_state: TaskState) -> None:
        if not self.state.can_transition_to(new_state):
            raise ValueError(
                f"Invalid transition: {self.state.value} → {new_state.value}. "
                f"Valid: {[s.value for s in self.state.valid_transitions()[self.state]]}"
            )
        self.state = new_state

    def log_work(self, message: str, session_id: str = "") -> None:
        """Append a work log entry."""
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if session_id:
            message = f"[Session {session_id}] {message}"
        self.work_log.append(WorkLogEntry(timestamp=ts, message=message))

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "state": self.state.value,
            "description": self.description,
            "priority": self.priority,
            "assignee": self.assignee,
            "reviewer": self.reviewer,
            "role": self.role,
            "parent_task": self.parent_task,
            "subtasks": self.subtasks,
            "tags": self.tags,
            "work_log": [e.to_dict() for e in self.work_log],
            "created": self.created,
            "updated": self.updated,
        }
        if self.final_state:
            d["final_state"] = self.final_state
        if self.resolution:
            d["resolution"] = self.resolution
        if self.completed_at:
            d["completed_at"] = self.completed_at
        # Only emit depends_on when non-empty: keeps existing pre-HATS-198
        # YAML files byte-clean on first save (no spurious `depends_on: []`
        # noise in diffs). Cards with real blockers still serialize as expected.
        if self.depends_on:
            d["depends_on"] = self.depends_on
        # Same byte-clean rule for the HATS-371 link fields — only emit when set.
        if self.related:
            d["related"] = self.related
        if self.see_also:
            d["see_also"] = self.see_also
        if self.folded_into:
            d["folded_into"] = self.folded_into
        if self.attachments:
            d["attachments"] = [a.to_dict() for a in self.attachments]
        # Round-trip unknown fields verbatim. Known fields take precedence in
        # case of accidental collision (extras should never contain known keys
        # since _capture_extras filters them out, but we defend against direct
        # mutation of task.extras).
        for k, v in self.extras.items():
            if k not in self._KNOWN_FIELDS:
                d[k] = v
        return d

    @classmethod
    def from_yaml(cls, path: Path) -> TaskCard:
        return cls.model_validate(yaml.safe_load(path.read_text()) or {})

    @classmethod
    def load_header(cls, path: Path) -> dict[str, str]:
        """Cheap header read for STATE.md rendering.

        Extracts the seven scalar fields needed by ``_update_state_md`` via a
        single regex pass — ~60× faster than ``from_yaml`` on large cards
        because ``description``, ``work_log``, and ``acceptance_criteria`` are
        never decoded.

        Falls back to a full ``from_yaml`` for any card where the regex
        cannot find both ``id`` and ``state`` (e.g. multi-line block scalars,
        unusual layouts) — guarantees parity with the slow path.
        """
        text = path.read_text()
        fields: dict[str, str] = {}
        for m in _TASK_HEADER_RE.finditer(text):
            fields[m.group("key")] = _unquote_yaml_scalar(m.group("val"))
        if "id" not in fields or "state" not in fields:
            full = cls.from_yaml(path)
            return {
                "id": full.id,
                "title": full.title,
                "state": full.state.value,
                "priority": full.priority,
                "assignee": full.assignee,
                "reviewer": full.reviewer,
                "role": full.role,
            }
        fields.setdefault("title", "")
        fields.setdefault("priority", "medium")
        fields.setdefault("assignee", "")
        fields.setdefault("reviewer", "user")
        fields.setdefault("role", "")
        return fields

    def save(self, path: Path) -> None:
        atomic_write_text(
            path,
            yaml.dump(
                self.to_dict(), default_flow_style=False, allow_unicode=True, sort_keys=False
            ),
        )


_TASK_HEADER_RE = re.compile(
    r"^(?P<key>id|title|state|priority|assignee|reviewer|role):[ \t]*(?P<val>.*)$",
    re.MULTILINE,
)


def _unquote_yaml_scalar(value: str) -> str:
    """Strip outer YAML quotes and unescape doubled single quotes."""
    v = value.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        inner = v[1:-1]
        if v[0] == "'":
            inner = inner.replace("''", "'")
        return inner
    return v


def resolve_namespace(name: str) -> str:
    """Convert namespace notation (dev::python) to filesystem path (dev/python)."""
    return name.replace("::", "/")
