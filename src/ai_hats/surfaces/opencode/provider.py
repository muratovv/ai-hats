"""OpenCode adapter for ai-hats.

Role context, skills and runtime hooks are materialized into the ai-hats
session cache and delivered through OpenCode's per-session ``OPENCODE_CONFIG``
config file: a composed primary agent carries the role prompt, and the
runtime-hook dispatcher registers through the config's ``plugin`` array as a
``file://`` entry. Skills are mirrored under a session-scoped
``XDG_CONFIG_HOME`` so OpenCode's native skill discovery sees them without
touching user-owned directories. The project root is never written: no
``AGENTS.md``, no ``.opencode/``.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from ai_hats_core.layout import ProjectLayout
from typing import TYPE_CHECKING

from ai_hats.materialization import describe_merge_json, describe_mkdir
from ai_hats.surfaces import Surface
from ai_hats.session_artifacts import RunMode, SessionPolicy

from ..mirror import mirror_entries, path_dirs
from ..plan import CompositionPlan, Host, Launch, MaterializationPlan, Prompt, mirror_name
from ..skill_index import skill_index_block
from .home import (
    ENV_OPENCODE_CONFIG,
    ENV_XDG_CONFIG_HOME,
    OpenCodeHome,
    plan_projection,
    probe_home,
)

if TYPE_CHECKING:
    from ai_hats_core import CompositionResult

#: The single session agent opencode is launched with (`--agent`). One stable
#: name keeps launch argv free of role-derived values that change per project.
AGENT_NAME = "ai-hats"

_DANGEROUS_FLAGS = {"--auto"}

_SCHEMA = "https://opencode.ai/config.json"


class OpenCodeSurface(Surface):
    """The ``opencode`` entry-point surface, isolated to one ai-hats session."""

    @property
    def name(self) -> str:
        return "opencode"

    def detected_home_dirs(self) -> list[str]:
        return [".opencode"]

    def supports_session_command_wrappers(self) -> bool:
        # Consent middleware is PATH-based around the rack/wt binaries and
        # provider-agnostic (ADR-0030); opencode inherits launch env like codex.
        return True

    def surface_hints(self) -> list:
        from ai_hats.surfaces import SurfaceHint

        return [
            SurfaceHint(
                name="--model",
                values="<provider/model>",
                description="Override the model for this session.",
            ),
        ]

    def system_prompt_path(self, layout: ProjectLayout) -> Path | None:
        """OpenCode receives ai-hats context via the session agent; no project file."""
        del layout
        return None

    def rules_dir(self, session_dir: Path) -> Path:
        return session_dir / "rules"

    def build_system_prompt(self, result: "CompositionResult") -> str:
        # The session-aware skill index is appended by _expanded_prompt, where
        # exact paths in this session's cache are available.
        return self._compose_sections(result)

    # --- deterministic session paths -------------------------------------------------

    def session_config_path(self, layout: ProjectLayout, session_id: str) -> Path:

        return layout.cache.session(session_id) / "opencode" / "opencode.json"

    def session_xdg_config_home(self, layout: ProjectLayout, session_id: str) -> Path:
        """The ``XDG_CONFIG_HOME`` value pinned for this session's child process.

        OpenCode resolves its global config dir as ``<XDG_CONFIG_HOME>/opencode``
        (probed on 1.18.21 via ``debug paths``), so pointing it into the session
        cache makes the native skill discovery read the ai-hats mirror while
        user-owned entries stay reachable through base-home projection.
        """

        return layout.cache.session(session_id) / "opencode-xdg"

    def session_skills_root(self, layout: ProjectLayout, session_id: str) -> Path:
        # Inside the redirected config dir: <XDG>/opencode/skills is a native
        # discovery path, so the mirror doubles as real skills.
        return self.session_xdg_config_home(layout, session_id) / "opencode" / "skills"

    # --- the plan (ADR-0036 D2): entries, env and launch from the composition half --

    def probe_home(self, environ: Mapping[str, str]) -> OpenCodeHome:
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
        from .runtime_hooks import plan_hooks

        home = host.home
        if not isinstance(home, OpenCodeHome):
            raise RuntimeError(
                "opencode plans from its probed home: probe_host(surface=<opencode>) "
                "before planning"
            )
        mode = RunMode(run_mode)
        xdg_root = root / "opencode-xdg"
        session_config_dir = xdg_root / "opencode"
        skills_root = session_config_dir / "skills"
        config_path = root / "opencode" / "opencode.json"
        prompt = composition.prompt
        if index := skill_index_block(composition, skills_root, surface=self.name):
            prompt = Prompt((*prompt.blocks, index))
        document: dict[str, object] = {"$schema": _SCHEMA}
        entries = [describe_mkdir(root)]
        args: list[str] = []
        env: dict[str, str] = {}
        if policy.context:
            document["agent"] = {
                AGENT_NAME: {
                    "description": f"ai-hats composed role session ({composition.identity})",
                    "mode": "primary",
                    "prompt": prompt.text,
                }
            }
            args += ["--agent", AGENT_NAME]
            env[ENV_OPENCODE_CONFIG] = str(config_path)
        if composition.skills:
            entries.append(describe_mkdir(skills_root))
            entries += mirror_entries(composition, skills_root)
            mirrored = {mirror_name(skill) for skill in composition.skills}
            entries += plan_projection(home, session_config_dir, mirrored=mirrored)
            if dirs := path_dirs(composition, skills_root):
                env["PATH"] = os.pathsep.join([*(str(d) for d in dirs), host.path])
            env[ENV_XDG_CONFIG_HOME] = str(xdg_root)
        if policy.hooks:
            manifest, plugin, hook_env = plan_hooks(
                composition,
                root,
                host,
                layout=layout,
                skills_root=skills_root,
                permission_rules=self._permission_rules(layout),
            )
            entries += [manifest, plugin]
            env.update(hook_env)
            document["plugin"] = [f"file://{plugin.target}"]
        if policy.context or policy.hooks:
            entries.append(describe_merge_json(config_path, document))
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

    # --- hooks -----------------------------------------------------------------------

    def _permission_rules(self, layout: ProjectLayout) -> list[dict[str, str]]:
        """Role-owned permission policy shipped in the hook manifest.

        The generated opencode.json carries no permission keys (Q2: decisions
        live in the role, not in a global-looking config). The single rule
        declares ai-hats' own cache subtree session-owned; every other native
        ask defers — to the TUI prompt in HITL, to opencode's headless
        auto-reject otherwise.
        """

        return [
            {
                "permission": "external_directory",
                "prefix": f"{layout.cache.root}/",
                "action": "allow",
            },
        ]

    # --- launch ----------------------------------------------------------------------

    @staticmethod
    def _validate_passthrough(args: list[str]) -> None:
        for arg in args:
            token = arg.split("=", 1)[0].lower()
            if token in _DANGEROUS_FLAGS or token == "-p":  # noqa: S105 — a CLI flag, not a secret
                raise ValueError(f"unsafe OpenCode option is not allowed by ai-hats: {arg}")
            if arg == "--agent":
                raise ValueError(
                    "unsafe OpenCode option is not allowed by ai-hats: --agent is owned "
                    f"by ai-hats ({AGENT_NAME})"
                )

    def get_cli_command(self, args: list[str] | None = None) -> list[str]:
        extra = list(args or [])
        self._validate_passthrough(extra)
        return ["opencode", *extra]

    def get_run_command(self, cmd: list[str], meta_prompt: str) -> list[str]:
        """Build the headless `opencode run ...` command for Automate launches."""
        base = list(cmd or ["opencode"])
        return [base[0], "run", *base[1:], meta_prompt]

    def get_env(self, session_dir: Path, layout: ProjectLayout) -> dict[str, str]:
        project_dir = layout.root
        del session_dir
        from ai_hats.paths import AI_HATS_PROJECT_DIR_ENV, ENV_AI_HATS_DIR

        return {
            ENV_AI_HATS_DIR: str(layout.base),
            AI_HATS_PROJECT_DIR_ENV: str(project_dir),
        }
