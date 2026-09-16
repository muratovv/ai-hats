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
from typing import TYPE_CHECKING, Mapping

from ai_hats_core.layout import ProjectLayout

from ai_hats.materialization import describe_mkdir
from ai_hats.surfaces import Surface
from ai_hats.surfaces.mcp import StdioMCPServer
from ai_hats.session_artifacts import (
    AutomateLaunch,
    BuiltArtifacts,
    RunMode,
    SessionPolicy,
    working_directory_section,
)

from ..hook_channel import HookEvent, HookRow
from ..mirror import path_dirs
from ..plan import CompositionPlan, Host, Launch, Launched, LaunchFlags, MaterializationPlan, Prompt
from ..skill_index import skill_index_block
from .home import (
    AI_HATS_HOME_DIR,
    ENV_CODEX_BASE_HOME,
    ENV_CODEX_HOME,
    ENV_CODEX_SQLITE_HOME,
    SESSION_HOMES_DIR,
    SQLITE_ARTIFACT_SUFFIXES,
    CodexHome,
    configured_base_home,
    configured_sqlite_home,
    plan_session_home,
    probe_home,
    session_home_env,
    session_home_of,
    validate_sqlite_home,
)
from .session_home import (
    SESSION_HOME_MANIFEST,
    SessionHomeMetadata,
    normalize_session_rollout_paths,
    read_session_home_metadata,
    render_session_home_metadata,
)
from .session_auth import reconcile_auth, stage_auth

if TYPE_CHECKING:
    from ai_hats_core import CompositionResult

    from ai_hats.session_run import SessionRun
    from ai_hats.surfaces import SurfaceHint


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
_ENV_CODEX_BASE_HOME = ENV_CODEX_BASE_HOME
_ENV_CODEX_HOME = ENV_CODEX_HOME
_ENV_CODEX_SQLITE_HOME = ENV_CODEX_SQLITE_HOME
_AI_HATS_HOME_DIR = AI_HATS_HOME_DIR
_SESSION_HOMES_DIR = SESSION_HOMES_DIR
_SQLITE_ARTIFACT_SUFFIXES = SQLITE_ARTIFACT_SUFFIXES

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


