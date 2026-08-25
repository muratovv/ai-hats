"""The live-agy availability fixture rejects unusable provider sessions."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

_CONFTEST = Path(__file__).resolve().parent.parent / "e2e" / "conftest.py"
_spec = importlib.util.spec_from_file_location("e2e_conftest_agy_probe", _CONFTEST)
_conftest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_conftest)


def _invoke_probe(*outcomes) -> tuple[str | None, list[list[str]]]:
    results = iter(outcomes)
    calls = []

    def run(args, **_kwargs):
        calls.append(args)
        outcome = next(results)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    return _conftest._agy_execution_probe("/usr/local/bin/agy", run), calls


def test_quota_refusal_reports_provider_diagnostic() -> None:
    quota = subprocess.CompletedProcess(
        ["agy", "--output-format", "json", "-p", "Reply OK"],
        1,
        '{"status":"ERROR","error":"Individual quota reached. Resets in 7h."}',
        "",
    )

    unavailable, _calls = _invoke_probe(quota)

    assert unavailable == (
        "agy auth/execution probe failed: Individual quota reached. Resets in 7h."
    )


def test_non_json_refusal_is_bounded() -> None:
    refusal = subprocess.CompletedProcess(
        ["agy", "--output-format", "json", "-p", "Reply OK"],
        1,
        "x" * 1000 + "provider unavailable",
        "",
    )

    unavailable, _calls = _invoke_probe(refusal)

    assert unavailable is not None
    assert unavailable.endswith("provider unavailable")
    assert len(unavailable) <= len("agy auth/execution probe failed: ") + 300


def test_successful_turn_allows_live_tests() -> None:
    success = subprocess.CompletedProcess(
        ["agy", "--output-format", "json", "-p", "Reply OK"],
        0,
        '{"status":"SUCCESS","response":"OK"}',
        "",
    )

    unavailable, calls = _invoke_probe(success)

    assert unavailable is None
    assert _conftest.requires_agy_auth._fixture_function_marker.scope == "session"
    assert calls[0] == [
        "/usr/local/bin/agy",
        "--output-format",
        "json",
        "--print-timeout",
        "15s",
        "-p",
        "Reply OK",
    ]


def test_execution_timeout_reports_unavailable_instead_of_hanging() -> None:
    timeout = subprocess.TimeoutExpired(
        ["agy", "--output-format", "json", "-p", "Reply OK"],
        20,
    )

    unavailable, _calls = _invoke_probe(timeout)

    assert unavailable is not None
    assert "agy execution probe failed" in unavailable


def test_execution_os_error_reports_diagnostic() -> None:
    unavailable, _calls = _invoke_probe(OSError("permission denied"))

    assert unavailable is not None
    assert "permission denied" in unavailable
