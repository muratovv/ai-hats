"""``--dry-run`` reports the session the launch would deliver — asserted, not claimed.

``tests/pipeline/test_dry_run_matches_session.py`` promises this in its filename
and proves something narrower: that a dry-run writes nothing. Nothing anywhere
compared a dry-run payload against a real session's, even though both sides
build the same :class:`SessionReport` and the launch already persists its own to
``role_materialization.json`` (HATS-1216).

What this is worth: the artifact build IS shared code (one
``build_session_artifacts`` with a swapped port), so agreement there is cheap.
The load-bearing part is everything NOT shared — a different composition entry
point (``build_preview_payload`` against ``build_composition_payload``), a
different policy source, a fixed session id, and a launch that skips the runner's
post-build phase entirely. That surface had no coverage at all.

What it does NOT catch, stated so nobody reads a green run as more than it is:
dropping a field from ``SessionReport`` keeps this green, because both sides lose
it together. The bindings section has its own inversion in
``tests/e2e/test_check_mirror_dry_run.py``.
"""  # comment-length: allow — a test that proves less than its name must say so

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from ai_hats.assembler import Assembler
from ai_hats.cli import main
from ai_hats.dry_run import AT_LAUNCH, DRY_RUN_SESSION_ID, dry_run_hitl
from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG
from ai_hats_observe.artifacts import ROLE_MATERIALIZATION_JSON

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LIBRARY_DIR = REPO_ROOT / "packages" / "ai-hats-library" / "src" / "ai_hats_library"

#: Files whose CONTENT embeds the session id, so their digest cannot match
#: across two sessions by construction: claude bakes absolute hook commands into
#: settings.json, rooted at the sid-keyed skills dir. The goldens dodge this by
#: shipping a fixture skill with no hook script — this fixture is the real
#: `maintainer` role precisely so the case is exercised rather than avoided.
SID_IN_CONTENT = {"settings.json", "hooks.json"}

_SESSION_DIR = re.compile(r"session_(\d{8}-\d{6}-\d+-\d+)")


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    """The real ``maintainer`` role: it ships runtime-hook scripts AND binds a
    check, so neither the sid-in-content case nor the gates section is dodged."""
    proj = tmp_path / "proj"
    proj.mkdir()
    ProjectConfig(
        provider="claude",
        library_paths=[str(LIBRARY_DIR)],
        ai_hats_dir=".agent/ai-hats",
        active_role="maintainer",
        default_role="maintainer",
    ).save(proj / PROJECT_CONFIG)
    asm = Assembler(proj, library_paths=[LIBRARY_DIR])
    asm.init()
    asm.set_role("maintainer", provider_name="claude")
    monkeypatch.chdir(proj)
    monkeypatch.setenv("AI_HATS_NO_UPDATE_CHECK", "1")
    return proj


def _launch_for_real(monkeypatch, project: Path) -> dict:
    """Drive the real HITL runner, spawn excepted, and read its launch record."""
    from ai_hats import runtime as rt

    sink: dict[str, Any] = {}
    monkeypatch.setattr(
        rt.WrapRunner,
        "_pty_spawn",
        lambda _self, cmd, env, tracer, pty_tap_factory=None: sink.update(env=dict(env)) or 0,
    )
    monkeypatch.setenv("AI_HATS_QUIET", "1")

    result = CliRunner().invoke(main, [])
    assert result.exit_code == 0, result.output

    runs = project / ".agent" / "ai-hats" / "sessions" / "runs"
    (sdir,) = [d for d in runs.iterdir() if d.name.startswith("session_")]
    payload = json.loads((sdir / ROLE_MATERIALIZATION_JSON).read_text())
    payload["_child_env"] = sink["env"]
    return payload


def _sid_of(launched: dict) -> str:
    (sid,) = _SESSION_DIR.findall(json.dumps(launched))[:1] or [""]
    assert sid, "the launch record must carry its own session id somewhere"
    return sid


def _normalize(obj, sid: str):
    """Rewrite the two values only a launch can mint: its id, and the provider's."""
    if isinstance(obj, str):
        return obj.replace(sid, DRY_RUN_SESSION_ID)
    if isinstance(obj, list):
        return [_normalize(o, sid) for o in obj]
    if isinstance(obj, dict):
        return {k: _normalize(v, sid) for k, v in obj.items()}
    return obj


def _strip_provider_session_id(launch: list[str]) -> list[str]:
    """claude puts a fresh uuid on the argv; the dry-run says so instead."""
    out = list(launch)
    for i, token in enumerate(out[:-1]):
        if token == "--session-id":
            out[i + 1] = AT_LAUNCH
    return out


def _comparable(payload: dict, sid: str) -> dict:
    d = _normalize(payload, sid)
    d.pop("_child_env", None)
    d["launch"] = _strip_provider_session_id(d["launch"])
    for entry in d["materialized"]:
        if Path(entry["target"]).name in SID_IN_CONTENT:
            entry["digest"] = "<sid-dependent content>"
            entry["size"] = "<sid-dependent content>"
    return d


def test_the_dry_run_payload_equals_the_launch_record(project: Path, monkeypatch):
    """Field for field, once the launch-minted values are folded away."""
    planned = dry_run_hitl(project).to_dict()
    launched = _launch_for_real(monkeypatch, project)

    sid = _sid_of(launched)
    assert _comparable(planned, sid) == _comparable(launched, sid)


def test_the_reported_env_is_the_environment_the_child_receives(project: Path, monkeypatch):
    """``env_keys`` is a claim about a real process — so check the real process.

    The record is asserted to be exactly the inherited environment plus what the
    launch adds, which is what makes the equality above meaningful rather than
    two copies of the same omission (HATS-1548 R8).
    """
    import os

    launched = _launch_for_real(monkeypatch, project)
    child_env = launched.pop("_child_env")

    added = set(launched["env_keys"])
    assert added <= set(child_env), "the record names a key the child never gets"
    assert set(child_env) - added <= set(os.environ), "an added key is missing from the record"


def test_the_gates_survive_the_round_trip(project: Path, monkeypatch):
    """The section HATS-1548 added is the one an operator reads before starting.

    Named separately because the equality above cannot fail on it: drop the
    section and both payloads lose it together.
    """
    planned = dry_run_hitl(project).to_dict()
    launched = _launch_for_real(monkeypatch, project)

    assert planned["checks"], "the maintainer role binds a gate — the fixture must show it"
    assert planned["checks"] == _normalize(launched["checks"], _sid_of(launched))
