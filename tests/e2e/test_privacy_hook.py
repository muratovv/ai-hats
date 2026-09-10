"""e2e (HATS-633)

flow:   a developer committing files containing potential API keys or private tokens
cmds:
    git commit -m "add config"
expect: privacy pre-commit hook scans staged diffs, blocks commits containing secrets,
        and logs journal
why: without privacy pre-commit hooks, sensitive tokens and API keys get accidentally
     committed to git"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.guards


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PRIVACY_HOOK = (
    REPO_ROOT
    / "packages/ai-hats-library/src/ai_hats_library/core/skills/git-mastery/git_hooks/pre-commit-privacy.sh"
)

# Synthetic secrets — fake values shaped to match the catalogue regexes. The
# fixed-length ones (GitHub 36, AWS 40, SendGrid 22/43, npm 36) were validated
# against the live patterns before being baked in here.
PRIVATE_KEY = "-----BEGIN OPENSSH PRIVATE KEY-----"
DB_URI_CREDS = "DATABASE_URL=postgres://admin:s3cr3tP4ss@db.internal:5432/app"
GITHUB_OAUTH = "token=gho_0123456789abcdefghijklmnopqrstuvwxyz"
GITHUB_PAT = "github_pat_11ABCDEFG0123456789abcdef_GhIjKlMnOpQrStUvWx"
AWS_SECRET = 'aws_secret_access_key = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"'  # noqa: S105 — a decoy the hook must catch
SLACK_WEBHOOK = "url=https://hooks.slack.com/services/T00000000/B11111111/abcdEFGH1234ijklMNOP5678"
STRIPE_LIVE = "stripe=sk_live_4eC39HqLyjWDarjtT1zdp7dc"
SENDGRID = "SENDGRID_API_KEY=SG.ngeVfQFYQlKU0Zcu8XPHvw.Tnl0YtBNZ7w7nP1234567890abcdefghijklmnopqrs"
NPM_TOKEN = "//registry.npmjs.org/:_authToken=npm_0123456789abcdefghijklmnopqrstuvwxyz"  # noqa: S105 — a decoy the hook must catch
JWT = (
    "Authorization=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
)

# AKIA access-key id — pre-existing pattern (PAT_API_KEY), used as a regression
# control that the catalogue extension did not break what already worked.
AKIA_KEY = "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE"

CATALOGUE = [
    pytest.param(PRIVATE_KEY, id="private-key-header"),
    pytest.param(DB_URI_CREDS, id="db-uri-with-creds"),
    pytest.param(GITHUB_OAUTH, id="github-oauth-token"),
    pytest.param(GITHUB_PAT, id="github-fine-grained-pat"),
    pytest.param(AWS_SECRET, id="aws-secret-access-key"),
    pytest.param(SLACK_WEBHOOK, id="slack-webhook"),
    pytest.param(STRIPE_LIVE, id="stripe-live-key"),
    pytest.param(SENDGRID, id="sendgrid-key"),
    pytest.param(NPM_TOKEN, id="npm-token"),
    pytest.param(JWT, id="jwt"),
]


@pytest.fixture
def privacy_repo(tmp_path: Path) -> Path:
    """A fresh git repo (no commits, no allowlist) to stage content into."""
    subprocess.run(["git", "init", "--quiet"], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "config", "user.email", "t@e.x"], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=str(tmp_path), check=True)
    return tmp_path


def _stage_and_run(repo: Path, content: str, *, filename: str = "leak.txt"):
    """Write + stage `content`, then run the privacy hook from the repo root."""
    (repo / filename).write_text(content + "\n")
    subprocess.run(["git", "add", filename], cwd=str(repo), check=True)
    env = os.environ.copy()
    # Don't let an ambient developer override mask the hook behaviour.
    env.pop("AI_HATS_PRIVACY_ACK", None)
    return subprocess.run(
        ["bash", str(PRIVACY_HOOK)],
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )


# --- catalogue: every credential class must hard-block -----------------------


@pytest.mark.integration
@pytest.mark.parametrize("payload", CATALOGUE)
def test_catalogue_blocks_credential(privacy_repo: Path, payload: str):
    res = _stage_and_run(privacy_repo, payload)
    assert res.returncode == 1, f"expected block, got {res.returncode}\n{res.stderr}"
    assert "commit blocked" in res.stderr


# --- regression: the pre-existing AKIA pattern still blocks -------------------


@pytest.mark.integration
def test_existing_akia_pattern_still_blocks(privacy_repo: Path):
    res = _stage_and_run(privacy_repo, AKIA_KEY)
    assert res.returncode == 1, res.stderr


# --- negative controls: must NOT block ---------------------------------------


@pytest.mark.integration
def test_credential_free_db_uri_allowed(privacy_repo: Path):
    """Bare `postgres://localhost` has no user:pass@ — must not false-positive."""
    res = _stage_and_run(privacy_repo, "DSN=postgres://localhost:5432/app")
    assert res.returncode == 0, res.stderr


@pytest.mark.integration
def test_clean_file_allowed(privacy_repo: Path):
    res = _stage_and_run(privacy_repo, "just an ordinary line of code\nx = 1 + 2")
    assert res.returncode == 0, res.stderr


# --- inline FP allow-marker --------------------------------------------------


@pytest.mark.integration
def test_inline_marker_skips_only_that_line(privacy_repo: Path):
    """A confirmed FP line carrying the marker is skipped → commit allowed."""
    res = _stage_and_run(privacy_repo, f"{DB_URI_CREDS}  # ai-hats: allow-secret")
    assert res.returncode == 0, res.stderr


@pytest.mark.integration
def test_same_secret_without_marker_still_blocks(privacy_repo: Path):
    """Positive control for the marker: without it, the identical line blocks."""
    res = _stage_and_run(privacy_repo, DB_URI_CREDS)
    assert res.returncode == 1, res.stderr


@pytest.mark.integration
def test_marker_does_not_whitelist_a_different_line(privacy_repo: Path):
    """Marker is line-scoped: a marked FP line must not unblock a real leak on
    another line of the same file."""
    content = f"{DB_URI_CREDS}  # ai-hats: allow-secret\n{PRIVATE_KEY}"
    res = _stage_and_run(privacy_repo, content)
    assert res.returncode == 1, res.stderr


# --- denial advertises the marker (discoverability) --------------------------


@pytest.mark.integration
def test_block_message_advertises_marker_and_remove_first(privacy_repo: Path):
    res = _stage_and_run(privacy_repo, PRIVATE_KEY)
    assert res.returncode == 1, res.stderr
    # The just-in-time channel: the agent learns the bypass at block-time.
    assert "ai-hats: allow-secret" in res.stderr
    # Marker must not be the reflex — "remove a real secret" leads.
    assert "remove it" in res.stderr


# --- HATS-940: Python decorators must not false-positive as email ------------
# `+@name.attr` kept its diff `+` marker and matched PAT_EMAIL (`+` local-part
# + `name` domain + `.attr` TLD). The marker is now stripped before matching.

PY_DECORATORS = [
    pytest.param('@pytest.fixture(scope="module")', id="pytest-fixture"),
    pytest.param('@pytest.mark.parametrize("x", [1, 2])', id="pytest-parametrize"),
    pytest.param('@app.post("/x")', id="fastapi-route"),
    pytest.param('@click.option("--flag", is_flag=True)', id="click-option"),
]


@pytest.mark.integration
@pytest.mark.parametrize("decorator", PY_DECORATORS)
def test_python_decorator_not_flagged_as_email(privacy_repo: Path, decorator: str):
    res = _stage_and_run(privacy_repo, decorator, filename="deco.py")
    assert res.returncode == 0, f"decorator falsely blocked:\n{res.stderr}"


@pytest.mark.integration
def test_real_email_still_blocks(privacy_repo: Path):
    """Positive control: stripping the diff marker must not blind PAT_EMAIL."""
    res = _stage_and_run(privacy_repo, "owner = 'jane.doe@example.com'")
    assert res.returncode == 1, res.stderr
    assert "email address" in res.stderr


@pytest.mark.integration
def test_env_secret_still_blocks_after_anchor_change(privacy_repo: Path):
    """PAT_ENV lost its `^[+]` anchor when the marker-strip landed; guard that an
    env-style secret on an added line still hard-blocks."""
    res = _stage_and_run(privacy_repo, "API_KEY=supersecretvalue123")
    assert res.returncode == 1, res.stderr
    assert "env-style secret" in res.stderr


# --- HATS-1255: Binary files and non-UTF-8 text files must pass cleanly -------


def _stage_and_run_bytes(repo: Path, content: bytes, *, filename: str = "leak.bin"):
    """Write binary `content`, stage it, then run the privacy hook from the repo root."""
    (repo / filename).write_bytes(content)
    subprocess.run(["git", "add", filename], cwd=str(repo), check=True)
    env = os.environ.copy()
    env.pop("AI_HATS_PRIVACY_ACK", None)
    return subprocess.run(
        ["bash", str(PRIVACY_HOOK)],
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )


@pytest.mark.integration
def test_binary_png_file_allowed_without_warnings(privacy_repo: Path):
    """PNG image header + null bytes must pass silently with returncode 0 and empty stderr."""
    png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4"
    res = _stage_and_run_bytes(privacy_repo, png_bytes, filename="screenshot.png")
    assert res.returncode == 0, f"binary file falsely blocked:\n{res.stderr}"
    assert "warning:" not in res.stderr
    assert "illegal byte sequence" not in res.stderr
    assert res.stderr == ""


@pytest.mark.integration
def test_non_utf8_text_file_handled_cleanly(privacy_repo: Path):
    """Non-UTF-8 text file (CP1251) must be scanned without sed illegal byte sequence errors."""
    cp1251_bytes = "Привет мир".encode("cp1251")
    res = _stage_and_run_bytes(privacy_repo, cp1251_bytes, filename="doc_cp1251.txt")
    assert res.returncode == 0, f"non-utf8 text file caused error:\n{res.stderr}"
    assert "illegal byte sequence" not in res.stderr
    assert res.stderr == ""