class CodexSurface(Surface):
    """The ``codex`` entry-point surface, isolated to one ai-hats session."""

    @property
    def name(self) -> str:
        return "codex"

    def detected_home_dirs(self) -> list[str]:
        return [".codex"]

    def supports_session_command_wrappers(self) -> bool:
        return True

    def surface_hints(self) -> list["SurfaceHint"]:
        from ai_hats.surfaces import SurfaceHint

        return [
            SurfaceHint(
                name="--profile",
                values="<name>",
                description="Use a profile from the user's existing Codex config.",
            ),
            SurfaceHint(
                name="--model",
                values="<model>",
                description="Override the Codex model for this session.",
            ),
        ]

    def settings_lint_warnings(self, layout: ProjectLayout) -> list[str]:
        """Probe CLI/auth readiness without reading or changing Codex config."""
        del layout
        return _readiness_warnings()

    def system_prompt_path(self, layout: ProjectLayout) -> Path | None:
        """Codex receives ai-hats context inline; no project file is managed."""
        del layout
        return None

    def rules_dir(self, session_dir: Path) -> Path:
        return session_dir / "rules"

    def build_system_prompt(self, result: "CompositionResult") -> str:
        # The session-aware skill index is appended by _expanded_prompt, where
        # exact paths in this session's cache are available.
        return self._compose_sections(result)

    def session_skills_root(self, layout: ProjectLayout, session_id: str) -> Path:
        return self.session_codex_home(layout, session_id) / "skills"

    def session_codex_home(self, layout: ProjectLayout, session_id: str) -> Path:
        return session_home_of(self._configured_base_home(), layout.cache.root.name, session_id)

    @staticmethod
    def _configured_base_home() -> Path:
        return configured_base_home(os.environ)

    def _base_codex_home(self, session_home: Path) -> Path:
        base_home = self._configured_base_home()
        resolved_session_home = session_home.resolve(strict=False)
        managed_root = base_home / _AI_HATS_HOME_DIR / _SESSION_HOMES_DIR
        if managed_root not in resolved_session_home.parents:
            raise RuntimeError("Codex session home must be inside the managed durable root")
        return base_home

    @staticmethod
    def _validate_sqlite_home(sqlite_home: Path, base_home: Path) -> Path:
        return validate_sqlite_home(sqlite_home, base_home, os.environ)

    @staticmethod
    def _configured_sqlite_home(base_home: Path) -> Path:
        return configured_sqlite_home(os.environ, base_home)

    # -- the plan (ADR-0036 D2): entries, env and launch from the composition half --

    def probe_home(self, environ: Mapping[str, str]) -> CodexHome:
        return probe_home(environ)

    def plan(
        self,
        composition: CompositionPlan,
        *,
        run_mode: RunMode,
        policy: SessionPolicy,
        root: Path,
        layout: ProjectLayout,
        host: Host,
    ) -> MaterializationPlan:
        from .runtime_hooks import build_hook_cli_args, plan_hooks

        home = host.home
        if not isinstance(home, CodexHome):
            raise RuntimeError(
                "codex plans from its probed home: probe_host(surface=<codex>) before planning"
            )
        mode = RunMode(run_mode)
        session_home = session_home_of(home.base_home, layout.cache.root.name, root.name)
        skills_root = session_home / "skills"
        prompt = composition.prompt
        if index := skill_index_block(composition, skills_root, surface=self.name):
            prompt = Prompt((*prompt.blocks, index))
        entries = [describe_mkdir(root)]
        args: list[str] = []
        env: dict[str, str] = {}
        if policy.context:
            args += ["-c", self._developer_override(prompt.text)]
        # No skills, no session home: the child runs against the person's own
        # home, as it always has.
        if composition.skills:
            entries += plan_session_home(
                composition, home, session_home, project_key=layout.cache.root.name
            )
            if dirs := path_dirs(composition, skills_root):
                env["PATH"] = os.pathsep.join([*(str(d) for d in dirs), host.path])
            env.update(session_home_env(home, session_home))
        if policy.hooks and composition.hooks.runtime:
            manifest, hook_env = plan_hooks(
                composition, root, host, layout=layout, skills_root=skills_root
            )
            entries.append(manifest)
            env.update(hook_env)
            args += build_hook_cli_args()
        if policy.settings:
            approval = "on-request" if mode is RunMode.HITL else "never"
            args += ["--sandbox", "workspace-write", "--ask-for-approval", approval]
        env.update(self.get_env(root, layout))
        return MaterializationPlan(
            composition=composition,
            prompt=prompt,
            surface=self.name,
            run_mode=mode,
            policy=policy,
            root=root,
            entries=tuple(entries),
            env=env,
            launch=Launch(args=tuple(args), sdk_options=None),
        )

    def automate_launch(
        self,
        plan: MaterializationPlan,
        flags: LaunchFlags,
        env: Mapping[str, str],
        *,
        layout: ProjectLayout,
    ) -> Launched:
        """The role rides ``developer_instructions``; the prompt token carries
        only the working directory and the brief."""
        prompt = "\n\n".join(s for s in (working_directory_section(layout), flags.brief or "") if s)
        model = self.model_flags(flags.model) if flags.model else []
        cmd = self.get_cli_command() + list(plan.launch.args or ()) + model
        return Launched(
            args=tuple(self.get_run_command(cmd, prompt)), sdk_options=None, env=env, prompt=prompt
        )

    def claim_resources(
        self,
        plan: MaterializationPlan,
        flags: LaunchFlags,
        *,
        layout: ProjectLayout,
        run: SessionRun,
    ) -> None:
        """Stale homes of earlier sessions are reconciled on the way in; this
        session's home is reconciled and removed when the run closes."""
        try:
            for warning in self._recover_session_homes(layout, flags.session_id):
                run.warn(warning)
        except Exception as exc:
            logger.warning("Codex session-home recovery failed", exc_info=True)
            run.warn(f"Codex session-home recovery failed: {type(exc).__name__}: {exc}")
        if _ENV_CODEX_HOME not in plan.env:
            return
        session_home = Path(plan.env[_ENV_CODEX_HOME])
        base_home = Path(plan.env[_ENV_CODEX_BASE_HOME])
        sqlite_home = Path(plan.env[_ENV_CODEX_SQLITE_HOME])

        def finalize() -> None:
            warning = self._finalize_session_home(session_home, base_home, sqlite_home)
            if warning:
                run.warn(warning)

        run.defer("Codex session home", finalize)

    @staticmethod
    def _project_base_home(base_home: Path, session_home: Path, artifacts) -> None:
        try:
            for source in sorted(base_home.iterdir(), key=lambda path: path.name):
                if source.name not in {
                    "skills",
                    "auth.json",
                    _AI_HATS_HOME_DIR,
                } and not source.name.endswith(_SQLITE_ARTIFACT_SUFFIXES):
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

    def _skill_index(self, layout: ProjectLayout, result, session_id: str) -> str:
        if not result.skills:
            return ""
        skills_root = self.session_skills_root(layout, session_id)
        lines = [
            "## AVAILABLE SKILLS",
            "Use a skill when its description matches the task. Before using it, read the exact "
            "SKILL.md path below; resolve its relative references from that skill directory.",
        ]
        for skill in result.skills:
            skill_md = skills_root / skill.name / "SKILL.md"
            lines.append(f"- **{skill.name}** — {self._skill_description(skill)} (`{skill_md}`)")
        return "\n".join(lines)

    def _expanded_prompt(self, layout: ProjectLayout, result, session_id: str) -> str:
        project_dir = layout.root
        from ai_hats.placeholders import expand_path_placeholders
        from ai_hats.role_catalog import expand_role_catalog

        prompt = self.build_system_prompt(result)
        index = self._skill_index(layout, result, session_id)
        if index:
            prompt = f"{prompt}\n\n{index}" if prompt else index
        prompt = expand_path_placeholders(prompt, layout)
        return expand_role_catalog(prompt, project_dir)

    @staticmethod
    def _developer_override(prompt: str) -> str:
        # A JSON string literal is also a TOML basic string literal. Keep the
        # whole key=value expression as one argv token for `codex -c`.
        return f"developer_instructions={json.dumps(prompt, ensure_ascii=False)}"

    def _build_context_hitl(self, layout, result, session_id, artifacts) -> None:
        prompt = self._expanded_prompt(layout, result, session_id)
        artifacts.full_content = prompt
        artifacts.cli_args.extend(["-c", self._developer_override(prompt)])

    def _build_context_automate(self, layout, result, session_id, artifacts) -> None:
        prompt = self._expanded_prompt(layout, result, session_id)
        artifacts.full_content = prompt
        artifacts.cli_args.extend(["-c", self._developer_override(prompt)])

    def _deliver_skills(self, layout, result, session_id, artifacts) -> None:
        from ai_hats.skills_dir import inject_skill_paths_to_env, materialize_skills_dir

        if not result.skills:
            return
        cache_dir = layout.cache.session(session_id)
        session_home = self.session_codex_home(layout, session_id)
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
        skills_root = self.session_skills_root(layout, session_id)
        materialize_skills_dir(skills_root, result.skills, layout, artifacts.port)
        artifacts.port.mkdir(base_home / "sessions")
        self._project_base_home(base_home, session_home, artifacts)
        stage_auth(base_home, session_home, artifacts.port)
        self._project_base_skills(
            base_home,
            skills_root,
            {skill.name for skill in result.skills},
            artifacts,
        )
        inject_skill_paths_to_env(artifacts.extra_env, result.skills, skills_root)
        artifacts.extra_env.update(
            {
                _ENV_CODEX_HOME: str(session_home),
                _ENV_CODEX_SQLITE_HOME: str(sqlite_home),
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
        warning = reconcile_auth(base_home, session_home)
        if warning:
            return warning
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

    def _recover_session_homes(self, layout: ProjectLayout, session_id: str) -> list[str]:

        base_home = self._configured_base_home()
        project_key_value = layout.cache.root.name
        project_root = base_home / _AI_HATS_HOME_DIR / _SESSION_HOMES_DIR / project_key_value
        if not project_root.is_dir():
            return []

        warnings: list[str] = []
        for session_home in sorted(project_root.iterdir(), key=lambda path: path.name):
            stale_session_id = session_home.name
            if stale_session_id == session_id or layout.cache.session(stale_session_id).exists():
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
        layout: ProjectLayout,
        result: "CompositionResult",
        session_id: str,
        *,
        run_mode: RunMode | str = RunMode.HITL,
        policy: SessionPolicy | None = None,
        artifacts: BuiltArtifacts,
    ) -> BuiltArtifacts:
        if artifacts.resources is not None:
            try:
                artifacts.notices.extend(self._recover_session_homes(layout, session_id))
            except Exception as exc:
                logger.warning("Codex session-home recovery failed", exc_info=True)
                artifacts.notices.append(
                    f"Codex session-home recovery failed: {type(exc).__name__}: {exc}"
                )
        return super().build_session_artifacts(
            layout,
            result,
            session_id,
            run_mode=run_mode,
            policy=policy,
            artifacts=artifacts,
        )

    def _build_skills_hitl(self, layout, result, session_id, artifacts) -> None:
        self._deliver_skills(layout, result, session_id, artifacts)

    def _build_skills_automate(self, layout, result, session_id, artifacts) -> None:
        self._deliver_skills(layout, result, session_id, artifacts)

    def _deliver_hooks(self, layout, result, session_id, artifacts) -> None:
        from ai_hats.hook_collection import collect_runtime_hooks

        from .runtime_hooks import build_hook_cli_args, materialize_hook_manifest

        # Hookless roles should not be asked to trust an inert dispatcher.
        if not collect_runtime_hooks(result):
            return
        materialize_hook_manifest(
            layout,
            result,
            session_id,
            artifacts,
            skills_dir=self.session_skills_root(layout, session_id),
        )
        artifacts.cli_args.extend(build_hook_cli_args())

    def _build_hooks_hitl(self, layout, result, session_id, artifacts) -> None:
        self._deliver_hooks(layout, result, session_id, artifacts)

    def mcp_form_cli_args(self, server: StdioMCPServer) -> list[str]:
        settings = {
            "command": server.command,
            "args": server.args,
            "cwd": str(server.cwd),
            "env_vars": server.env_vars,
            "startup_timeout_sec": server.startup_timeout_s,
            "tool_timeout_sec": server.tool_timeout_s,
            "required": True,
        }
        return [
            arg
            for key, value in settings.items()
            for arg in ("-c", f"mcp_servers.{server.name}.{key}={json.dumps(value)}")
        ]

    def command_guard_rows(self, environ: Mapping[str, str]) -> list[HookRow]:
        from .hook_dispatcher import _load_manifest, _rows

        return _rows(_load_manifest(environ), HookEvent.PRE_TOOL_USE)

    def _build_hooks_automate(self, layout, result, session_id, artifacts) -> None:
        self._deliver_hooks(layout, result, session_id, artifacts)

    def _build_settings_hitl(self, layout, result, session_id, artifacts) -> None:
        del layout, result, session_id
        artifacts.cli_args.extend(
            ["--sandbox", "workspace-write", "--ask-for-approval", "on-request"]
        )

    def _build_settings_automate(self, layout, result, session_id, artifacts) -> None:
        del layout, result, session_id
        artifacts.cli_args.extend(["--sandbox", "workspace-write", "--ask-for-approval", "never"])

    def build_session_prompt(
        self,
        layout: ProjectLayout,
        result: "CompositionResult",
        session_id: str,
    ) -> tuple[list[str], dict[str, str], str]:
        artifacts = self.build_session_artifacts(
            layout,
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
        layout: ProjectLayout,
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
            layout,
            role_context="",
            task=task,
            ticket_id=ticket_id,
        )
        model_args = self.model_flags(model) if model else []
        command = self.get_cli_command() + artifacts.cli_args + model_args
        return AutomateLaunch(launch=self.get_run_command(command, prompt), prompt=prompt)

    def get_env(self, session_dir: Path, layout: ProjectLayout) -> dict[str, str]:
        project_dir = layout.root
        del session_dir
        from ai_hats.paths import AI_HATS_PROJECT_DIR_ENV, ENV_AI_HATS_DIR

        return {
            ENV_AI_HATS_DIR: str(layout.base),
            AI_HATS_PROJECT_DIR_ENV: str(project_dir),
        }
