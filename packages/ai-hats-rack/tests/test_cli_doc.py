"""``rack doc`` + show Documents block + default root resolution (HATS-1021)."""

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


# ----- doc ls -----------------------------------------------------------------


def test_ls_json_lists_direct_writes_with_absolute_paths(runner, tmp_path):
    card_dir = _setup_task(runner, tmp_path, ("gate.log", b"tail"), ("plan.md", b"# p"))
    result = runner.invoke(main, ["doc", "ls", "HATS-001", *_tasks_args(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert [d["name"] for d in payload["documents"]] == ["gate.log", "plan.md"]
    for entry in payload["documents"]:
        assert Path(entry["path"]).is_absolute()
        assert entry["path"] == str((card_dir / entry["name"]).absolute())
        assert entry["digest"].startswith("sha256:")
        assert entry["mtime"].endswith("Z")
        assert entry["frozen"] is False and entry["drift"] is None
    assert payload["drifted"] == []


def test_ls_human_prints_name_path_mtime_digest(runner, tmp_path):
    card_dir = _setup_task(runner, tmp_path, ("gate.log", b"tail"))
    result = runner.invoke(main, ["doc", "ls", "HATS-001", *_tasks_args(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "gate.log" in result.output
    assert str((card_dir / "gate.log").absolute()) in result.output
    assert "sha256:" in result.output


def test_ls_empty_names_the_directory_to_write_into(runner, tmp_path):
    card_dir = _setup_task(runner, tmp_path)
    result = runner.invoke(main, ["doc", "ls", "HATS-001", *_tasks_args(tmp_path)])
    assert result.exit_code == 0
    assert str(card_dir.absolute()) in result.output


def test_ls_unknown_task_is_typed(runner, tmp_path):
    _setup_task(runner, tmp_path)
    result = runner.invoke(main, ["doc", "ls", "HATS-404", *_tasks_args(tmp_path), "--json"])
    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["code"] == "unknown_task"


def test_ls_fails_loudly_on_frozen_drift(runner, tmp_path):
    card_dir = _setup_task(runner, tmp_path, ("evidence.log", b"v1"))
    freeze = runner.invoke(
        main, ["transition", "HATS-001", "--freeze", "evidence.log", *_tasks_args(tmp_path)]
    )
    assert freeze.exit_code == 0, freeze.output
    (card_dir / "evidence.log").write_bytes(b"v2")
    result = runner.invoke(main, ["doc", "ls", "HATS-001", *_tasks_args(tmp_path)])
    assert result.exit_code == 1
    assert "frozen ✗ modified" in result.output
    assert "--refreeze" in result.output or "re-pin" in result.output

    as_json = runner.invoke(main, ["doc", "ls", "HATS-001", *_tasks_args(tmp_path), "--json"])
    assert as_json.exit_code == 1
    payload = json.loads(as_json.output)
    assert payload["drifted"] == ["evidence.log"]
    assert payload["documents"][0]["drift"] == "modified"


# ----- freeze / rm (absorbed into `transition --freeze/--rm`, HATS-1030) -------


def test_freeze_via_transition_and_drift_refusal(runner, tmp_path):
    card_dir = _setup_task(runner, tmp_path, ("evidence.log", b"v1"))
    result = runner.invoke(
        main, ["transition", "HATS-001", "--freeze", "evidence.log", *_tasks_args(tmp_path), "--json"]
    )
    assert result.exit_code == 0, result.output
    (op,) = json.loads(result.output)["ops"]
    assert op["op"] == "freeze" and op["digest"].startswith("sha256:") and op["changed"] is True

    # Re-freezing drifted content in the composite is refused (no --refreeze op);
    # deliberate re-pinning stays available via the DocStore API (test_docstore).
    (card_dir / "evidence.log").write_bytes(b"v2")
    refused = runner.invoke(
        main, ["transition", "HATS-001", "--freeze", "evidence.log", *_tasks_args(tmp_path), "--json"]
    )
    assert refused.exit_code == 1
    error = json.loads(refused.output)["error"]
    assert error["code"] == "frozen_pin_drift"


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


# ----- show: discovery block ----------------------------------------------------


def test_show_prints_documents_block_with_absolute_paths_no_content(runner, tmp_path):
    card_dir = _setup_task(runner, tmp_path, ("gate.log", b"SECRET-BODY-MARKER"))
    runner.invoke(main, ["transition", "HATS-001", "--freeze", "gate.log", *_tasks_args(tmp_path)])
    result = runner.invoke(main, ["show", "HATS-001", *_tasks_args(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "Documents" in result.output
    assert str((card_dir / "gate.log").absolute()) in result.output
    assert "frozen ✓" in result.output
    # discovery, not injection: file content never rides show output
    assert "SECRET-BODY-MARKER" not in result.output


def test_show_json_carries_documents(runner, tmp_path):
    _setup_task(runner, tmp_path, ("gate.log", b"tail"))
    result = runner.invoke(main, ["show", "HATS-001", *_tasks_args(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["task"]["id"] == "HATS-001"
    assert [d["name"] for d in payload["documents"]] == ["gate.log"]
    assert Path(payload["documents"][0]["path"]).is_absolute()


def test_show_without_documents_still_prints_block_header(runner, tmp_path):
    _setup_task(runner, tmp_path)
    result = runner.invoke(main, ["show", "HATS-001", *_tasks_args(tmp_path)])
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

        shown = runner.invoke(main, ["show", "SBX-001"])
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
