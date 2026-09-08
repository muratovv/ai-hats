"""Register the consent-owned form server through the surface's MCP contract."""

import sys
from pathlib import Path
from collections.abc import Mapping, Sequence

from ai_hats_library.hooks.consent_gate import operations
from ai_hats_library.hooks.consent_gate.questions import RACK_FORM

from ..env import (
    AI_HATS_PROJECT_DIR_ENV,
    ENV_AI_HATS_DIR,
    ENV_SESSION_CACHE_DIR,
    ENV_AI_HATS_PYTHON,
    ENV_AI_HATS_CACHE_HOME,
    ENV_AI_HATS_USER_HOME,
    ENV_LIBRARY_ROOT,
)
from ..consent_wrapper import CONFIG_ENV
from ..session_identity import IDENTITY_ENV_KEYS
from ..session_artifacts import BuiltArtifacts
from ..surfaces import Surface, StdioMCPServer


def register_server(
    project_dir: Path,
    policy: Mapping[str, Sequence[str]],
    provider: Surface,
    artifacts: BuiltArtifacts,
) -> None:
    if (
        RACK_FORM.operation not in policy
        or provider.name != RACK_FORM.provider
        or ENV_SESSION_CACHE_DIR not in artifacts.extra_env
    ):
        return
    spec = operations.spec_for(RACK_FORM.operation)
    if spec is None:
        raise RuntimeError(f"Missing consent operation: {RACK_FORM.operation}")
    legacy = {
        flag
        for selector in policy[RACK_FORM.operation]
        for flag in spec.legacy_flags(operations.Reading("", "", selector.partition("->")[2]))
    }
    server = StdioMCPServer(
        name=RACK_FORM.tool.partition(".")[0],
        command=sys.executable,
        args=("-B", "-m", RACK_FORM.module),
        cwd=project_dir.resolve(),
        env_vars=(
            *IDENTITY_ENV_KEYS,
            CONFIG_ENV,
            "PATH",
            ENV_AI_HATS_DIR,
            AI_HATS_PROJECT_DIR_ENV,
            ENV_SESSION_CACHE_DIR,
            ENV_AI_HATS_PYTHON,
            ENV_AI_HATS_CACHE_HOME,
            ENV_AI_HATS_USER_HOME,
            ENV_LIBRARY_ROOT,
            *sorted(legacy),
        ),
        startup_timeout_s=30,
        tool_timeout_s=960,
    )
    args = provider.mcp_form_cli_args(server)
    if args is None:
        raise RuntimeError(f"{provider.name} cannot deliver its registered consent form")
    artifacts.cli_args.extend(args)
