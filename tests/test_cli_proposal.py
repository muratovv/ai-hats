"""Tests for `ai-hats task proposal` CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from ai_hats.cli.proposal import proposal
from ai_hats.paths import proposals_dir


@pytest.fixture
def project_dir(tmp_path: Path, monkeypatch) -> Path:
    pd = tmp_path / "proj"
    (proposals_dir(pd)).mkdir(parents=True)
    monkeypatch.chdir(pd)
    return pd


def _invoke(args):
    return CliRunner().invoke(proposal, args)


def _create(title="t", category="rule", target="dev_rule_x"):
    return _invoke([
        "create",
        "--title", title,
        "--category", category,
        "--target", target,
        "--description", "d",
        "--rationale", "r",
        "--session", "s1",
    ])


def test_create_auto_id(project_dir: Path):
    res = _create()
    assert res.exit_code == 0, res.output
    assert "PROP-001" in res.output
    assert (proposals_dir(project_dir) / "PROP-001.yaml").exists()


def test_create_increments_id(project_dir: Path):
    _create()
    res = _create(title="t2")
    assert res.exit_code == 0
    assert "PROP-002" in res.output


def test_create_duplicate_id_rejected(project_dir: Path, monkeypatch):
    """Duplicate PROP id must exit 1 with a clean error, not a traceback."""
    res1 = _create()
    assert res1.exit_code == 0, res1.output
    # Force next_proposal_id to collide by pre-creating PROP-002 then stubbing the id generator.
    p = proposals_dir(project_dir) / "PROP-001.yaml"
    original = p.read_text()
    monkeypatch.setattr("ai_hats_tracker.cli.proposal.next_proposal_id", lambda _d: "PROP-001")
    res2 = _create(title="duplicate")
    assert res2.exit_code == 1, res2.output
    assert "already exists" in res2.output.lower()
    assert "Traceback" not in res2.output
    assert p.read_text() == original


def test_create_meta_proposal_with_failed_session(project_dir: Path):
    res = _invoke([
        "create",
        "--title", "reflect-session failed",
        "--category", "process",
        "--target", "reflect-session",
        "--description", "d",
        "--rationale", "r",
        "--session", "judge-s1",
        "--failed-session-id", "20260504-120000-1",
    ])
    assert res.exit_code == 0
    p = proposals_dir(project_dir) / "PROP-001.yaml"
    data = yaml.safe_load(p.read_text())
    assert data["failed_session_id"] == "20260504-120000-1"
    assert data["category"] == "process"


def test_vote_increments(project_dir: Path):
    _create()
    res1 = _invoke([
        "vote",
        "--prop", "PROP-001",
        "--session", "s2",
        "--reasoning", "agree",
    ])
    assert res1.exit_code == 0
    res2 = _invoke([
        "vote",
        "--prop", "PROP-001",
        "--session", "s3",
        "--reasoning", "yes",
    ])
    assert res2.exit_code == 0
    p = proposals_dir(project_dir) / "PROP-001.yaml"
    data = yaml.safe_load(p.read_text())
    assert len(data["votes"]) == 2


def test_vote_unknown_fails(project_dir: Path):
    res = _invoke([
        "vote",
        "--prop", "PROP-999",
        "--session", "s",
        "--reasoning", "x",
    ])
    assert res.exit_code != 0


def test_status_change(project_dir: Path):
    _create()
    res = _invoke(["status", "--prop", "PROP-001", "--status", "accepted"])
    assert res.exit_code == 0
    p = proposals_dir(project_dir) / "PROP-001.yaml"
    assert yaml.safe_load(p.read_text())["status"] == "accepted"


def test_list_filter_by_status(project_dir: Path):
    _create(title="open1")
    _create(title="open2")
    _invoke(["status", "--prop", "PROP-001", "--status", "accepted"])
    res = _invoke(["list", "--status", "open", "--json"])
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    assert [d["id"] for d in data] == ["PROP-002"]


def test_list_filter_by_category(project_dir: Path):
    _create(category="rule", target="rule_a")
    _create(category="code", target="x.py")
    res = _invoke(["list", "--category", "code", "--json"])
    assert res.exit_code == 0
    data = json.loads(res.output)
    assert [d["id"] for d in data] == ["PROP-002"]


def test_show(project_dir: Path):
    _create()
    res = _invoke(["show", "PROP-001"])
    assert res.exit_code == 0
    assert "PROP-001" in res.output


def test_show_unknown(project_dir: Path):
    res = _invoke(["show", "PROP-999"])
    assert res.exit_code != 0
