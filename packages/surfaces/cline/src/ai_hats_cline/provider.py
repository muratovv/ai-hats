"""Cline surface adapter — maps the `cline` CLI to the ai-hats `Provider`.

Inline-`-s` surface registered via the `ai_hats.providers` entry point (HATS-870).
Verified cline-v3.0.3 flag facts + the CLINE_DATA_DIR/auth rationale: HATS-956.
Skill materialization into `.cline/skills/` native registry: HATS-963.
Hooks materialization via TS plugin wrapper into `.cline/plugins/`: HATS-964.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from ai_hats.providers import Provider

if TYPE_CHECKING:
    # Re-exported by the integrator; import here keeps the plugin's only
    # first-party root `ai_hats` (workspace-boundary Rule 1, HATS-869).
    from ai_hats.providers import CompositionResult
    from ai_hats_observe.parsers.base import TranscriptParser

# Marker file tracking which skill dirs under `.cline/skills/` are ai-hats-owned
# (so user-authored skills are preserved on re-materialization). Mirrors the
# `.ai-hats-managed` convention from plugin_dir.py (HATS-901).
_MANAGED_MARKER = ".ai-hats-managed"

# HATS-963: filelock timeout for concurrent cline sessions (plugin_dir.py:60
# pattern). The rebuild is sub-second; a timeout means a stuck holder.
_LOCK_TIMEOUT = 30.0

# HATS-964: filenames materialized into .cline/plugins/.
_PLUGIN_FILENAME = "ai-hats-hooks.ts"
_INDEX_FILENAME = "ai-hats-hooks.json"

# HATS-964 R1: the guard reads .tool_input.command (guard L41). The TS plugin
# must emit this exact stdin shape — the card's {tool,command} shape produces
# an empty extraction → silent allow.
_GUARD_HOOK_INDEX = [
    {"event": "PreToolUse", "cline_tool": "bash",
     "script": "pre_bash_shared_state_guard.sh"},
]

# HATS-964: the TS plugin lives as a separate template file (see
# templates/ai-hats-hooks.ts). Cline plugin model + lifecycle hooks:
# https://docs.cline.bot/sdk/plugins  (AgentPlugin/hooks/beforeTool).
_PLUGIN_TS_PATH = Path(__file__).parent / "templates" / _PLUGIN_FILENAME


class ClineProvider(Provider):
    """`cline` CLI adapter, registered via the `ai_hats.providers` entry point."""

    @property
    def name(self) -> str:
        return "cline"

    def transcript_parser(self) -> TranscriptParser:
        # HATS-960: cline emits a structured `.messages.json` → richer parse than
        # the default trace-only. Lazy import so entry-point discovery of the
        # provider class never eager-loads the observe parsers.
        from ai_hats_cline.parser import ClineParser

        return ClineParser()

    def system_prompt_path(self, project_dir: Path) -> Path:
        # Vestigial: cline takes the role inline via `-s`, so this path is never
        # read or written (update_system_prompt is a no-op). The ABC requires it.
        return project_dir / "CLINE.md"

    def update_system_prompt(self, project_dir: Path, content: str) -> None:
        # Inline-only surface: the role reaches cline through `-s`
        # (build_session_prompt), never a static file — so `set_role` must not
        # write a CLINE.md that cline would ignore.
        del project_dir, content

    def rules_dir(self, session_dir: Path) -> Path:
        return session_dir / "rules"

    def build_system_prompt(self, result: CompositionResult) -> str:
        # HATS-963: skills reach cline via the native `.cline/skills/` registry
        # (materialize_runtime_skills). Live smoke confirmed /skills works in
        # the TUI, so the text index is a duplicate — suppress it (~1.5k
        # tok/session saving). Claude precedent: providers.py:420-424.
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
        *,
        model: str | None = None,
    ) -> list[str]:
        # Headless one-shot. Strip only the interactive flags so a HITL `-i`
        # never collides with `--yolo`, while other passthrough (e.g. future
        # skill args) survives. Task prompt is positional (last).
        kept = [a for a in (cmd or ["cline"]) if a not in ("-i", "--tui")]
        extra = ["--model", model] if model else []
        return [*kept, "--yolo", "--json", *extra, meta_prompt]

    @staticmethod
    def _plugins_dir(project_dir: Path) -> Path:
        """Materialization target for the TS hook plugin."""
        return project_dir / ".cline" / "plugins"

    def get_env(self, session_dir: Path, project_dir: Path) -> dict[str, str]:
        # HATS-964 R7: the TS plugin reads AI_HATS_DIR to locate guard scripts
        # (same shape as ClaudeProvider, providers.py:554-557).
        from ai_hats.paths import AI_HATS_PROJECT_DIR_ENV, ENV_AI_HATS_DIR
        from ai_hats.paths import ai_hats_dir

        return {
            ENV_AI_HATS_DIR: str(ai_hats_dir(project_dir)),
            AI_HATS_PROJECT_DIR_ENV: str(project_dir),
            # HATS-973: per-session hub port so parallel cline sessions don't
            # collide on the default 25463 (EADDRINUSE). Ephemeral allocation
            # guarantees the port is free at bind time; cline reads it via
            # process.env.CLINE_HUB_PORT (explicit-endpoint mode).
            "CLINE_HUB_PORT": str(self._allocate_hub_port()),
            # HATS-964: point cline at our materialized TS plugin directory so
            # the shared-state guard loads. Auto-discovery of .cline/plugins/ is
            # unreliable (configExtensionCount:0 in hub-daemon logs); the env
            # var adds it as an additional hooks scan path.
            "CLINE_HOOKS_DIR": str(self._plugins_dir(project_dir)),
        }

    @staticmethod
    def _allocate_hub_port() -> int:
        """Bind to an ephemeral port (:0), read the assigned port, close.

        Guarantees the port was free at allocation time. The same process that
        closes this socket spawns cline milliseconds later — the TOCTOU window
        before cline's hub daemon rebinds is negligible. Standard pattern used
        by Jupyter / Dask / pytest-fixture port helpers.
        """
        import socket

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def ensure_runtime_hooks(
        self, project_dir: Path, result: CompositionResult | None = None
    ) -> None:
        """Materialize the TS plugin + hook index into ``.cline/plugins/``.

        HATS-964: the TS plugin template lives in ``templates/ai-hats-hooks.ts``.
        ``CLINE_HOOKS_DIR`` env (set by ``get_env``) points cline at this
        directory so it scans for the plugin at session start — cline v3.0.3
        does NOT auto-discover ``.cline/plugins/`` (hub-daemon logs show
        ``configExtensionCount:0`` for every session).
        """
        import filelock

        del result  # MVP: guard is unconditional; skill-declared hooks → follow-up.
        plugins_dir = self._plugins_dir(project_dir)
        plugins_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_gitignored(project_dir, ".cline/plugins/")

        lock_path = plugins_dir / ".lock"
        lock = filelock.FileLock(str(lock_path), timeout=_LOCK_TIMEOUT)
        try:
            with lock:
                self._rebuild_plugins(plugins_dir)
        except filelock.Timeout as exc:
            raise RuntimeError(
                f"cline plugins materialization blocked >{_LOCK_TIMEOUT:.0f}s "
                f"on lock {lock_path}."
            ) from exc

    @staticmethod
    def _rebuild_plugins(plugins_dir: Path) -> None:
        """Sweep stale + write plugin files. Caller holds the lock."""
        # Sweep stale managed files (orphaned from a previous session/role).
        managed_marker = plugins_dir / ".ai-hats-managed"
        prev_managed: set[str] = set()
        if managed_marker.is_file():
            prev_managed = {
                line.strip()
                for line in managed_marker.read_text().splitlines()
                if line.strip()
            }
        for name in prev_managed:
            stale = plugins_dir / name
            if stale.exists():
                stale.unlink()

        # Write plugin + index from template.
        plugin_ts = _PLUGIN_TS_PATH.read_text()
        (plugins_dir / _PLUGIN_FILENAME).write_text(plugin_ts)
        (plugins_dir / _INDEX_FILENAME).write_text(
            json.dumps(_GUARD_HOOK_INDEX, indent=2)
        )

        managed_marker.write_text(
            "\n".join(sorted({_PLUGIN_FILENAME, _INDEX_FILENAME})) + "\n"
        )

    def materialize_runtime_skills(
        self,
        project_dir: Path,
        result: CompositionResult,
        session_id: str,
    ) -> list[str]:
        """Materialize the composed role's skills into `.cline/skills/`.

        HATS-963: cline discovers skills by convention from `<project>/.cline/
        skills/` (docs.cline.bot/features/skills). No `--skills-dir` flag —
        returns ``[]`` (discovery is purely directory-based).

        HATS-981: parallel-safe. Each session registers its skills under its
        ``session_id`` in the ``.ai-hats-managed`` marker (JSON dict). The
        union of ALL sessions' skills stays on disk — two concurrent cline
        sessions with different roles each see their OWN skills (no wipe).
        User-authored skill dirs (not in the marker) are always preserved.
        """
        import filelock

        skills_dir = project_dir / ".cline" / "skills"
        marker = skills_dir / _MANAGED_MARKER

        # HATS-963 R4c: gitignore the materialized mirror so it doesn't surface
        # as untracked (parity with .claude/skills/, root .gitignore:44).
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

        return []  # no CLI flag — cline discovers .cline/skills/ by convention

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
        """Additive ref-counted materialization. Caller holds the lock.

        HATS-981: each session registers its desired skills under its
        ``session_id`` in the marker (JSON dict).  The union of ALL sessions'
        skills stays on disk; only skills no session references are swept.
        """
        from ai_hats.placeholders import expand_path_placeholders

        # Read marker (JSON: session_id → [skill_names]).
        refs: dict[str, list[str]] = {}
        if marker.is_file():
            try:
                refs = json.loads(marker.read_text())
            except (json.JSONDecodeError, ValueError):
                refs = {}  # HATS-981: corrupt or old flat-format marker

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
            # HATS-380 parity: expand <ai_hats_dir> in SKILL.md before cline
            # reads it. Other assets (hooks, fixtures) are copied verbatim.
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
        """HITL (WrapRunner-only): compose the role, expand placeholders, and
        hand cline the interactive TUI (`-i`) plus the role inline (`-s`).

        `-i` is safe here: `build_session_prompt` is HITL-exclusive (the automate
        path uses `get_run_command` with `--yolo`), so the TUI flag never meets
        the mutually-exclusive `--yolo`. The third return element is the exact
        meta-prompt bytes WrapRunner persists to `meta_prompt.txt` (HATS-523).
        """
        from ai_hats.placeholders import expand_path_placeholders
        from ai_hats.role_catalog import expand_role_catalog

        prompt_content = self.build_system_prompt(result)
        prompt_content = expand_path_placeholders(prompt_content, project_dir)
        prompt_content = expand_role_catalog(prompt_content, project_dir)

        # HATS-963: materialize the role's skills into .cline/skills/ before
        # the TUI launches, so /skills discovers them. Mirrors Claude's
        # build_session_prompt calling materialize_runtime_skills (providers.py:492).
        self.materialize_runtime_skills(project_dir, result, session_id)

        # HATS-964: materialize the TS hook plugin into .cline/plugins/ so the
        # shared-state guard is active in the cline session (safety net —
        # ensure_runtime_hooks also runs during _refresh/set_role).
        self.ensure_runtime_hooks(project_dir, result)

        plugins_dir = self._plugins_dir(project_dir)
        return (
            ["-i", "-s", prompt_content, "--hooks-dir", str(plugins_dir)],
            {},
            prompt_content,
        )
