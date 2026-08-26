"""Codex CLI adapter for ai-hats.

Role context is delivered through Codex's per-run ``developer_instructions``
configuration override. Composed skills live in a per-session ``CODEX_HOME``
that projects shared user state without writing ``.agents`` or ``.codex`` into
the project. The developer instructions retain a compact skill-path index as a
fallback.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING

from ai_hats.surfaces import Provider
from ai_hats.session_artifacts import (
    AutomateLaunch,
    BuiltArtifacts,
    RunMode,
    SessionPolicy,
)

from .session_home import (
    SESSION_HOME_MANIFEST,
    SessionHomeMetadata,
    normalize_session_rollout_paths,
    read_session_home_metadata,
    render_session_home_metadata,
)

if TYPE_CHECKING:
    from ai_hats_core import CompositionResult

    from ai_hats.surfaces import ProviderHint


_DANGEROUS_FLAGS = {
    "--dangerously-bypass-approvals-and-sandbox",
    "--dangerously-bypass-hook-trust",
    "--yolo",
}

_CONFIG_FLAGS = ("-c", "--config")
_HOOK_POLICY_KEYS = {
    "allow_managed_hooks_only",
    "bypass_hook_trust",
    "features.codex_hooks",
    "features.hooks",
}
_HOOK_FEATURE_NAMES = {"codex_hooks", "hooks"}
_ENV_CODEX_BASE_HOME = "AI_HATS_CODEX_BASE_HOME"
_AI_HATS_HOME_DIR = ".ai-hats"
_SESSION_HOMES_DIR = "session-homes"
_SQLITE_ARTIFACT_SUFFIXES = (".sqlite", ".sqlite-shm", ".sqlite-wal", ".sqlite-journal")

logger = logging.getLogger(__name__)


def _config_overrides(command: list[str]):
    """Yield Codex config expressions from all documented argv forms."""
    for index, token in enumerate(command):
        if token in _CONFIG_FLAGS and index + 1 < len(command):
            yield command[index + 1]
        elif token.startswith("-c=") or token.startswith("--config="):
            yield token.split("=", 1)[1]
        elif token.startswith("-c") and not token.startswith("--") and len(token) > 2:
            yield token[2:]


def _config_paths(expression: str):
    """Yield normalized TOML paths, retaining containers such as ``hooks={}``."""
    try:
        parsed = tomllib.loads(expression)
    except tomllib.TOMLDecodeError:
        key, separator, value = expression.partition("=")
        if separator:
            yield key.strip().casefold(), value.strip().casefold()
        return

    def walk(mapping: dict, prefix: tuple[str, ...] = ()):
        for key, value in mapping.items():
            path = (*prefix, str(key).casefold())
            yield ".".join(path), value
            if isinstance(value, dict):
                yield from walk(value, path)

    yield from walk(parsed)


def _sets_config_key(expression: str, config_key: str) -> bool:
    return any(path == config_key for path, _value in _config_paths(expression))


def _readiness_warnings(*, which=shutil.which, run=subprocess.run) -> list[str]:
    binary = which("codex")
    if not binary:
        return [
            "Codex CLI is not on PATH. Install it, then run `codex --version` and `codex login`."
        ]
    try:
        version = run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if version.returncode != 0:
            return ["`codex --version` failed; repair the Codex CLI before launch."]
        auth = run(
            [binary, "login", "status"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ["Codex readiness probe failed; run `codex --version` manually."]
    if auth.returncode != 0:
        return ["Codex is not authenticated. Run `codex login`, then retry ai-hats."]
    return []


def _reconcile_policy_default(
    command: list[str], *, flags: tuple[str, ...], config_key: str
) -> list[str]:
    """Drop the provider default when the user supplied this policy explicitly."""
    occurrences: list[tuple[int, int]] = []
    explicit_config = False
    for index, token in enumerate(command):
        if token in flags:
            occurrences.append((index, 2))
        elif any(token.startswith(f"{flag}=") for flag in flags):
            occurrences.append((index, 1))
        elif token in _CONFIG_FLAGS and index + 1 < len(command):
            explicit_config = explicit_config or _sets_config_key(command[index + 1], config_key)
        elif token.startswith("-c=") or token.startswith("--config="):
            explicit_config = explicit_config or _sets_config_key(
                token.split("=", 1)[1], config_key
            )
        elif token.startswith("-c") and not token.startswith("--") and len(token) > 2:
            explicit_config = explicit_config or _sets_config_key(token[2:], config_key)

    # Session settings contribute exactly one occurrence after user args. A
    # second occurrence, or an equivalent `-c` key, means that final default
    # must be removed: clap rejects duplicate flags instead of choosing one.
    if occurrences and (len(occurrences) > 1 or explicit_config):
        index, width = occurrences[-1]
        return command[:index] + command[index + width :]
    return command


class CodexProvider(Provider):
    """The ``codex`` entry-point surface, isolated to one ai-hats session."""

    @property
    def name(self) -> str:
        return "codex"

    def detected_home_dirs(self) -> list[str]:
        return [".codex"]

    def supports_session_command_wrappers(self) -> bool:
        return True

    def provider_hints(self) -> list["ProviderHint"]:
        from ai_hats.surfaces import ProviderHint

        return [
            ProviderHint(
                name="--profile",
                values="<name>",
                description="Use a profile from the user's existing Codex config.",
            ),
            ProviderHint(
                name="--model",
                values="<model>",
                description="Override the Codex model for this session.",
            ),
        ]

    def settings_lint_warnings(self, project_dir: Path) -> list[str]:
        """Probe CLI/auth readiness without reading or changing Codex config."""
        del project_dir
        return _readiness_warnings()

    def system_prompt_path(self, project_dir: Path) -> Path | None:
        """Codex receives ai-hats context inline; no project file is managed."""
        del project_dir
        return None

    def rules_dir(self, session_dir: Path) -> Path:
        return session_dir / "rules"

    def build_system_prompt(self, result: "CompositionResult") -> str:
        # The session-aware skill index is appended by _expanded_prompt, where
        # exact paths in this session's cache are available.
        return self._compose_sections(result)

    def session_skills_root(self, project_dir: Path, session_id: str) -> Path:
        return self.session_codex_home(project_dir, session_id) / "skills"

    def session_codex_home(self, project_dir: Path, session_id: str) -> Path:
        from ai_hats.paths import project_key

        base_home = self._configured_base_home()
        return (
            base_home
            / _AI_HATS_HOME_DIR
            / _SESSION_HOMES_DIR
            / project_key(project_dir)
            / session_id
        )

    @staticmethod
    def _configured_base_home() -> Path:
        from ai_hats.paths import cache_home

        configured = os.environ.get(_ENV_CODEX_BASE_HOME) or os.environ.get("CODEX_HOME")
        candidate = Path(configured).expanduser() if configured else Path.home() / ".codex"
        if not candidate.is_absolute() or not candidate.is_dir():
            raise RuntimeError("Codex base home must be an existing absolute directory")
        base_home = candidate.resolve()
        resolved_cache_home = cache_home().resolve()
        if (
            base_home == resolved_cache_home
            or base_home in resolved_cache_home.parents
            or resolved_cache_home in base_home.parents
        ):
            raise RuntimeError("Codex base home must be disjoint from the ai-hats cache home")
        return base_home

    def _base_codex_home(self, session_home: Path) -> Path:
        base_home = self._configured_base_home()
        resolved_session_home = session_home.resolve(strict=False)
        managed_root = base_home / _AI_HATS_HOME_DIR / _SESSION_HOMES_DIR
        if managed_root not in resolved_session_home.parents:
            raise RuntimeError("Codex session home must be inside the managed durable root")
        return base_home

    @staticmethod
    def _validate_sqlite_home(sqlite_home: Path, base_home: Path) -> Path:
        from ai_hats.paths import cache_home

        resolved_cache_home = cache_home().resolve()
        if sqlite_home == resolved_cache_home or resolved_cache_home in sqlite_home.parents:
            raise RuntimeError("Codex SQLite home must be outside the ai-hats cache home")
        managed_root = base_home / _AI_HATS_HOME_DIR / _SESSION_HOMES_DIR
        if sqlite_home == managed_root or managed_root in sqlite_home.parents:
            raise RuntimeError("Codex SQLite home must be outside managed session homes")
        return sqlite_home

    @classmethod
    def _configured_sqlite_home(cls, base_home: Path) -> Path:
        configured = os.environ.get("CODEX_SQLITE_HOME")
        sqlite_home = Path(configured).expanduser() if configured else base_home
        if not sqlite_home.is_absolute():
            raise RuntimeError("Codex SQLite home must be an absolute directory")
        return cls._validate_sqlite_home(sqlite_home.resolve(strict=False), base_home)

    @staticmethod
    def _project_base_home(base_home: Path, session_home: Path, artifacts) -> None:
        try:
            for source in sorted(base_home.iterdir(), key=lambda path: path.name):
                if source.name not in {"skills", _AI_HATS_HOME_DIR} and not source.name.endswith(
                    _SQLITE_ARTIFACT_SUFFIXES
                ):
                    artifacts.port.symlink(source, session_home / source.name)
        except OSError:
            raise RuntimeError("Codex session home projection failed") from None

    @staticmethod
    def _project_base_skills(base_home: Path, skills_root: Path, role_names, artifacts) -> None:
        base_skills = base_home / "skills"
        if not base_skills.is_dir():
            return
        try:
            for source in sorted(base_skills.iterdir(), key=lambda path: path.name):
                if source.name not in role_names:
                    artifacts.port.symlink(source, skills_root / source.name)
        except OSError:
            raise RuntimeError("Codex base skill projection failed") from None

    @staticmethod
    def _skill_description(skill) -> str:
        from ai_hats.frontmatter import FrontmatterError, read_frontmatter

        try:
            metadata = read_frontmatter(skill.source_path / "SKILL.md")
        except (FrontmatterError, OSError):
            return skill.name
        description = metadata.get("description")
        return description if isinstance(description, str) and description else skill.name

    def _skill_index(self, project_dir: Path, result, session_id: str) -> str:
        if not result.skills:
            return ""
        skills_root = self.session_skills_root(project_dir, session_id)
        lines = [
            "## AVAILABLE SKILLS",
            "Use a skill when its description matches the task. Before using it, read the exact "
            "SKILL.md path below; resolve its relative references from that skill directory.",
        ]
        for skill in result.skills:
            skill_md = skills_root / skill.name / "SKILL.md"
            lines.append(f"- **{skill.name}** — {self._skill_description(skill)} (`{skill_md}`)")
        return "\n".join(lines)

    def _expanded_prompt(self, project_dir: Path, result, session_id: str) -> str:
        from ai_hats.placeholders import expand_path_placeholders
        from ai_hats.role_catalog import expand_role_catalog

        prompt = self.build_system_prompt(result)
        index = self._skill_index(project_dir, result, session_id)
        if index:
            prompt = f"{prompt}\n\n{index}" if prompt else index
        prompt = expand_path_placeholders(prompt, project_dir)
        return expand_role_catalog(prompt, project_dir)

    @staticmethod
    def _developer_override(prompt: str) -> str:
        # A JSON string literal is also a TOML basic string literal. Keep the
        # whole key=value expression as one argv token for `codex -c`.
        return f"developer_instructions={json.dumps(prompt, ensure_ascii=False)}"

    def _build_context_hitl(self, project_dir, result, session_id, artifacts) -> None:
        prompt = self._expanded_prompt(project_dir, result, session_id)
        artifacts.full_content = prompt
        artifacts.cli_args.extend(["-c", self._developer_override(prompt)])

    def _build_context_automate(self, project_dir, result, session_id, artifacts) -> None:
        prompt = self._expanded_prompt(project_dir, result, session_id)
        artifacts.full_content = prompt
        artifacts.cli_args.extend(["-c", self._developer_override(prompt)])

    def _deliver_skills(self, project_dir, result, session_id, artifacts) -> None:
        from ai_hats.paths import session_cache_dir
        from ai_hats.skills_dir import inject_skill_paths_to_env, materialize_skills_dir

        if not result.skills:
            return
        cache_dir = session_cache_dir(project_dir, session_id)
        session_home = self.session_codex_home(project_dir, session_id)
        base_home = self._base_codex_home(session_home)
        sqlite_home = self._configured_sqlite_home(base_home)
        resources = artifacts.resources
        if resources is not None:

            def finalize_session_home() -> None:
                warning = self._finalize_session_home(session_home, base_home, sqlite_home)
                if warning:
                    resources.warn(warning)

            resources.defer("Codex session home", finalize_session_home)
        artifacts.port.mkdir(cache_dir)
        artifacts.port.mkdir(session_home)
        artifacts.port.write_text(
            session_home / SESSION_HOME_MANIFEST,
            render_session_home_metadata(
                SessionHomeMetadata(
                    base_home=base_home,
                    sqlite_home=sqlite_home,
                    project_key=session_home.parent.name,
                    session_id=session_id,
                )
            ),
        )
        skills_root = self.session_skills_root(project_dir, session_id)
        materialize_skills_dir(skills_root, result.skills, project_dir, artifacts.port)
        artifacts.port.mkdir(base_home / "sessions")
        self._project_base_home(base_home, session_home, artifacts)
        self._project_base_skills(
            base_home,
            skills_root,
            {skill.name for skill in result.skills},
            artifacts,
        )
        inject_skill_paths_to_env(artifacts.extra_env, result.skills, skills_root)
        artifacts.extra_env.update(
            {
                "CODEX_HOME": str(session_home),
                "CODEX_SQLITE_HOME": str(sqlite_home),
                _ENV_CODEX_BASE_HOME: str(base_home),
            }
        )
        artifacts.materialized.append(skills_root)

    def _finalize_session_home(
        self,
        session_home: Path,
        base_home: Path,
        sqlite_home: Path,
    ) -> str | None:
        if session_home.is_symlink():
            raise RuntimeError("Refusing to finalize a symlinked Codex session home")
        self._base_codex_home(session_home)
        result = normalize_session_rollout_paths(sqlite_home, session_home, base_home)
        if not result.removable:
            return (
                f"Codex session home {session_home} retained: "
                f"{result.remaining} rollout reference(s) remain"
            )
        if session_home.is_dir():
            shutil.rmtree(session_home)  # safe-delete: ok session-cache
        return None

    def _validated_session_metadata(
        self,
        session_home: Path,
        *,
        base_home: Path,
        project_key_value: str,
        session_id: str,
    ) -> SessionHomeMetadata:
        metadata = read_session_home_metadata(session_home)
        if (
            metadata.base_home.resolve(strict=False) != base_home
            or metadata.project_key != project_key_value
            or metadata.session_id != session_id
        ):
            raise RuntimeError("Codex session-home manifest does not match its path")
        return SessionHomeMetadata(
            base_home=metadata.base_home,
            sqlite_home=self._validate_sqlite_home(
                metadata.sqlite_home.resolve(strict=False), base_home
            ),
            project_key=metadata.project_key,
            session_id=metadata.session_id,
        )

    def _recover_session_homes(self, project_dir: Path, session_id: str) -> list[str]:
        from ai_hats.paths import project_key, session_cache_dir

        base_home = self._configured_base_home()
        project_key_value = project_key(project_dir)
        project_root = base_home / _AI_HATS_HOME_DIR / _SESSION_HOMES_DIR / project_key_value
        if not project_root.is_dir():
            return []

        warnings: list[str] = []
        for session_home in sorted(project_root.iterdir(), key=lambda path: path.name):
            stale_session_id = session_home.name
            if (
                stale_session_id == session_id
                or session_cache_dir(project_dir, stale_session_id).exists()
            ):
                continue
            try:
                if not session_home.is_dir() or session_home.is_symlink():
                    raise RuntimeError("managed Codex session-home entry is not a directory")
                metadata = self._validated_session_metadata(
                    session_home,
                    base_home=base_home,
                    project_key_value=project_key_value,
                    session_id=stale_session_id,
                )
                warning = self._finalize_session_home(
                    session_home,
                    base_home,
                    metadata.sqlite_home,
                )
                if warning:
                    warnings.append(warning)
            except Exception as exc:
                warning = (
                    f"Codex session home {session_home} retained after recovery failure: "
                    f"{type(exc).__name__}: {exc}"
                )
                logger.warning(warning, exc_info=True)
                warnings.append(warning)
        return warnings

    def build_session_artifacts(
        self,
        project_dir: Path,
        result: "CompositionResult",
        session_id: str,
        *,
        run_mode: RunMode | str = RunMode.HITL,
        policy: SessionPolicy | None = None,
        artifacts: BuiltArtifacts,
    ) -> BuiltArtifacts:
        if artifacts.resources is not None:
            try:
                artifacts.notices.extend(self._recover_session_homes(project_dir, session_id))
            except Exception as exc:
                logger.warning("Codex session-home recovery failed", exc_info=True)
                artifacts.notices.append(
                    f"Codex session-home recovery failed: {type(exc).__name__}: {exc}"
                )
        return super().build_session_artifacts(
            project_dir,
            result,
            session_id,
            run_mode=run_mode,
            policy=policy,
            artifacts=artifacts,
        )

    def _build_skills_hitl(self, project_dir, result, session_id, artifacts) -> None:
        self._deliver_skills(project_dir, result, session_id, artifacts)

    def _build_skills_automate(self, project_dir, result, session_id, artifacts) -> None:
        self._deliver_skills(project_dir, result, session_id, artifacts)

    def _deliver_hooks(self, project_dir, result, session_id, artifacts) -> None:
        from ai_hats.hook_collection import collect_runtime_hooks

        from .runtime_hooks import build_hook_cli_args, materialize_hook_manifest

        # Hookless roles should not be asked to trust an inert dispatcher.
        if not collect_runtime_hooks(result):
            return
        materialize_hook_manifest(
            project_dir,
            result,
            session_id,
            artifacts,
            skills_dir=self.session_skills_root(project_dir, session_id),
        )
        artifacts.cli_args.extend(build_hook_cli_args())

    def _build_hooks_hitl(self, project_dir, result, session_id, artifacts) -> None:
        self._deliver_hooks(project_dir, result, session_id, artifacts)

    def _build_hooks_automate(self, project_dir, result, session_id, artifacts) -> None:
        self._deliver_hooks(project_dir, result, session_id, artifacts)

    def _build_settings_hitl(self, project_dir, result, session_id, artifacts) -> None:
        del project_dir, result, session_id
        artifacts.cli_args.extend(
            ["--sandbox", "workspace-write", "--ask-for-approval", "on-request"]
        )

    def _build_settings_automate(self, project_dir, result, session_id, artifacts) -> None:
        del project_dir, result, session_id
        artifacts.cli_args.extend(["--sandbox", "workspace-write", "--ask-for-approval", "never"])

    def build_session_prompt(
        self,
        project_dir: Path,
        result: "CompositionResult",
        session_id: str,
    ) -> tuple[list[str], dict[str, str], str]:
        artifacts = self.build_session_artifacts(
            project_dir,
            result,
            session_id,
            run_mode=RunMode.HITL,
            artifacts=BuiltArtifacts(),
        )
        return artifacts.cli_args, artifacts.extra_env, artifacts.full_content or ""

    @staticmethod
    def _validate_passthrough(args: list[str]) -> None:
        lowered = [arg.lower() for arg in args]
        for index, arg in enumerate(lowered):
            if arg.split("=", 1)[0] in _DANGEROUS_FLAGS or "danger-full-access" in arg:
                raise ValueError(f"unsafe Codex option is not allowed by ai-hats: {arg}")
            if (
                arg == "--disable"
                and index + 1 < len(lowered)
                and lowered[index + 1] in _HOOK_FEATURE_NAMES
            ):
                raise ValueError(
                    f"unsafe Codex option is not allowed by ai-hats: --disable {lowered[index + 1]}"
                )
            if arg.startswith("--disable=") and arg.split("=", 1)[1] in _HOOK_FEATURE_NAMES:
                raise ValueError(f"unsafe Codex option is not allowed by ai-hats: {arg}")

        for expression in _config_overrides(args):
            for key, value in _config_paths(expression):
                if key == "hooks" or key.startswith("hooks.") or key in _HOOK_POLICY_KEYS:
                    raise ValueError(f"unsafe Codex option is not allowed by ai-hats: config {key}")
                if key == "sandbox_mode" and "danger-full-access" in str(value).casefold():
                    raise ValueError(
                        "unsafe Codex option is not allowed by ai-hats: "
                        "sandbox_mode=danger-full-access"
                    )

    def get_cli_command(self, args: list[str] | None = None) -> list[str]:
        extra = list(args or [])
        self._validate_passthrough(extra)
        return ["codex", *extra]

    def get_cli_launch_args(
        self, base_cmd: list[str], session_id: str, is_resume: bool
    ) -> list[str]:
        del session_id, is_resume
        command = _reconcile_policy_default(
            list(base_cmd),
            flags=("--sandbox", "-s"),
            config_key="sandbox_mode",
        )
        return _reconcile_policy_default(
            command,
            flags=("--ask-for-approval", "-a"),
            config_key="approval_policy",
        )

    def get_run_command(self, cmd: list[str], meta_prompt: str) -> list[str]:
        """Build the non-HITL `ai-hats agent` Codex exec command.

        In Codex 0.147 approval policy is parsed globally, so it must precede
        ``exec``. A caller handing this method a HITL-shaped command is safely
        narrowed to non-interactive ``never`` rather than retaining on-request.
        """
        globals_ = list(cmd or ["codex"])
        for index, token in enumerate(globals_[:-1]):
            if token in ("--ask-for-approval", "-a"):
                globals_[index + 1] = "never"
        for index, token in enumerate(globals_):
            if token.startswith("--ask-for-approval="):
                globals_[index] = "--ask-for-approval=never"
        return [*globals_, "exec", "--json", "--ephemeral", meta_prompt]

    def describe_automate_launch(
        self,
        project_dir: Path,
        result: "CompositionResult",
        session_id: str,
        artifacts: BuiltArtifacts,
        *,
        task: str,
        ticket_id: str,
        model: str,
        env: dict[str, str],
    ) -> AutomateLaunch:
        """Keep role in developer instructions, never duplicate it as user text."""
        from ai_hats.session_artifacts import assemble_meta_prompt

        del result, session_id, env
        prompt = assemble_meta_prompt(
            project_dir,
            role_context="",
            task=task,
            ticket_id=ticket_id,
        )
        model_args = self.model_flags(model) if model else []
        command = self.get_cli_command() + artifacts.cli_args + model_args
        return AutomateLaunch(launch=self.get_run_command(command, prompt), prompt=prompt)

    def get_env(self, session_dir: Path, project_dir: Path) -> dict[str, str]:
        del session_dir
        from ai_hats.paths import AI_HATS_PROJECT_DIR_ENV, ENV_AI_HATS_DIR, ai_hats_dir

        return {
            ENV_AI_HATS_DIR: str(ai_hats_dir(project_dir)),
            AI_HATS_PROJECT_DIR_ENV: str(project_dir),
        }
