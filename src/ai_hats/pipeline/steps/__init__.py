"""Built-in steps for ai-hats pipelines.

Importing this package registers every built-in step under its YAML
name in ``pipeline.registry``. The 10 steps come from ADR-0002 §Step
inventory (HATS-273 plan).
"""

from __future__ import annotations

from .. import registry
from .check_update import CheckUpdateAsync
from .compose import ComposeRole
from .emit import EmitStdout
from .extract import ExtractMarker
from .handoff import BuildHandoff
from .launch import LaunchProvider
from .log import PostLog, PreLog
from .materialize import MaterializeSystemPrompt
from .prompt import ResolvePrompt
from .save import SaveArtifact
from .session_review import RunSessionReview
from .spawn_review import SpawnSessionReview
from .update_banner import RenderUpdateBanner


_BUILTINS = {
    "check_update_async": CheckUpdateAsync,
    "compose_role": ComposeRole,
    "materialize_system_prompt": MaterializeSystemPrompt,
    "resolve_prompt": ResolvePrompt,
    "build_handoff": BuildHandoff,
    "pre_log": PreLog,
    "launch_provider": LaunchProvider,
    "spawn_session_review": SpawnSessionReview,
    "extract_marker": ExtractMarker,
    "save_artifact": SaveArtifact,
    "post_log": PostLog,
    "run_session_review": RunSessionReview,
    "render_update_banner": RenderUpdateBanner,
    "emit_stdout": EmitStdout,
}


def _register_builtins() -> None:
    for name, cls in _BUILTINS.items():
        if name in registry.names():
            continue
        registry.register(name, cls)


_register_builtins()


__all__ = [
    "BuildHandoff",
    "CheckUpdateAsync",
    "ComposeRole",
    "EmitStdout",
    "ExtractMarker",
    "LaunchProvider",
    "MaterializeSystemPrompt",
    "PostLog",
    "PreLog",
    "RenderUpdateBanner",
    "ResolvePrompt",
    "RunSessionReview",
    "SaveArtifact",
    "SpawnSessionReview",
]
