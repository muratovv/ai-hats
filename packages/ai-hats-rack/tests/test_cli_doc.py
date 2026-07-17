"""Doc-store CLI surface: transition --freeze/--rm ops + the context
Documents block + default root resolution (HATS-1021/1030/1031)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from ai_hats_rack.cli import main


@pytest.fixture
def runner():
    return CliRunner()


def _tasks_args(tmp_path):
    return ["--tasks-dir", str(tmp_path / "tasks")]


def _setup_task(runner, tmp_path, *files: tuple[str, bytes]) -> Path:
    result = runner.invoke(main, ["create", "doc host", *_tasks_args(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    card_dir = tmp_path / "tasks" / "HATS-001"
    for name, data in files:
        path = card_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return card_dir


# ----- frozen drift on the read surface (doc ls folded into context) ----------


def test_context_surfaces_frozen_drift(runner, tmp_path):
    # `doc ls` is gone (HATS-1029), `show` too (HATS-1031); `context` runs the
    # same live pin verification and marks a drifted frozen doc. The read
    # surface flags drift, it does not fail on it — transitions do (Р13 guard).
    card_dir = _setup_task(runner, tmp_path, ("evidence.log", b"v1"))
    freeze = runner.invoke(
        main, ["transition", "HATS-001", "--freeze", "evidence.log", *_tasks_args(tmp_path)]
    )
    assert freeze.exit_code == 0, freeze.output
    (card_dir / "evidence.log").write_bytes(b"v2")

    human = runner.invoke(main, ["context", "HATS-001", *_tasks_args(tmp_path)])
    assert human.exit_code == 0, human.output
    assert "frozen ✗ modified" in human.output

    as_json = runner.invoke(main, ["context", "HATS-001", *_tasks_args(tmp_path), "--json"])
    doc = next(d for d in json.loads(as_json.output)["documents"] if d["name"] == "evidence.log")
    assert doc["drift"] == "modified"


# ----- freeze / rm (absorbed into `transition --freeze/--rm`, HATS-1030) -------


def test_freeze_via_transition_and_drift_refusal(runner, tmp_path):
    card_dir = _setup_task(runner, tmp_path, ("evidence.log", b"v1"))
    result = runner.invoke(
        main, ["transition", "HATS-001", "--freeze", "evidence.log", *_tasks_args(tmp_path), "--json"]
    )
    assert result.exit_code == 0, result.output
    (op,) = json.loads(result.output)["ops"]
    assert op["op"] == "freeze" and op["digest"].startswith("sha256:") and op["changed"] is True

    # Re-freezing drifted content without acknowledgement stays refused; the
    # --ack-frozen hatch accepts the new content (HATS-1031 Р13 recipe).
    (card_dir / "evidence.log").write_bytes(b"v2")
    refused = runner.invoke(
        main, ["transition", "HATS-001", "--freeze", "evidence.log", *_tasks_args(tmp_path), "--json"]
    )
    assert refused.exit_code == 1
    error = json.loads(refused.output)["error"]
    assert error["code"] == "frozen_pin_drift"

    accepted = runner.invoke(
        main,
        [
            "transition", "HATS-001", "--freeze", "evidence.log",
            "--ack-frozen", *_tasks_args(tmp_path), "--json",
        ],
    )
    assert accepted.exit_code == 0, accepted.output
    (op2,) = json.loads(accepted.output)["ops"]
    assert op2["changed"] is True and op2["digest"] != op["digest"]


def test_rm_frozen_refusal_names_the_flag_then_succeeds_with_ack(runner, tmp_path):
    _setup_task(runner, tmp_path, ("evidence.log", b"v1"))
    runner.invoke(main, ["transition", "HATS-001", "--freeze", "evidence.log", *_tasks_args(tmp_path)])
    refused = runner.invoke(
        main, ["transition", "HATS-001", "--rm", "evidence.log", *_tasks_args(tmp_path), "--json"]
    )
    assert refused.exit_code == 1
    error = json.loads(refused.output)["error"]
    assert error["code"] == "frozen_document"
    assert "--ack-frozen" in error["message"]

    removed = runner.invoke(
        main,
        [
            "transition", "HATS-001", "--rm", "evidence.log",
            "--ack-frozen", *_tasks_args(tmp_path), "--json",
        ],
    )
    assert removed.exit_code == 0, removed.output
    (op,) = json.loads(removed.output)["ops"]
    assert op["op"] == "rm" and op["pin_removed"] is True
    assert op["trashed_to"] is not None
    assert Path(op["trashed_to"]).is_file()  # recoverable, not deleted
    assert op["revert"] == f"rack transition HATS-001 --attach {op['trashed_to']}:evidence.log"


def test_rm_plain_prints_recovery_path(runner, tmp_path):
    _setup_task(runner, tmp_path, ("scratch.log", b"x"))
    result = runner.invoke(
        main, ["transition", "HATS-001", "--rm", "scratch.log", *_tasks_args(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    assert "recoverable:" in result.output
    assert "revert: rack transition HATS-001 --attach" in result.output


def test_invalid_name_is_typed(runner, tmp_path):
    _setup_task(runner, tmp_path)
    result = runner.invoke(
        main, ["transition", "HATS-001", "--rm", "../escape.md", *_tasks_args(tmp_path), "--json"]
    )
    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["code"] == "invalid_document_name"


# ----- context: discovery block ---------------------------------------------------


def test_context_prints_documents_block_with_absolute_paths_no_content(runner, tmp_path):
    card_dir = _setup_task(runner, tmp_path, ("gate.log", b"SECRET-BODY-MARKER"))
    runner.invoke(main, ["transition", "HATS-001", "--freeze", "gate.log", *_tasks_args(tmp_path)])
    result = runner.invoke(main, ["context", "HATS-001", *_tasks_args(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "Documents" in result.output
    assert str((card_dir / "gate.log").absolute()) in result.output
    assert "frozen ✓" in result.output
    # discovery, not injection: file content never rides context without --with
    assert "SECRET-BODY-MARKER" not in result.output


def test_context_json_carries_documents(runner, tmp_path):
    _setup_task(runner, tmp_path, ("gate.log", b"tail"))
    result = runner.invoke(main, ["context", "HATS-001", *_tasks_args(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["task"]["id"] == "HATS-001"
    assert [d["name"] for d in payload["documents"]] == ["gate.log"]
    assert Path(payload["documents"][0]["path"]).is_absolute()


def test_context_without_documents_still_prints_block_header(runner, tmp_path):
    _setup_task(runner, tmp_path)
    result = runner.invoke(main, ["context", "HATS-001", *_tasks_args(tmp_path)])
    assert result.exit_code == 0
    assert "Documents" in result.output and "(none" in result.output


# ----- default root resolution (walk-up replaces the K1 --tasks-dir default) -----


def test_default_resolution_walks_up_and_honors_config(runner, tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path) as fs:
        root = Path(fs)
        (root / "ai-hats.yaml").write_text("ai_hats_dir: .hats\ntask_prefix: SBX\n")
        nested = root / "deep" / "inside"
        nested.mkdir(parents=True)
        os.chdir(nested)
        result = runner.invoke(main, ["create", "from nested", "--json"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["task"]["id"] == "SBX-001"
        card = root / ".hats" / "tracker" / "backlog" / "tasks" / "SBX-001" / "task.yaml"
        assert card.is_file()

        shown = runner.invoke(main, ["context", "SBX-001"])
        assert shown.exit_code == 0, shown.output


def test_no_project_root_is_typed_error_and_creates_nothing(runner, tmp_path):
    with runner.isolated_filesystem(temp_dir=tmp_path) as fs:
        root = Path(fs)
        result = runner.invoke(main, ["create", "orphan", "--json"])
        assert result.exit_code == 1
        error = json.loads(result.output)["error"]
        assert error["code"] == "no_project_root"
        assert "--tasks-dir" in error["message"]
        assert list(root.iterdir()) == []  # HATS-839: no phantom tracker
