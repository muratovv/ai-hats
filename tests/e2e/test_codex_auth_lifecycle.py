"""e2e (HATS-1896)

flow:   a user logs out and logs back in across isolated Codex session homes
cmds:
    codex logout
    codex login --with-api-key
    codex login status
expect: logout and subsequent login persist into the next session home
why:    unlinking a projected auth symlink used to leave canonical credentials behind
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from _helpers.env import clean_env
from ai_hats.materialization import describe_mkdir
from ai_hats.session_artifacts import RunMode, SessionPolicy
from ai_hats.surfaces.codex.session_auth import auth_digest, plan_auth, reconcile_auth
from ai_hats.surfaces.plan import (
    CompositionPlan,
    Hooks,
    Launch,
    MaterializationPlan,
    Prompt,
    PromptBlock,
    PromptMember,
    apply,
)

pytestmark = [pytest.mark.integration, pytest.mark.surfaces]


def apply_auth(base_home: Path, session_home: Path) -> None:
    """The credential staged the way a session plans it: a private copy and
    its baseline digest, applied under the session home as the root."""
    composition = CompositionPlan(
        identity="codex-auth",
        prompt=Prompt((PromptBlock(None, (PromptMember("codex-auth::prompt", "# r\n", None),)),)),
        skills=(),
        hooks=Hooks((), ()),
        trace=(),
    )
    apply(
        MaterializationPlan(
            composition=composition,
            prompt=composition.prompt,
            surface="codex",
            run_mode=RunMode.HITL,
            policy=SessionPolicy(),
            root=session_home,
            entries=(
                describe_mkdir(session_home),
                *plan_auth(base_home, session_home, auth_digest(base_home)),
            ),
            env={},
            launch=Launch(args=(), sdk_options=None),
        )
    )


def test_real_codex_logout_and_login_survive_session_homes(tmp_path: Path) -> None:
    codex = shutil.which("codex")
    if codex is None:
        pytest.skip("codex binary not found")
    base_home = tmp_path / "base"
    base_home.mkdir()
    user_home = tmp_path / "user"
    user_home.mkdir()
    env = clean_env()
    env["HOME"] = str(user_home)
    env.pop("AI_HATS_CODEX_BASE_HOME", None)
    env.pop("CODEX_SQLITE_HOME", None)

    def run(home: Path, *args: str, key: str | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 - local binary with synthetic file credentials
            [codex, "-c", 'cli_auth_credentials_store="file"', *args],
            input=key,
            text=True,
            capture_output=True,
            cwd=tmp_path,
            env={**env, "CODEX_HOME": str(home)},
            timeout=30,
        )

    login = run(base_home, "login", "--with-api-key", key="synthetic-initial-key\n")
    assert login.returncode == 0, login.stderr
    assert (base_home / "auth.json").is_file()
    session_home = tmp_path / "logout"
    apply_auth(base_home, session_home)

    logout = run(session_home, "logout")
    assert logout.returncode == 0, logout.stderr
    assert not (session_home / "auth.json").exists()
    assert reconcile_auth(base_home, session_home) is None
    assert not (base_home / "auth.json").exists()

    next_home = tmp_path / "login"
    apply_auth(base_home, next_home)
    status = run(next_home, "login", "status")
    assert status.returncode == 1, status.stderr
    login = run(next_home, "login", "--with-api-key", key="synthetic-renewed-key\n")
    assert login.returncode == 0, login.stderr
    assert reconcile_auth(base_home, next_home) is None

    final_home = tmp_path / "verify"
    apply_auth(base_home, final_home)
    status = run(final_home, "login", "status")
    assert status.returncode == 0, status.stderr
    assert (final_home / "auth.json").read_bytes() == (next_home / "auth.json").read_bytes()
