"""``--dry-run`` reports the session the launch would deliver — asserted, not claimed.

``tests/sessions/test_dry_run_matches_session.py`` promises this in its filename
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

import dataclasses
import json
import re
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from ai_hats.assembler import Assembler
from ai_hats.cli import main
from ai_hats.dry_run import AT_LAUNCH, DRY_RUN_SESSION_ID, dry_run_automate, dry_run_hitl
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
        lambda _self, cmd, env, tracer, pty_tap_factory=None, on_spawn=None: (
            sink.update(env=dict(env)) or 0
        ),
    )
    monkeypatch.setenv("AI_HATS_QUIET", "1")

    result = CliRunner().invoke(main, [])
    assert result.exit_code == 0, result.output

    runs = project / ".agent" / "ai-hats" / "sessions" / "runs"
    (sdir,) = [d for d in runs.iterdir() if d.name.startswith("session_")]
    payload = json.loads((sdir / ROLE_MATERIALIZATION_JSON).read_text())
    payload["_child_env"] = sink["env"]
    payload["_sid"] = sdir.name[len("session_") :]
    return payload


def _sid_of(launched: dict) -> str:
    """The sid of the run dir this launch actually wrote, carried explicitly.

    It used to be the first ``session_<sid>`` match in ``json.dumps(launched)``,
    which includes ``_child_env`` — the real launch environment. A session
    started from inside another ai-hats session inherits that parent's
    ``AI_HATS_SESSION_IDENTITY`` and ``TRACE_LOG_PATH``, both carrying the OUTER
    sid, so the fold rewrote the wrong id and the comparison failed. The record
    itself never carries one, which is why the search reached the env at all.
    """  # comment-length: allow — the flake is invisible outside an ai-hats run
    sid = launched.get("_sid", "")
    assert sid, "the launcher must record which run dir it wrote"
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
    d.pop("_sid", None)
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


# AUTOMATE (HATS-1552): the HITL half above shares its launch assembly between
# report and launch; the sub-agent path shared none of it, hence everything below.

TICKET = "HATS-0001"
TASK_TEXT = "ship the thing"


def _write_ticket(project: Path) -> str:
    """A card on disk is what makes ``TICKET_CONTEXT`` non-empty — the section the
    dry-run drops together with the ``ticket_id`` that selects it."""
    from ai_hats.paths import tasks_dir

    card = tasks_dir(project) / TICKET
    card.mkdir(parents=True, exist_ok=True)
    (card / "task.yaml").write_text(f"id: {TICKET}\ntitle: the card the sub-agent is handed\n")
    return TICKET


def _automate_for_real(monkeypatch, project: Path) -> dict:
    """Drive the real sub-agent runner with only the SDK call excepted.

    Everything up to ``run_claude_sdk_blocking`` runs for real, so the captured
    options are the ones a sub-agent would have been launched with — not a
    reconstruction the test agrees with by construction.
    """
    from ai_hats.composition_seam import build_composition_payload
    from ai_hats.paths import runs_dir
    from ai_hats.subagent_runner import SubAgentRunner
    from ai_hats.surfaces.claude.sdk_runner import SdkRunResult
    from ai_hats_observe import SessionManager

    seen: dict[str, Any] = {}

    def _capture(options, initial_message, *, timeout_s):  # noqa: ARG001
        seen["options"] = options
        seen["initial_message"] = initial_message
        return SdkRunResult(
            exit_code=0,
            stdout="",
            stderr="",
            claude_session_id=None,
            total_cost_usd=None,
            num_turns=None,
            stop_reason=None,
            timed_out=False,
            error=None,
        )

    monkeypatch.setattr(
        "ai_hats.surfaces.claude.sdk_runner.run_claude_sdk_blocking",
        _capture,
    )

    payload = build_composition_payload(project, role_override="maintainer")
    session_mgr = SessionManager(project, runs_dir=runs_dir(project))
    session = SubAgentRunner(project, payload, session_mgr=session_mgr).run(
        task=TASK_TEXT,
        ticket_id=TICKET,
        isolation_mode="none",
    )
    seen["record"] = json.loads(Path(session.role_materialization_path).read_text())
    seen["meta_prompt"] = Path(session.meta_prompt_path).read_text()
    seen["sid"] = session.session_id
    return seen


def _planned_automate(project: Path):
    return dry_run_automate(project, role="maintainer", task=TASK_TEXT, ticket_id=TICKET)


def test_the_automate_dry_run_payload_equals_the_launch_record(project: Path, monkeypatch):
    """Same claim as the HITL case, on the path that shares no assembly with it."""
    _write_ticket(project)
    planned = _planned_automate(project).to_dict()
    real = _automate_for_real(monkeypatch, project)

    assert _comparable(planned, real["sid"]) == _comparable(real["record"], real["sid"])


def test_the_automate_dry_run_reports_the_prompt_the_sub_agent_receives(project: Path, monkeypatch):
    """The meta-prompt is the sub-agent's whole world and lives outside ``to_dict``.

    Byte equality against ``meta_prompt.txt`` is the only assertion that can see
    a dry-run building its prompt with a second, tidier function than the launch.
    """
    _write_ticket(project)
    planned = _planned_automate(project)
    real = _automate_for_real(monkeypatch, project)

    assert planned.prompt_text, "the report must carry the prompt the sub-agent is given"
    assert planned.prompt_text == _normalize(real["meta_prompt"], real["sid"])


def test_the_automate_meta_prompt_is_what_the_sdk_was_actually_sent(project: Path, monkeypatch):
    """``meta_prompt.txt`` is an audit artifact — so audit it against the SDK call.

    Byte equality between the report and the record is worth nothing if both
    describe a first user message the SDK never received.
    """
    _write_ticket(project)
    real = _automate_for_real(monkeypatch, project)

    assert real["initial_message"] in real["meta_prompt"], (
        "the saved audit names a first user message the SDK was never sent"
    )


def test_the_reported_automate_env_is_the_environment_the_sub_agent_receives(
    project: Path, monkeypatch
):
    """``env_keys`` is a claim about a real child — so check the real child.

    The HITL sibling of this test is what makes the equality above meaningful
    rather than two copies of the same omission (HATS-1548 R8).
    """
    real = _automate_for_real(monkeypatch, project)
    delivered = dict(real["options"].env or {})

    assert set(real["record"]["env_keys"]) == set(delivered), (
        "the record and the SDK disagree about what the sub-agent's environment is"
    )


def test_a_cli_surface_executes_the_argv_it_reported(tmp_path: Path, monkeypatch):
    """The record's ``launch`` is the argv, not a third derivation of it.

    The runner used to re-assemble the command from ``materialize_runtime_skills``
    at spawn time and agreed with its own record only by coincidence — for cline
    because that call rebuilds the same args, for agy because it returns none.
    Coincidence is not a property, so the decoy below makes the two derivations
    disagree: without it, this test passes against the code it was written for.
    """  # comment-length: allow — why the decoy exists is the point of the test
    import subprocess

    from ai_hats_cline import ClineProvider

    monkeypatch.setattr(
        ClineProvider,
        "materialize_runtime_skills",
        lambda *a, **k: ["--config", "/decoy-from-the-second-derivation"],
    )

    proj = tmp_path / "proj"
    proj.mkdir()
    ProjectConfig(
        provider="cline",
        library_paths=[str(LIBRARY_DIR)],
        ai_hats_dir=".agent/ai-hats",
        active_role="maintainer",
        default_role="maintainer",
    ).save(proj / PROJECT_CONFIG)
    asm = Assembler(proj, library_paths=[LIBRARY_DIR])
    asm.init()
    asm.set_role("maintainer", provider_name="cline")
    monkeypatch.chdir(proj)
    monkeypatch.setenv("AI_HATS_NO_UPDATE_CHECK", "1")

    from ai_hats.composition_seam import build_composition_payload
    from ai_hats.paths import runs_dir
    from ai_hats.subagent_runner import SubAgentRunner
    from ai_hats_observe import SessionManager

    spawned: dict[str, Any] = {}

    def _capture(launch, **kwargs):
        spawned["cmd"] = list(launch)
        return subprocess.CompletedProcess(list(launch), 0, stdout="", stderr="")

    # The spawn seam, not ``subprocess.run``: patching the stdlib name reaches
    # the whole process, so the anchor's own ``ps`` (HATS-1339 D3) ran later and
    # overwrote the capture with its argv.
    monkeypatch.setattr("ai_hats.subagent_runner._run_surface", _capture)

    payload = build_composition_payload(proj, role_override="maintainer")
    session = SubAgentRunner(
        proj, payload, session_mgr=SessionManager(proj, runs_dir=runs_dir(proj))
    ).run(task=TASK_TEXT, isolation_mode="none")

    record = json.loads(Path(session.role_materialization_path).read_text())
    assert spawned["cmd"], "the surface never reached the spawn"
    assert record["launch"] == spawned["cmd"]


def test_the_reported_automate_launch_is_the_options_the_sdk_receives(project: Path, monkeypatch):
    """Every option ai-hats sets on the SDK is named in the report.

    Measured against the SDK's own defaults, so the set is what ai-hats CHANGED
    rather than every field the dataclass happens to carry. ``cwd`` is excepted
    by construction: the record is written before the worktree exists and
    carries it in its own field as a sentinel.
    """  # comment-length: allow — the diff-against-defaults is the whole trick
    from claude_agent_sdk import ClaudeAgentOptions

    real = _automate_for_real(monkeypatch, project)
    reported = {token.split("=", 1)[0] for token in real["record"]["launch"]}
    stock = ClaudeAgentOptions()
    delivered = {
        f.name
        for f in dataclasses.fields(real["options"])
        if getattr(real["options"], f.name) != getattr(stock, f.name)
    } - {"cwd"}

    assert delivered <= reported, (
        f"the SDK gets options the record never names: {sorted(delivered - reported)}"
    )
