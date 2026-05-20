"""Core data models for ai-hats components (Pydantic v2)."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    computed_field,
    field_validator,
    model_validator,
)

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


class LifecycleEvent(str, Enum):
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    TASK_START = "task_start"
    TASK_COMPLETE = "task_complete"
    TASK_FAILED = "task_failed"
    ERROR = "error"


class FeedbackPolicy(str, Enum):
    OFF = "off"
    ALWAYS = "always"
    SMART = "smart"
    HINT = "hint"


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


class HooksConfig(_YamlModel):
    session_start: list[str] = Field(default_factory=list)
    session_end: list[str] = Field(default_factory=list)
    task_start: list[str] = Field(default_factory=list)
    task_complete: list[str] = Field(default_factory=list)
    task_failed: list[str] = Field(default_factory=list)
    error: list[str] = Field(default_factory=list)

    def get_scripts(self, event: LifecycleEvent) -> list[str]:
        return getattr(self, event.value, [])


class Composition(_YamlModel):
    traits: list[str] = Field(default_factory=list)
    rules: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    hooks: HooksConfig = Field(default_factory=HooksConfig)


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
)


class SkillMetadata(_YamlModel):
    """Parsed metadata.yaml for a skill.

    `git_hooks` lets a skill declare scripts that should be installed into
    the project's `.githooks/<event>.d/` during composition. Keys are git
    hook event names (see GIT_HOOK_EVENTS); values are lists of script
    paths relative to the skill directory.

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

    @classmethod
    def from_yaml(cls, path: Path) -> SkillMetadata:
        if not path.exists():
            return cls()
        return cls.model_validate(yaml.safe_load(path.read_text()) or {})


# ----- Overlays + feedback config -----


class OverlayConfig(_YamlModel):
    """Per-role customization overlay (add/remove components).

    Wire format nests add/remove sections (``add: {traits: [...], ...}``) while
    the in-memory shape is flat. ``from_dict`` / ``to_dict`` bridge the two.
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


# ----- ProjectConfig -----


class ProjectConfigError(ValueError):
    """Raised when ai-hats.yaml fails schema validation."""


# HATS-290 — outer section ordering of `.agent/ai-hats/imports.md`.
# Names match the canonical file/dir prefix the renderer uses to bucket paths.
IMPORTS_SECTION_NAMES: tuple[str, ...] = (
    "priorities",
    "traits",
    "role",
    "rules",
    "user-rules",
    "skills_index",
)

# Named outer-order presets. Authors can pick a preset string or supply a full
# permutation list under `imports_order:` in ai-hats.yaml. `None` (the default)
# means "use the `default` preset" — kept as an explicit alias so omitting the
# field never carries semantic weight beyond "no opinion".
IMPORTS_ORDER_PRESETS: dict[str, list[str]] = {
    # Identity → constraints → references. Today's hardcoded behaviour.
    "default": [
        "priorities",
        "traits",
        "role",
        "rules",
        "user-rules",
        "skills_index",
    ],
    # Role-first: identity-driven. "Who am I → goals → behaviour → constraints".
    "role-first": [
        "role",
        "priorities",
        "traits",
        "rules",
        "user-rules",
        "skills_index",
    ],
    # Constraints-first: safety-driven. Loads rules even if context truncates.
    "constraints-first": [
        "rules",
        "user-rules",
        "priorities",
        "role",
        "traits",
        "skills_index",
    ],
    # Anthropic-style: persona → behaviour → goals → constraints → tools.
    "anthropic": [
        "role",
        "traits",
        "priorities",
        "rules",
        "user-rules",
        "skills_index",
    ],
}


class ProjectConfig(_YamlModel):
    """ai-hats.yaml — unified project configuration.

    Sections:
      - Project: provider, library_paths, ai_hats_dir
      - Role: active_role, default_role, customizations
      - Feedback: session_retro
      - Composition: imports_order
      - Meta: schema_version (4 = current)
    """

    # Reject unknown keys so typos in ai-hats.yaml fail loudly instead of silently dropping.
    model_config = ConfigDict(extra="forbid")

    provider: str = "gemini"
    default_role: str = ""
    active_role: str = ""
    schema_version: int = 4
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
    # HATS-290: outer section order of imports.md.
    # None → use the "default" preset. str → preset name. list[str] → custom
    # permutation of IMPORTS_SECTION_NAMES.
    imports_order: str | list[str] | None = None

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

    @field_validator("imports_order")
    @classmethod
    def _validate_imports_order(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, str):
            if value not in IMPORTS_ORDER_PRESETS:
                allowed = ", ".join(sorted(IMPORTS_ORDER_PRESETS))
                raise ValueError(
                    f"unknown imports_order preset {value!r} — allowed: {allowed}"
                )
            return value
        if isinstance(value, list):
            if not all(isinstance(item, str) for item in value):
                raise ValueError("imports_order list entries must be strings")
            allowed_set = set(IMPORTS_SECTION_NAMES)
            seen: set[str] = set()
            for item in value:
                if item not in allowed_set:
                    raise ValueError(
                        f"unknown imports_order section {item!r} — "
                        f"allowed: {', '.join(IMPORTS_SECTION_NAMES)}"
                    )
                if item in seen:
                    raise ValueError(f"duplicate imports_order section {item!r}")
                seen.add(item)
            missing = allowed_set - seen
            if missing:
                raise ValueError(
                    f"imports_order list missing sections: "
                    f"{', '.join(sorted(missing))}"
                )
            return value
        raise ValueError(
            "imports_order must be null, a preset name, or a list of section names"
        )

    @classmethod
    def from_yaml(cls, path: Path) -> ProjectConfig:
        if not path.exists():
            return cls()
        data = yaml.safe_load(path.read_text()) or {}
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
        try:
            return cls.model_validate(data)
        except ValidationError as e:
            raise ProjectConfigError(_format_project_config_error(path, e)) from e

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "schema_version": 4,
            "provider": self.provider,
            # HATS-316: ai_hats_dir is unconditionally serialized so users
            # see the configurable path in their ai-hats.yaml.
            "ai_hats_dir": self.ai_hats_dir,
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
        if self.imports_order is not None:
            d["imports_order"] = self.imports_order
        return d

    def save(self, path: Path) -> None:
        with open(path, "w") as f:
            yaml.dump(self.to_dict(), f, default_flow_style=False, allow_unicode=True)

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
            with open(config_path, "w") as f:
                yaml.dump(raw, f, default_flow_style=False, allow_unicode=True)
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
    with open(yaml_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
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
    with open(yaml_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
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
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            yaml.dump(
                self.to_dict(), f, default_flow_style=False, allow_unicode=True, sort_keys=False
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
