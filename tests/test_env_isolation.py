"""HATS-671: ambient ``AI_HATS_DIR`` must never leak into the test suite.

Regression guard for the bug where
``test_save_artifact_expands_ai_hats_dir_placeholder`` escaped its
``tmp_path`` and wrote the literal ``"payload"`` into the real
``$AI_HATS_DIR/sessions/retros/judge/`` whenever pytest ran in a shell that
exported ``AI_HATS_DIR`` (``ai_hats_dir()`` gives the env var precedence over
``project_dir``). The ``_isolate_ai_hats_dir`` autouse fixture in
``tests/conftest.py`` neutralizes it.

Fail-under-revert: run ``AI_HATS_DIR=$(mktemp -d) pytest tests/test_env_isolation.py``
— green with the fixture, red without it (the second test resolves the judge
path under the ambient sentinel instead of ``tmp_path``).
"""
from __future__ import annotations

import os
from pathlib import Path

from ai_hats.pipeline.steps.save import SaveArtifact


def test_ambient_ai_hats_dir_is_neutralized():
    """The autouse guard clears any ambient ``AI_HATS_DIR`` for every test."""
    assert os.environ.get("AI_HATS_DIR") is None


def test_save_artifact_judge_template_stays_in_project_dir(tmp_path: Path):
    """The exact path that leaked (HATS-671) must resolve under the caller's
    ``project_dir`` — never an ambient env override nor the process CWD.

    The path is anchored absolutely to ``project_dir`` by ``SaveArtifact``
    (HATS-671), so it is contained even though the test's CWD is the repo root.
    """
    template = "<ai_hats_dir>/sessions/retros/judge/{ts}-report.md"
    step = SaveArtifact({"key": "blob", "out_path_template": template})
    out = step.run(blob="payload", project_dir=tmp_path)
    saved = out["saved_path"]
    # ``.resolve()`` on both sides normalizes the macOS ``/var`` → ``/private/var``
    # symlink so containment is compared on canonical paths.
    assert saved.is_absolute()
    assert saved.resolve().is_relative_to(tmp_path.resolve())
    assert ".agent/ai-hats/sessions/retros/judge/" in str(saved).replace("\\", "/")
    assert saved.read_text() == "payload"
