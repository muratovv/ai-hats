from __future__ import annotations

import asyncio
import sys

import anyio
import pytest

from ai_hats.consent_wrapper import WrapperConfig
from ai_hats.session_identity import SessionIdentity
from ai_hats.consent_mcp import server as consent_server
from ai_hats_library.hooks import consent_ticket


@pytest.fixture
def binding(tmp_path):
    identity = SessionIdentity(
        id="test",
        role="assistant",
        provider="codex",
        project_dir=tmp_path,
        session_dir=tmp_path,
        skills_root=str(tmp_path),
        session_cache_dir=str(tmp_path),
    )
    return consent_server.Binding(
        identity,
        WrapperConfig(tmp_path, {}, {}),
        tmp_path / "consent-wrapper/config.json",
        "",
        {},
    )


def test_execution_timeout_reports_possible_effects_without_retry(tmp_path, binding):
    wrapper = tmp_path / "consent-wrapper/bin/rack"
    wrapper.parent.mkdir(parents=True)
    marker = tmp_path / "started"
    wrapper.write_text(
        f"#!{sys.executable}\nfrom pathlib import Path\nimport time\n"
        f"with Path({str(marker)!r}).open('a') as f: f.write('started\\n')\n"
        "time.sleep(30)\n"
    )
    wrapper.chmod(0o755)
    result = asyncio.run(
        consent_server.execute(
            binding,
            ("transition", "TEST-1", "execute"),
            consent_server.Result(request_id="one", task_id="TEST-1", decision="accept"),
            None,
            timeout_s=1,
        )
    )
    assert result.execution == "indeterminate"
    assert result.exit_code is None
    assert marker.read_text().splitlines() == ["started"]


def test_cancellation_consumes_ticket_not_yet_used_by_wrapper(tmp_path, binding, monkeypatch):
    (tmp_path / ".git").mkdir()
    monkeypatch.setenv("AI_HATS_SESSION_ID", "test")
    argv = ("transition", "TEST-1", "execute")
    nonce = consent_ticket.mint("TEST-1", start=tmp_path, session_id="test", argv=argv)
    assert nonce is not None
    ticket = consent_ticket.tickets_dir(tmp_path) / f"{nonce}.json"
    marker = tmp_path / "started"
    wrapper = tmp_path / "consent-wrapper/bin/rack"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text(
        f"#!{sys.executable}\nfrom pathlib import Path\nimport time\n"
        f"Path({str(marker)!r}).touch()\n"
        "time.sleep(60)\n"
    )
    wrapper.chmod(0o755)

    async def scenario():
        async with anyio.create_task_group() as group:

            async def run():
                await consent_server.execute(
                    binding,
                    argv,
                    consent_server.Result(request_id="one", task_id="TEST-1", decision="accept"),
                    nonce,
                    timeout_s=30,
                )

            group.start_soon(run)
            with anyio.fail_after(5):
                while not marker.exists():
                    await anyio.sleep(0.01)
            assert ticket.is_file()
            group.cancel_scope.cancel()

    anyio.run(scenario)
    assert not ticket.exists()
