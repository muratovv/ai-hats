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
from .launch import LaunchProvider, Provider
from .log import PostLog, PreLog
from .make_audit import MakeAudit
from .materialize import MaterializeSystemPrompt
from .maybe_spawn_session_reviewer import MaybeSpawnSessionReviewer
from .prompt import ResolvePrompt
from .run_session_end import RunSessionEnd
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
    # HATS-535: ``launch_provider`` retained as a deprecated alias for
    # ``provider`` so externally-loaded YAML pipelines that pre-date the
    # split keep loading. Both resolve to the same class; the new YAML
    # convention is ``provider``.
    "launch_provider": LaunchProvider,
    "provider": Provider,
    "make_audit": MakeAudit,
    # HATS-530: shared by both finalize-hitl and finalize-subagent.
    "maybe_spawn_session_reviewer": MaybeSpawnSessionReviewer,
    "run_session_end": RunSessionEnd,
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
    "MakeAudit",
    "MaterializeSystemPrompt",
    "MaybeSpawnSessionReviewer",
    "PostLog",
    "PreLog",
    "Provider",
    "RenderUpdateBanner",
    "ResolvePrompt",
    "RunSessionEnd",
    "RunSessionReview",
    "SaveArtifact",
    "SpawnSessionReview",
]
