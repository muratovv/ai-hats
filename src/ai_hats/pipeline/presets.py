"""Built-in pipeline presets — Python-level reference compositions.

The 4 production-shape pipelines live as YAML in
``ai_hats/library/core/pipelines/`` and are loaded via ``loader.load_pipeline``.
``execute_pipeline`` here mirrors ``execute.yaml`` but built in Python —
kept for tests that exercise the pipeline core without YAML parsing.
"""

from __future__ import annotations

from .keys import PIPELINE_EXECUTE
from .pipeline import build
from .steps.check_update import CheckUpdateAsync
from .steps.compose import ComposeRole
from .steps.launch import Provider
from .steps.log import PostLog, PreLog
from .steps.prompt import ResolvePrompt
from .steps.spawn_review import SpawnSessionReview
from .steps.update_banner import RenderUpdateBanner


execute_pipeline = build(
    CheckUpdateAsync(),
    ComposeRole(),
    ResolvePrompt({"default_text": ""}),
    PreLog({"keys": ["role", "system_prompt", "prompt_text"]}),
    Provider(),
    SpawnSessionReview({"max_retries": 1}),
    PostLog({"keys": ["session_id", "exit_code", "review_pid"]}),
    RenderUpdateBanner(),
    name=PIPELINE_EXECUTE,
)
