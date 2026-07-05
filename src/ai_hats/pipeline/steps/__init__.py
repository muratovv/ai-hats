"""Built-in steps for ai-hats pipelines.

Importing this package registers every built-in step under its YAML
name in ``pipeline.registry``. The 10 steps come from ADR-0002 §Step
inventory (HATS-273 plan).
"""

from __future__ import annotations

from .. import registry
from .check_update import CheckUpdateAsync
from .compose import ComposeRole
from .compute_usage import ComputeUsage
from .emit import EmitStdout
from .extract import ExtractMarker
from .handoff import BuildHandoff
from .launch import LaunchProvider, Provider
from .log import PostLog, PreLog
from .make_audit import MakeAudit
from .materialize import MaterializeSystemPrompt
from .maybe_spawn_session_reviewer import MaybeSpawnSessionReviewer
from .prompt import ResolvePrompt
from .quorum_autoclose import QuorumAutoclose
from .run_session_end import RunSessionEnd
from .save import SaveArtifact
from .session_review import RunSessionReview
from .spawn_review import SpawnSessionReview
from .update_banner import RenderUpdateBanner


_BUILTIN_CLASSES = (
    CheckUpdateAsync,
    ComposeRole,
    MaterializeSystemPrompt,
    ResolvePrompt,
    BuildHandoff,
    PreLog,
    Provider,
    MakeAudit,
    ComputeUsage,
    MaybeSpawnSessionReviewer,
    RunSessionEnd,
    QuorumAutoclose,
    SpawnSessionReview,
    ExtractMarker,
    SaveArtifact,
    PostLog,
    RunSessionReview,
    RenderUpdateBanner,
    EmitStdout,
)


def _step_name(cls: type) -> str:
    return getattr(cls, "_NAME", None) or cls().io.name


# Registry key derives from each step's own StepIO declaration (HATS-917) —
# the YAML id is spelled once, in the step.
_BUILTINS = {_step_name(cls): cls for cls in _BUILTIN_CLASSES}
# HATS-535: pre-split YAML pipelines still load ``launch_provider``.
_BUILTINS["launch_provider"] = LaunchProvider


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
    "ComputeUsage",
    "EmitStdout",
    "ExtractMarker",
    "LaunchProvider",
    "MakeAudit",
    "MaterializeSystemPrompt",
    "MaybeSpawnSessionReviewer",
    "PostLog",
    "PreLog",
    "Provider",
    "QuorumAutoclose",
    "RenderUpdateBanner",
    "ResolvePrompt",
    "RunSessionEnd",
    "RunSessionReview",
    "SaveArtifact",
    "SpawnSessionReview",
]
