"""Core data models for ai-hats components."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar

import yaml

logger = logging.getLogger(__name__)


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

    @staticmethod
    def valid_transitions() -> dict[TaskState, list[TaskState]]:
        return {
            TaskState.BRAINSTORM: [TaskState.PLAN, TaskState.BLOCKED],
            TaskState.PLAN: [TaskState.EXECUTE, TaskState.BLOCKED],
            TaskState.EXECUTE: [TaskState.DOCUMENT, TaskState.BLOCKED, TaskState.FAILED],
            TaskState.DOCUMENT: [TaskState.REVIEW, TaskState.BLOCKED],
            TaskState.REVIEW: [TaskState.DONE, TaskState.FAILED],
            TaskState.BLOCKED: [TaskState.BRAINSTORM, TaskState.PLAN, TaskState.EXECUTE, TaskState.DOCUMENT],
            TaskState.FAILED: [TaskState.BRAINSTORM],
            TaskState.DONE: [],
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


@dataclass
class MCPServerConfig:
    name: str
    config: str  # path to config file


@dataclass
class HooksConfig:
    session_start: list[str] = field(default_factory=list)
    session_end: list[str] = field(default_factory=list)
    task_start: list[str] = field(default_factory=list)
    task_complete: list[str] = field(default_factory=list)
    task_failed: list[str] = field(default_factory=list)
    error: list[str] = field(default_factory=list)

    def get_scripts(self, event: LifecycleEvent) -> list[str]:
        return getattr(self, event.value, [])

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> HooksConfig:
        if not data:
            return cls()
        return cls(
            session_start=data.get("session_start", []),
            session_end=data.get("session_end", []),
            task_start=data.get("task_start", []),
            task_complete=data.get("task_complete", []),
            task_failed=data.get("task_failed", []),
            error=data.get("error", []),
        )


@dataclass
class Composition:
    traits: list[str] = field(default_factory=list)
    rules: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    hooks: HooksConfig = field(default_factory=HooksConfig)
    mcp: list[MCPServerConfig] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Composition:
        if not data:
            return cls()
        mcp_list = []
        for m in data.get("mcp", []):
            if isinstance(m, dict):
                mcp_list.append(MCPServerConfig(name=m["name"], config=m.get("config", "")))
        return cls(
            traits=data.get("traits", []),
            rules=data.get("rules", []),
            skills=data.get("skills", []),
            hooks=HooksConfig.from_dict(data.get("hooks")),
            mcp=mcp_list,
        )


@dataclass
class ComponentConfig:
    """Parsed config.yaml for a trait or role."""

    name: str
    composition: Composition = field(default_factory=Composition)
    injection: str = ""
    priorities: list[str] = field(default_factory=list)
    source_path: Path | None = None

    @classmethod
    def from_yaml(cls, path: Path) -> ComponentConfig:
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return cls(
            name=data.get("name", path.parent.name),
            composition=Composition.from_dict(data.get("composition")),
            injection=data.get("injection", ""),
            priorities=data.get("priorities", []),
            source_path=path,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any], source_path: Path | None = None) -> ComponentConfig:
        return cls(
            name=data.get("name", ""),
            composition=Composition.from_dict(data.get("composition")),
            injection=data.get("injection", ""),
            priorities=data.get("priorities", []),
            source_path=source_path,
        )


@dataclass
class RuleMetadata:
    name: str = ""
    description: str = ""
    author: str = ""
    tags: list[str] = field(default_factory=list)

    @classmethod
    def from_yaml(cls, path: Path) -> RuleMetadata:
        if not path.exists():
            return cls()
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            author=data.get("author", ""),
            tags=data.get("tags", []),
        )


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


@dataclass
class SkillMetadata:
    """Parsed metadata.yaml for a skill.

    `git_hooks` lets a skill declare scripts that should be installed into
    the project's `.githooks/<event>.d/` during composition. Keys are git
    hook event names (see GIT_HOOK_EVENTS); values are lists of script
    paths relative to the skill directory.
    """

    name: str = ""
    description: str = ""
    author: str = ""
    tags: list[str] = field(default_factory=list)
    pattern: str = ""
    git_hooks: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def from_yaml(cls, path: Path) -> SkillMetadata:
        if not path.exists():
            return cls()
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        raw_hooks = data.get("git_hooks") or {}
        # Normalize: accept either "pre-commit" or "pre_commit" in yaml.
        git_hooks: dict[str, list[str]] = {}
        if isinstance(raw_hooks, dict):
            for ev, scripts in raw_hooks.items():
                if not isinstance(scripts, list):
                    continue
                normalized = str(ev).replace("_", "-")
                if normalized not in GIT_HOOK_EVENTS:
                    # Unknown event — silently skip; surfaces upstream via tests.
                    continue
                git_hooks[normalized] = [str(s) for s in scripts]
        return cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            author=data.get("author", ""),
            tags=data.get("tags", []),
            pattern=data.get("pattern", ""),
            git_hooks=git_hooks,
        )


@dataclass
class OverlayConfig:
    """Per-role customization overlay (add/remove components)."""

    add_traits: list[str] = field(default_factory=list)
    add_rules: list[str] = field(default_factory=list)
    add_skills: list[str] = field(default_factory=list)
    remove_traits: list[str] = field(default_factory=list)
    remove_rules: list[str] = field(default_factory=list)
    remove_skills: list[str] = field(default_factory=list)
    injection_append: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> OverlayConfig:
        if not data:
            return cls()
        add = data.get("add", {})
        remove = data.get("remove", {})
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
        add: dict[str, list[str]] = {}
        if self.add_traits:
            add["traits"] = self.add_traits
        if self.add_rules:
            add["rules"] = self.add_rules
        if self.add_skills:
            add["skills"] = self.add_skills
        if add:
            d["add"] = add
        remove: dict[str, list[str]] = {}
        if self.remove_traits:
            remove["traits"] = self.remove_traits
        if self.remove_rules:
            remove["rules"] = self.remove_rules
        if self.remove_skills:
            remove["skills"] = self.remove_skills
        if remove:
            d["remove"] = remove
        if self.injection_append:
            d["injection_append"] = self.injection_append
        return d

    @property
    def is_empty(self) -> bool:
        return not any([
            self.add_traits, self.add_rules, self.add_skills,
            self.remove_traits, self.remove_rules, self.remove_skills,
            self.injection_append,
        ])


class FeedbackPolicy(str, Enum):
    OFF = "off"
    ALWAYS = "always"
    SMART = "smart"
    HINT = "hint"


class JudgePolicy(str, Enum):
    OFF = "off"
    MANUAL = "manual"


@dataclass
class SmartThreshold:
    min_turns: int = 5
    min_tool_calls: int = 10

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> SmartThreshold:
        if not data:
            return cls()
        return cls(
            min_turns=data.get("min_turns", 5),
            min_tool_calls=data.get("min_tool_calls", 10),
        )

    def to_dict(self) -> dict[str, int]:
        return {"min_turns": self.min_turns, "min_tool_calls": self.min_tool_calls}


@dataclass
class SessionRetroConfig:
    policy: FeedbackPolicy = FeedbackPolicy.SMART
    smart_threshold: SmartThreshold = field(default_factory=SmartThreshold)
    background: bool = True
    mode: str = "programmatic"

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> SessionRetroConfig:
        if not data:
            return cls()
        return cls(
            policy=FeedbackPolicy(data.get("policy", "smart")),
            smart_threshold=SmartThreshold.from_dict(data.get("smart_threshold")),
            background=data.get("background", True),
            mode=data.get("mode", "programmatic"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy": self.policy.value,
            "smart_threshold": self.smart_threshold.to_dict(),
            "background": self.background,
            "mode": self.mode,
        }


@dataclass
class JudgeConfig:
    policy: JudgePolicy = JudgePolicy.MANUAL

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> JudgeConfig:
        if not data:
            return cls()
        return cls(policy=JudgePolicy(data.get("policy", "manual")))

    def to_dict(self) -> dict[str, str]:
        return {"policy": self.policy.value}


@dataclass
class FeedbackConfig:
    session_retro: SessionRetroConfig = field(default_factory=SessionRetroConfig)
    judge: JudgeConfig = field(default_factory=JudgeConfig)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> FeedbackConfig:
        if not data:
            return cls()
        return cls(
            session_retro=SessionRetroConfig.from_dict(data.get("session_retro")),
            judge=JudgeConfig.from_dict(data.get("judge")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_retro": self.session_retro.to_dict(),
            "judge": self.judge.to_dict(),
        }

    @property
    def is_default(self) -> bool:
        return self == FeedbackConfig()


@dataclass
class ProjectConfig:
    """ai-hats.yaml — unified project configuration.

    Sections:
      - Project: provider, library_paths
      - Role: active_role, default_role, customizations
      - Feedback: session_retro, judge
      - Meta: schema_version (2 = current)
    """

    provider: str = "gemini"
    default_role: str = ""
    active_role: str = ""
    schema_version: int = 2
    library_paths: list[str] = field(default_factory=list)
    customizations: dict[str, OverlayConfig] = field(default_factory=dict)
    feedback: FeedbackConfig = field(default_factory=FeedbackConfig)
    manage_gitignore: bool = True

    @classmethod
    def from_yaml(cls, path: Path) -> ProjectConfig:
        if not path.exists():
            return cls()
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        if data.get("schema_version", 1) < 2:
            data = _migrate_v1_to_v2(path, data)
        customizations: dict[str, OverlayConfig] = {}
        for role_name, overlay_data in data.get("customizations", {}).items():
            customizations[role_name] = OverlayConfig.from_dict(overlay_data)
        return cls(
            provider=data.get("provider", "gemini"),
            default_role=data.get("default_role", ""),
            active_role=data.get("active_role", ""),
            schema_version=data.get("schema_version", 2),
            library_paths=data.get("library_paths", []),
            customizations=customizations,
            feedback=FeedbackConfig.from_dict(data.get("feedback")),
            manage_gitignore=data.get("manage_gitignore", True),
        )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "schema_version": 2,
            "provider": self.provider,
            "library_paths": self.library_paths,
            "active_role": self.active_role,
            "default_role": self.default_role,
        }
        if self.customizations:
            d["customizations"] = {
                name: overlay.to_dict()
                for name, overlay in self.customizations.items()
                if not overlay.is_empty
            }
        if not self.feedback.is_default:
            d["feedback"] = self.feedback.to_dict()
        if not self.manage_gitignore:
            d["manage_gitignore"] = False
        return d

    def save(self, path: Path) -> None:
        with open(path, "w") as f:
            yaml.dump(self.to_dict(), f, default_flow_style=False, allow_unicode=True)


def _migrate_v1_to_v2(yaml_path: Path, data: dict[str, Any]) -> dict[str, Any]:
    """Auto-migrate schema v1 → v2: merge profile.json into ai-hats.yaml.

    Runs once when a v1 ai-hats.yaml is loaded. Merges active_role, provider,
    and feedback from adjacent profile.json (if present), writes the unified
    YAML, and renames profile.json to profile.json.bak.
    """
    import json

    profile_path = yaml_path.parent / "profile.json"
    if profile_path.exists():
        try:
            with open(profile_path) as f:
                profile = json.load(f)
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


@dataclass
class ProfileConfig:
    """Deprecated: shim that reads/writes through ai-hats.yaml.

    Use ProjectConfig directly instead. This exists for backward compat
    during the transition period.
    """

    active_role: str = ""
    provider: str = ""
    feedback: FeedbackConfig = field(default_factory=FeedbackConfig)

    @classmethod
    def load(cls, path: Path) -> ProfileConfig:
        # Try ai-hats.yaml first (unified config)
        yaml_path = path.parent / "ai-hats.yaml"
        if yaml_path.exists():
            cfg = ProjectConfig.from_yaml(yaml_path)
            return cls(
                active_role=cfg.active_role,
                provider=cfg.provider,
                feedback=cfg.feedback,
            )
        # Fallback: read legacy profile.json (tests, external tools)
        if not path.exists():
            return cls()
        import json

        with open(path) as f:
            data = json.load(f)
        return cls(
            active_role=data.get("active_role", ""),
            provider=data.get("provider", ""),
            feedback=FeedbackConfig.from_dict(data.get("feedback")),
        )

    def save(self, path: Path) -> None:
        # Write through to ai-hats.yaml
        yaml_path = path.parent / "ai-hats.yaml"
        if yaml_path.exists():
            cfg = ProjectConfig.from_yaml(yaml_path)
            cfg.active_role = self.active_role
            cfg.provider = self.provider
            cfg.feedback = self.feedback
            cfg.save(yaml_path)
            return
        # Fallback: write legacy JSON
        import json

        out: dict[str, Any] = {
            "active_role": self.active_role,
            "provider": self.provider,
        }
        if not self.feedback.is_default:
            out["feedback"] = self.feedback.to_dict()
        with open(path, "w") as f:
            json.dump(out, f, indent=2)


@dataclass
class WorkLogEntry:
    """Single work log entry with timestamp and session tracking."""

    timestamp: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"timestamp": self.timestamp, "message": self.message}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkLogEntry:
        return cls(timestamp=data.get("timestamp", ""), message=data.get("message", ""))


@dataclass
class TaskCard:
    """YAML task card for state machine.

    Unknown YAML keys are captured into ``extras`` and round-tripped verbatim
    on save. This guards against silent data loss when callers add new fields
    (e.g. ``acceptance_criteria``) that aren't part of the typed schema.
    """

    #: typed fields recognized by from_dict / to_dict; everything else → extras
    _KNOWN_FIELDS: ClassVar[frozenset[str]] = frozenset({
        "id", "title", "state", "description", "priority",
        "assignee", "reviewer", "role", "parent_task", "subtasks",
        "tags", "work_log", "final_state", "resolution",
        "created", "updated", "completed_at",
    })

    id: str
    title: str
    state: TaskState = TaskState.BRAINSTORM
    description: str = ""
    priority: str = "medium"
    assignee: str = ""
    reviewer: str = "user"
    role: str = ""
    parent_task: str = ""
    subtasks: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    work_log: list[WorkLogEntry] = field(default_factory=list)
    final_state: str = ""
    resolution: str = ""
    created: str = ""
    updated: str = ""
    completed_at: str = ""
    extras: dict[str, Any] = field(default_factory=dict)

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
        # Round-trip unknown fields verbatim. Known fields take precedence in
        # case of accidental collision (extras should never contain known keys
        # since from_dict filters them out, but we defend against direct mutation).
        for k, v in self.extras.items():
            if k not in self._KNOWN_FIELDS:
                d[k] = v
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskCard:
        work_log = [
            WorkLogEntry.from_dict(e) if isinstance(e, dict) else WorkLogEntry(timestamp="", message=str(e))
            for e in data.get("work_log", [])
        ]
        extras = {k: v for k, v in data.items() if k not in cls._KNOWN_FIELDS}
        return cls(
            id=data["id"],
            title=data["title"],
            state=TaskState(data.get("state", "brainstorm")),
            description=data.get("description", ""),
            priority=data.get("priority", "medium"),
            assignee=data.get("assignee", ""),
            reviewer=data.get("reviewer", "user"),
            role=data.get("role", ""),
            parent_task=data.get("parent_task", ""),
            subtasks=data.get("subtasks", []),
            tags=data.get("tags", []),
            work_log=work_log,
            final_state=data.get("final_state", ""),
            resolution=data.get("resolution", ""),
            created=data.get("created", ""),
            updated=data.get("updated", ""),
            completed_at=data.get("completed_at", ""),
            extras=extras,
        )

    @classmethod
    def from_yaml(cls, path: Path) -> TaskCard:
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return cls.from_dict(data)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            yaml.dump(self.to_dict(), f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def resolve_namespace(name: str) -> str:
    """Convert namespace notation (dev::python) to filesystem path (dev/python)."""
    return name.replace("::", "/")
