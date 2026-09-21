"""``scripts/replay_claude_corpus.py`` over the reader's own fixture transcripts.

The script is the per-release re-measurement of what the Claude reader calls
drift; the corpus it runs over is private, so the one thing this test must hold
is that nothing from a transcript reaches the output.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "replay_claude_corpus.py"
FIXTURES = REPO_ROOT / "packages" / "ai-hats-observe" / "tests" / "fixtures" / "transcripts"


def _load():
    spec = importlib.util.spec_from_file_location("replay_claude_corpus", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module  # a dataclass resolves its module by name
    spec.loader.exec_module(module)
    return module


def _fixture_prompt() -> str:
    """A prompt the fixtures carry verbatim — the privacy control's needle."""
    for line in (FIXTURES / "normal.jsonl").read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        content = (record.get("message") or {}).get("content")
        if record.get("type") != "user" or not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                return str(block["text"])
    raise AssertionError("normal.jsonl carries no prompt text — the control has no needle")


def test_replay_counts_every_fixture_and_reports_the_malformed_one() -> None:
    module = _load()
    result = module.replay(FIXTURES)

    assert result.files == len(list(FIXTURES.glob("*.jsonl"))) == 5
    assert result.raised == []
    assert result.kinds["ItemEmitted"] > 0
    # POSITIVE CONTROL: the malformed fixture is seen as drift, so a replay
    # that reports zero drift on a clean corpus is not a replay that reads nothing
    assert result.signals[("Notice/unsupported_record", "malformed-json")] >= 1


def test_the_rendering_carries_counts_and_not_a_transcripts_content() -> None:
    module = _load()
    text = module.render(module.replay(FIXTURES))

    assert text.startswith("files=5 ")
    assert "malformed-json" in text
    needle = _fixture_prompt()
    assert needle, "an empty needle proves nothing"
    assert needle not in text


def test_main_refuses_a_missing_root(tmp_path: Path, capsys) -> None:
    module = _load()
    assert module.main([str(SCRIPT), str(tmp_path / "nowhere")]) == 2
    assert "no such directory" in capsys.readouterr().err


def test_main_renders_the_default_shape(capsys) -> None:
    module = _load()
    assert module.main([str(SCRIPT), str(FIXTURES)]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("files=5 ")
    assert "signals (reason, raw_code):" in out


def test_the_script_runs_as_a_program() -> None:
    import subprocess

    done = subprocess.run(
        [sys.executable, str(SCRIPT), str(FIXTURES)], capture_output=True, text=True, timeout=60
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.startswith("files=5 ")
