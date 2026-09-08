from __future__ import annotations

import asyncio
import sys

from ai_hats.consent_wrapper import WrapperConfig
from ai_hats.session_identity import SessionIdentity
from ai_hats.consent_mcp import server as consent_server


def test_execution_timeout_reports_possible_effects_without_retry(tmp_path):
    wrapper = tmp_path / "consent-wrapper/bin/rack"
    wrapper.parent.mkdir(parents=True)
    marker = tmp_path / "started"
    wrapper.write_text(
        f"#!{sys.executable}\nfrom pathlib import Path\nimport time\n"
        f"with Path({str(marker)!r}).open('a') as f: f.write('started\\n')\n"
        "time.sleep(30)\n"
    )
    wrapper.chmod(0o755)
    identity = SessionIdentity(
        id="test",
        role="assistant",
        provider="codex",
        project_dir=tmp_path,
        session_dir=tmp_path,
        skills_root=str(tmp_path),
        session_cache_dir=str(tmp_path),
    )
    binding = consent_server.Binding(
        identity,
        WrapperConfig(tmp_path, {}, {}),
        tmp_path / "consent-wrapper/config.json",
        "",
        {},
    )
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
