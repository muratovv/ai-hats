"""A known session id resolves exactly or not at all (HATS-1397, P4).

``exact_path`` used to be a hint: a miss fell through to ``discover_recent_by_mtime``
— "freshest ``*.jsonl`` touched after this session started" — which at teardown is
usually ours and retroactively is a stranger's. The backfill grew a
``jsonl_path.stem != provider_session_id`` guard against exactly that, but the LIVE
path, which runs for every session, never got one: a stranger's transcript parsed
into turns, so ``_may_drop_trace`` returned True and deleted the session's only copy.

The guard belongs in the resolver, not in its callers: the stem check also rejected
agy and cline, whose transcript filenames are not the session id (F2).
"""

from __future__ import annotations

from pathlib import Path

from ai_hats.paths import resolve_transcript

# In the past, so anything created during the test has a later mtime.
SESSION_ID = "20260101-120000-1"
OURS = "aaaaaaaa-1111-2222-3333-444444444444"
STRANGER = "bbbbbbbb-5555-6666-7777-888888888888"


def _transcripts(tmp_path: Path) -> Path:
    d = tmp_path / "projects" / "-proj"
    d.mkdir(parents=True)
    return d


def test_a_known_id_never_resolves_to_a_stranger(tmp_path: Path) -> None:
    """The P4 shape: our transcript is gone, a fresher one belongs to someone else."""
    d = _transcripts(tmp_path)
    (d / f"{STRANGER}.jsonl").write_text('{"type":"user"}\n')

    resolved = resolve_transcript(d, "*.jsonl", SESSION_ID, exact_path=d / f"{OURS}.jsonl")

    assert resolved == []


def test_a_known_id_resolves_to_its_own_transcript(tmp_path: Path) -> None:
    d = _transcripts(tmp_path)
    ours = d / f"{OURS}.jsonl"
    ours.write_text('{"type":"user"}\n')
    (d / f"{STRANGER}.jsonl").write_text('{"type":"user"}\n')

    assert resolve_transcript(d, "*.jsonl", SESSION_ID, exact_path=ours) == [ours]


def test_without_an_id_the_mtime_window_still_applies(tmp_path: Path) -> None:
    """Discovery stays legal where it is honest: teardown of a surface that has no id."""
    d = _transcripts(tmp_path)
    only = d / f"{STRANGER}.jsonl"
    only.write_text('{"type":"user"}\n')

    assert resolve_transcript(d, "*.jsonl", SESSION_ID, exact_path=None) == [only]


def test_surfaces_whose_filename_is_not_the_id_still_resolve(tmp_path: Path, monkeypatch) -> None:
    """F2: the backfill's stem guard accepted only claude — agy and cline nest the id in a dir."""
    from ai_hats_agy.provider import AgyProvider
    from ai_hats.surfaces.cline.provider import ClineProvider

    monkeypatch.setenv("GEMINI_CONFIG_DIR", str(tmp_path / "gemini"))
    monkeypatch.setenv("CLINE_DATA_DIR", str(tmp_path / "cline"))

    agy = tmp_path / "gemini" / "antigravity-cli" / "brain" / OURS / ".system_generated" / "logs"
    agy.mkdir(parents=True)
    (agy / "transcript.jsonl").write_text('{"type":"USER_INPUT"}\n')

    cline = tmp_path / "cline" / "data" / "sessions" / OURS
    cline.mkdir(parents=True)
    (cline / f"{OURS}.messages.json").write_text("[]")

    for provider, expected in (
        (AgyProvider(), agy / "transcript.jsonl"),
        (ClineProvider(), cline / f"{OURS}.messages.json"),
    ):
        resolved = provider.resolve_transcript(tmp_path, SESSION_ID, provider_session_id=OURS)
        assert resolved == [expected]
        assert resolved[0].stem != OURS, "the guard this replaces compared exactly this"


def test_end_ts_filters_out_future_transcripts(tmp_path: Path) -> None:
    d = _transcripts(tmp_path)
    file1 = d / "transcript_1.jsonl"
    file2 = d / "transcript_2.jsonl"

    file1.write_text('{"type":"user"}\n')
    file2.write_text('{"type":"user"}\n')

    # Set mtimes explicitly
    import os

    os.utime(file1, (1767268800.0, 1767268800.0))  # 2026-01-01 12:00:00 UTC
    os.utime(file2, (1767272400.0, 1767272400.0))  # 2026-01-01 13:00:00 UTC (1 hr later)

    # Without end_ts, both are resolved
    res_all = resolve_transcript(d, "*.jsonl", "20260101-120000-1")
    assert res_all == [file1, file2]

    # With end_ts set before file2's mtime, only file1 is resolved
    res_bounded = resolve_transcript(d, "*.jsonl", "20260101-120000-1", end_ts=1767270000.0)
    assert res_bounded == [file1]
