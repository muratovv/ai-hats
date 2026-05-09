"""Built-in pipeline presets.

Phase 1 ships exactly one preset — ``execute_pipeline`` — used by the
``ai-hats execute`` CLI command. Its shape is
``[PreLogStub, LaunchProvider, PostLogStub]`` so a developer running
``ai-hats execute`` can see pre/post nodes fire on stderr. The silent
``PreStub`` / ``PostStub`` classes remain in ``steps.stubs`` as the
minimal reference contract for Phase 2/3 step authors.

Other CLI commands (bare ``ai-hats``, ``agent``, ``reflect all``,
``reflect session``) still use their original direct-call paths;
pipeline-ising them is Phase 4 work.
"""

from __future__ import annotations

from .pipeline import build
from .steps.launch import LaunchProvider
from .steps.stubs import PostLogStub, PreLogStub


execute_pipeline = build(
    PreLogStub(),
    LaunchProvider(),
    PostLogStub(),
    name="execute",
)
