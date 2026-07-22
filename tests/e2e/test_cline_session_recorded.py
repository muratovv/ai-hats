"""E2E: cline session → audit.md turn markers + usage.json tokens (HATS-1087).

Fail-under-revert: drop ``ClineProvider.resolve_transcript`` → audit.md is
the meta-only stub (no 👤/👾), usage.json has zero tokens.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ai_hats_observe.artifacts import AUDIT_MD, USAGE_JSON

from _helpers.env import checkout_pythonpath
from _helpers.project import Project

pytestmark = pytest.mark.integration

_CLINE_PKG = "packages/surfaces/cline"


def _has_cline_plugin() -> bool:
    """The cline surface plugin must be importable (editable install or src on path)."""
    import importlib

    try:
        importlib.import_module("ai_hats_cline")
        return True
    except ImportError:
        return False


def test_cline_session_records_audit_and_usage(
    tmp_project: Project,
    requires_cline_auth,
    repo_root: Path,
    tmp_path: Path,
) -> None:
    """Real cline session → audit.md with turn markers + usage.json with tokens."""

    if not _has_cline_plugin():
        pytest.skip("ai-hats-cline plugin not installed in this venv")

    # The shim runs the dev venv's ai_hats; PYTHONPATH points both it and the
    # cline surface at THIS checkout, else the subprocess tests the installed one.
    checkout_env = {
        "PYTHONPATH": os.pathsep.join(
            [checkout_pythonpath(repo_root), str(repo_root / _CLINE_PKG / "src")]
        )
    }

    # 1. self init configures cline provider
    tmp_project.run(
        "self", "init", "-r", "assistant", "-p", "cline", "--no-update",
        timeout=120, extra_env=checkout_env,
    ).expect_ok().expect_stdout_contains(
        "Provider: cline",
    )

    # 2. execute --batch runs a real headless cline session (--yolo --json)
    result = tmp_project.run(
        "execute", "--batch",
        "-r", "assistant", "-p", "cline",
        "--prompt", "Reply with exactly: OK. No other text.",
        "--json",
        timeout=120, extra_env=checkout_env,
    ).expect_ok()

    data = json.loads(result.stdout.strip().splitlines()[-1])
    assert data["exit_code"] == 0, data
    session_dir = Path(data["session_dir"])

    # 3. audit.md exists with real turn markers (absent when resolve_transcript → None).
    audit_path = session_dir / AUDIT_MD
    assert audit_path.exists(), f"audit.md missing: {audit_path}"
    audit = audit_path.read_text()
    assert "- **Provider**: cline" in audit, (
        f"audit.md missing provider marker\naudit:\n{audit}"
    )
    assert "👤" in audit, (
        f"audit.md has no 👤 turn markers — transcript not parsed "
        f"(resolve_transcript may be missing). audit:\n{audit}"
    )

    # 4. usage.json has non-zero token metrics from cline's metrics block.
    usage_path = session_dir / USAGE_JSON
    if usage_path.exists():
        usage = json.loads(usage_path.read_text())
        totals = usage.get("usage_totals", {})
        input_tokens = totals.get("input_tokens", 0)
        output_tokens = totals.get("output_tokens", 0)
        assert (input_tokens + output_tokens) > 0, (
            f"usage.json has zero tokens — transcript not parsed. usage:\n{usage}"
        )
