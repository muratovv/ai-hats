"""The environment an e2e test launches ``ai-hats headless`` in, around the packaged stub."""

from __future__ import annotations

import os

from ai_hats_client.testing import StubClaude

from _helpers.env import clean_env


def session_env(stub: StubClaude, project) -> dict[str, str]:
    """The scrubbed parent env, the project's pins, and the stub first on PATH."""
    env = clean_env(os.environ)
    env.update(project.env)
    env.update(stub.env(env.get("PATH", "")))
    env["AI_HATS_NO_UPDATE_CHECK"] = "1"
    return env
