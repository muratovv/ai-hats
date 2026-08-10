"""The dry-run report: a rendering of the record, never a second derivation."""

from __future__ import annotations

import json
from pathlib import Path

from ai_hats.materialization import PlanMaterializer
from ai_hats.session_artifacts import SessionPolicy
from ai_hats.session_report import SessionReport


def _report(tmp_path: Path) -> SessionReport:
    port = PlanMaterializer()
    port.mkdir(tmp_path / "cache")
    port.write_text(tmp_path / "cache" / "prompt.md", "role text")
    return SessionReport(
        role="maintainer",
        provider="agy",
        run_mode="hitl",
        policy=SessionPolicy(),
        launch=["agy", "--add-dir", str(tmp_path / "cache" / "rules")],
        env={"AI_HATS_DIR": "/secret/path", "AI_HATS_PYTHON": "/venv/bin/python"},
        prompt=tmp_path / "cache" / "prompt.md",
        plan=port.plan,
    )


def test_json_payload_carries_the_launch_and_the_record(tmp_path: Path):
    payload = _report(tmp_path).to_dict()

    assert payload["role"] == "maintainer"
    assert payload["run_mode"] == "hitl"
    assert payload["launch"][0] == "agy"
    assert payload["policy"] == {"context": True, "hooks": True, "settings": True}
    kinds = [e["kind"] for e in payload["materialized"]]
    assert kinds == ["mkdir", "write_text"]
    assert payload["materialized"][1]["size"] == 9


def test_env_values_never_appear_in_either_rendering(tmp_path: Path):
    """R5: env carries secrets — only key names may be reported."""
    report = _report(tmp_path)

    as_json = json.dumps(report.to_dict())
    as_text = report.render()

    assert "AI_HATS_DIR" in as_json and "AI_HATS_DIR" in as_text
    assert "/secret/path" not in as_json
    assert "/secret/path" not in as_text
    assert "/venv/bin/python" not in as_json
    assert "/venv/bin/python" not in as_text


def test_text_rendering_shows_launch_and_materialized_paths(tmp_path: Path):
    text = _report(tmp_path).render()

    assert "agy --add-dir" in text
    assert "prompt.md" in text
    assert "maintainer" in text


def test_duplicate_materialization_is_surfaced(tmp_path: Path):
    """R7 signal (b) must reach the human, not just the assertion."""
    port = PlanMaterializer()
    port.write_text(tmp_path / "p", "x")
    port.write_text(tmp_path / "p", "x")
    report = SessionReport(
        role="r",
        provider="claude",
        run_mode="automate",
        policy=SessionPolicy(),
        launch=["claude"],
        env={},
        prompt=None,
        plan=port.plan,
    )

    assert report.to_dict()["duplicates"] == [str(tmp_path / "p")]
    assert "materialized twice" in report.render()


def test_materialized_entries_carry_sha256_digests(tmp_path: Path):
    import hashlib

    port = PlanMaterializer()
    port.write_text(tmp_path / "test.txt", "hello world")
    report = SessionReport(
        role="r",
        provider="claude",
        run_mode="hitl",
        policy=SessionPolicy(),
        launch=["claude"],
        env={},
        prompt=None,
        plan=port.plan,
    )
    mat = report.to_dict()["materialized"]
    assert len(mat) == 1
    expected_digest = hashlib.sha256(b"hello world").hexdigest()
    assert mat[0]["digest"] == expected_digest


def test_full_render_dumps_the_body_a_plan_mode_build_never_wrote(tmp_path: Path):
    """HATS-1548: ``--dry-run-full`` promised the composed prompt and printed
    ``(not written)`` — it read the path off disk, and a dry-run writes nothing.

    The bytes were in ``BuiltArtifacts.full_content`` the whole time. Body stays
    out of ``to_dict``: it never was in the payload, and putting ~50 KB of prompt
    into ``--json`` (and into the goldens) to fix a human affordance is a trade
    nobody asked for.
    """  # comment-length: allow — the render/json asymmetry is deliberate
    port = PlanMaterializer()
    port.write_text(tmp_path / "cache" / "prompt.md", "role text")
    report = SessionReport(
        role="maintainer",
        provider="agy",
        run_mode="hitl",
        policy=SessionPolicy(),
        launch=["agy"],
        env={},
        prompt=tmp_path / "cache" / "prompt.md",
        plan=port.plan,
        prompt_text="# ROLE: MAINTAINER\nbody bytes",
    )

    assert not (tmp_path / "cache" / "prompt.md").exists(), "a dry-run writes nothing"
    assert "# ROLE: MAINTAINER\nbody bytes" in report.render(full=True)
    assert "(not written)" not in report.render(full=True)
    assert "prompt_text" not in report.to_dict()


def test_full_render_still_falls_back_to_the_file_on_a_real_record(tmp_path: Path):
    """A launch record read back from disk carries no body — the file does."""
    written = tmp_path / "prompt.md"
    written.write_text("bytes on disk")
    report = SessionReport(
        role="maintainer",
        provider="agy",
        run_mode="hitl",
        policy=SessionPolicy(),
        launch=["agy"],
        env={},
        prompt=written,
        plan=PlanMaterializer().plan,
    )

    assert "bytes on disk" in report.render(full=True)
