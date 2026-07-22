"""Cline surface adapter — maps the `cline` CLI to the ai-hats `Provider`."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from ai_hats.providers import Provider

if TYPE_CHECKING:
    # Workspace-boundary Rule 1 (HATS-869): only first-party root is `ai_hats`.
    from ai_hats.providers import CompositionResult
    from ai_hats_observe.parsers.base import TranscriptParser

# Marks ai-hats-owned skill dirs so user-authored ones survive re-materialization.
_MANAGED_MARKER = ".ai-hats-managed"

# Rebuild is sub-second — a lock timeout means a stuck holder.
_LOCK_TIMEOUT = 30.0

_PLUGIN_FILENAME = "ai-hats-hooks.ts"
_INDEX_FILENAME = "ai-hats-hooks.json"

# Guard reads .tool_input.command — the plugin must emit that exact stdin shape.
_GUARD_HOOK_INDEX = [
    {"event": "PreToolUse", "cline_tool": "bash",
     "script": "pre_bash_shared_state_guard.sh"},
]

# Plugin model + lifecycle hooks: https://docs.cline.bot/sdk/plugins
_PLUGIN_TS_PATH = Path(__file__).parent / "templates" / _PLUGIN_FILENAME


class ClineProvider(Provider):
    """`cline` CLI adapter, registered via the `ai_hats.providers` entry point."""

    @property
    def name(self) -> str:
        return "cline"

    def transcript_parser(self) -> TranscriptParser:
        # Lazy import: provider discovery must not eager-load observe parsers.
        from ai_hats_cline.parser import ClineParser

        return ClineParser()

    def system_prompt_path(self, project_dir: Path) -> Path:
        # Vestigial — the role goes inline via -s (never read/written); ABC requires it.
        return project_dir / "CLINE.md"

    def update_system_prompt(self, project_dir: Path, content: str) -> None:
        # Inline-only surface: set_role must not write a CLINE.md cline would ignore.
        del project_dir, content

    def rules_dir(self, session_dir: Path) -> Path:
        return session_dir / "rules"

    def build_system_prompt(self, result: CompositionResult) -> str:
        # Skills reach cline natively via .cline/skills/ — a text index duplicates them.
        return self._compose_sections(result, include_skills=False)

    def get_cli_command(self, args: list[str] | None = None) -> list[str]:
        cmd = ["cline"]
        if args:
            cmd.extend(args)
        return cmd

    def get_run_command(
        self,
        cmd: list[str],
        meta_prompt: str,
    ) -> list[str]:
        # Strip interactive flags so HITL -i never meets --yolo; other passthrough survives.
        kept = [a for a in (cmd or ["cline"]) if a not in ("-i", "--tui")]
        return [*kept, "--yolo", "--json", meta_prompt]

    @staticmethod
    def _plugins_dir(project_dir: Path) -> Path:
        """Materialization target for the TS hook plugin."""
        return project_dir / ".cline" / "plugins"

    def get_env(self, session_dir: Path, project_dir: Path) -> dict[str, str]:
        # The TS plugin reads AI_HATS_DIR to locate guard scripts (HATS-964).
        from ai_hats.paths import AI_HATS_PROJECT_DIR_ENV, ENV_AI_HATS_DIR
        from ai_hats.paths import ai_hats_dir

        return {
            ENV_AI_HATS_DIR: str(ai_hats_dir(project_dir)),
            AI_HATS_PROJECT_DIR_ENV: str(project_dir),
            # Per-session hub port — parallel sessions EADDRINUSE on the default (HATS-973).
            "CLINE_HUB_PORT": str(self._allocate_hub_port()),
            # Extra hooks scan path — auto-discovery of .cline/plugins/ is unreliable.
            "CLINE_HOOKS_DIR": str(self._plugins_dir(project_dir)),
        }

    @staticmethod
    def _allocate_hub_port() -> int:
        """Bind :0 and return the assigned port — free at allocation; cline rebinds ms later."""
        import socket

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def ensure_runtime_hooks(
        self, project_dir: Path, result: CompositionResult | None = None
    ) -> None:
        """Pre-warm project-scoped ``.cline/plugins/`` (set_role + automate fallback).

        Content is static — no lock/sweep needed, overwrite is safe. HITL gets a
        private session-scoped copy via ``build_session_prompt`` (HATS-981).
        """
        del result  # MVP: guard is unconditional; skill-declared hooks → follow-up.
        self._write_plugin_files(self._plugins_dir(project_dir))
        self._ensure_gitignored(project_dir, ".cline/plugins/")

    @staticmethod
    def _write_plugin_files(plugins_dir: Path) -> None:
        """Write the TS plugin + hook index (static content). No lock needed."""
        plugins_dir.mkdir(parents=True, exist_ok=True)
        plugin_ts = _PLUGIN_TS_PATH.read_text()
        (plugins_dir / _PLUGIN_FILENAME).write_text(plugin_ts)
        (plugins_dir / _INDEX_FILENAME).write_text(
            json.dumps(_GUARD_HOOK_INDEX, indent=2)
        )

    def materialize_runtime_skills(
        self,
        project_dir: Path,
        result: CompositionResult,
        session_id: str,
    ) -> list[str]:
        """Materialize the composed role's skills into ``.cline/skills/``.

        No ``--skills-dir`` flag exists — cline discovers this dir by convention,
        so it stays project-scoped; parallel-safe via a session-ref-counted
        marker (HATS-981). Returns ``[]`` (nothing to pass on the CLI).
        """
        import filelock

        skills_dir = project_dir / ".cline" / "skills"
        marker = skills_dir / _MANAGED_MARKER

        # Gitignore the materialized mirror (parity with .claude/skills/).
        self._ensure_gitignored(project_dir, ".cline/skills/")

        skills_dir.mkdir(parents=True, exist_ok=True)

        # filelock: project-scoped dir, concurrent sessions must serialise.
        lock_path = skills_dir.parent / "skills.lock"
        lock = filelock.FileLock(str(lock_path), timeout=_LOCK_TIMEOUT)
        try:
            with lock:
                self._rebuild_skills(skills_dir, marker, result, project_dir, session_id)
        except filelock.Timeout as exc:
            raise RuntimeError(
                f"cline skills materialization blocked >{_LOCK_TIMEOUT:.0f}s on "
                f"lock {lock_path} — a stuck ai-hats process likely holds it. "
                f"If safe, remove the lock file and retry."
            ) from exc

        return []

    @staticmethod
    def _ensure_gitignored(project_dir: Path, entry: str) -> None:
        """Idempotent: append ``entry`` to project .gitignore if not present."""
        gitignore = project_dir / ".gitignore"
        if gitignore.exists():
            lines = gitignore.read_text().splitlines()
            if entry in lines:
                return
            gitignore.write_text(
                gitignore.read_text().rstrip("\n") + f"\n{entry}\n"
            )
        else:
            gitignore.write_text(f"{entry}\n")

    @staticmethod
    def _rebuild_skills(
        skills_dir: Path,
        marker: Path,
        result: CompositionResult,
        project_dir: Path,
        session_id: str,
    ) -> None:
        """Additive ref-counted rebuild; caller holds the lock (HATS-981).

        The union of ALL sessions' skills stays on disk; only skills no session
        references are swept — no wipe between concurrent sessions.
        """
        from ai_hats.placeholders import expand_path_placeholders

        # Read marker (JSON: session_id → [skill_names]).
        refs: dict[str, list[str]] = {}
        if marker.is_file():
            try:
                refs = json.loads(marker.read_text())
            except (json.JSONDecodeError, ValueError):
                refs = {}  # corrupt or old flat-format marker — start fresh

        prev_all = {name for names in refs.values() for name in names}

        # Register this session's desired skills.
        desired = {s.name for s in result.skills if s.source_path.is_dir()}
        refs[session_id] = sorted(desired)

        new_all = {name for names in refs.values() for name in names}

        # Sweep skills that were managed but no session wants them anymore.
        for name in prev_all - new_all:
            stale = skills_dir / name
            if stale.is_dir():
                shutil.rmtree(stale)

        # Materialize THIS session's skills (content is deterministic on copy).
        for skill in result.skills:
            if not skill.source_path.is_dir():
                continue
            dest = skills_dir / skill.name
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(skill.source_path, dest)
            # Expand <ai_hats_dir> in SKILL.md (HATS-380 parity); other assets verbatim.
            skill_md = dest / "SKILL.md"
            if skill_md.exists():
                original = skill_md.read_text()
                expanded = expand_path_placeholders(original, project_dir)
                if expanded != original:
                    skill_md.write_text(expanded)

        marker.write_text(json.dumps(refs, indent=2, sort_keys=True) + "\n")

    def build_session_prompt(
        self,
        project_dir: Path,
        result: CompositionResult,
        session_id: str,
    ) -> tuple[list[str], dict[str, str], str]:
        """HITL-only: launch the interactive TUI (`-i`) with the role inline (`-s`).

        `-i` never meets the mutually-exclusive `--yolo` (the automate path uses
        `get_run_command`). Third return element = the exact meta-prompt bytes
        WrapRunner persists to `meta_prompt.txt` (HATS-523).
        """
        from ai_hats.placeholders import expand_path_placeholders
        from ai_hats.role_catalog import expand_role_catalog

        prompt_content = self.build_system_prompt(result)
        prompt_content = expand_path_placeholders(prompt_content, project_dir)
        prompt_content = expand_role_catalog(prompt_content, project_dir)

        # Materialize skills before the TUI launches so /skills discovers them.
        self.materialize_runtime_skills(project_dir, result, session_id)

        # Session-scoped plugins dir (--hooks-dir) → full isolation between
        # parallel sessions (HATS-981); ensure_runtime_hooks keeps the
        # project-scoped pre-warm for set_role/automate.
        from ai_hats.paths import session_cache_dir

        self.ensure_runtime_hooks(project_dir, result)
        session_plugins = session_cache_dir(project_dir, session_id) / "plugins"
        self._write_plugin_files(session_plugins)

        from ai_hats.skills_dir import inject_skill_paths_to_env

        extra_env: dict[str, str] = {}
        skills_dir = project_dir / ".cline" / "skills"
        inject_skill_paths_to_env(extra_env, result.skills, skills_dir)

        return (
            ["-i", "-s", prompt_content, "--hooks-dir", str(session_plugins)],
            extra_env,
            prompt_content,
        )

