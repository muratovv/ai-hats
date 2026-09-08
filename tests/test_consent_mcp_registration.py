import tomllib
from pathlib import Path
import pytest

from ai_hats.surfaces.codex.provider import CodexSurface
from ai_hats.surfaces.mcp import StdioMCPServer


def test_surface_configures_form_server_without_operation_knowledge():
    server = StdioMCPServer(
        name="confirmation",
        command="/python",
        args=("-m", "confirmation.server"),
        cwd=Path("/project"),
        env_vars=("SESSION_ID",),
        startup_timeout_s=30,
        tool_timeout_s=960,
    )

    args = CodexSurface().mcp_form_cli_args(server)

    settings = {}
    for flag, value in zip(args[::2], args[1::2]):
        assert flag == "-c"
        settings.update(tomllib.loads(value)["mcp_servers"]["confirmation"])
    assert settings["command"] == "/python"
    assert settings["args"] == ["-m", "confirmation.server"]
    assert settings["cwd"] == "/project"
    assert settings["required"] is True


@pytest.mark.parametrize(
    "policy,hooks_enabled",
    [
        ({"wt.merge": ["pre-merge"]}, True),
        ({"rack.transition": ["plan->execute"]}, False),
    ],
)
def test_unregistered_policy_or_disabled_hooks_do_not_add_form_server(policy, hooks_enabled):
    from ai_hats.consent_mcp.registration import register_server
    from ai_hats.session_artifacts import BuiltArtifacts
    from ai_hats.env import ENV_SESSION_CACHE_DIR

    artifacts = BuiltArtifacts()
    if hooks_enabled:
        artifacts.extra_env[ENV_SESSION_CACHE_DIR] = "/session-cache"

    register_server(Path("/project"), policy, CodexSurface(), artifacts)

    assert artifacts.cli_args == []
