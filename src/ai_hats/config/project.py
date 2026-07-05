"""ai-hats.yaml schema (``ProjectConfig``) — load, heal, round-trip."""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml
from pydantic import (
    ConfigDict,
    Field,
    PrivateAttr,
    ValidationError,
    field_validator,
    model_validator,
)

from ai_hats_core import YamlModel as _YamlModel
from ai_hats_core import atomic_write_text, file_lock

from ..constants import PROVIDER_GEMINI
from .harness import FeedbackConfig, HarnessConfig
from .migrations import _migrate_v1_to_v2, _migrate_v2_to_v3, _migrate_v3_to_v4
from .overlay import OverlayConfig

logger = logging.getLogger(__name__)


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

    provider: str = PROVIDER_GEMINI
    default_role: str = ""
    active_role: str = ""
    schema_version: int = 4
    migration_step: int = 0  # HATS-471: one-shot-migration counter, orthogonal to schema_version
    ai_hats_dir: str = ".agent/ai-hats"  # HATS-316: default is bootstrap-only; v4 yaml must carry it
    venv_path: str | None = None  # HATS-334: user-owned venv override; None → <ai_hats_dir>/.venv
    library_paths: list[str] = Field(default_factory=list)
    customizations: dict[str, OverlayConfig] = Field(default_factory=dict)
    feedback: FeedbackConfig = Field(default_factory=FeedbackConfig)
    manage_gitignore: bool = True
    task_prefix: str = "TASK"
    harness: HarnessConfig = Field(default_factory=HarnessConfig)  # HATS-764: self-update channel

    # HATS-792: unknown top-level keys preserved for round-trip. PrivateAttr, not
    # a field: extra="forbid" pre-strips them in from_yaml before validation.
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

    # ``provider`` is deliberately NOT schema-validated (HATS-863): the known
    # set lives in the providers registry, and schema→providers is the severed
    # back-edge. Write paths and the assembler load validate via
    # Assembler._validate_provider.

    @field_validator("ai_hats_dir")
    @classmethod
    def _validate_ai_hats_dir(cls, value: str) -> str:
        from ..paths import normalize_ai_hats_dir

        return normalize_ai_hats_dir(value)

    @field_validator("venv_path")
    @classmethod
    def _validate_venv_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        from ..paths import normalize_venv_path

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

        from ..paths import tasks_dir as _tasks_dir

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


def locked_update(path: Path, apply: Callable[[ProjectConfig], None]) -> ProjectConfig:
    """Serialized read-modify-write of an ai-hats.yaml (HATS-526).

    Re-reads the on-disk state under a cross-process ``file_lock``, applies the
    caller's delta, saves. Put ONLY your own field changes in ``apply`` —
    concurrent writers' fields survive via the fresh read.
    """
    with file_lock(path):
        cfg = ProjectConfig.from_yaml(path) if path.exists() else ProjectConfig()
        apply(cfg)
        cfg.save(path)
    return cfg


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
