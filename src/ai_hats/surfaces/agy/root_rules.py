"""Retired workspace root file hiding utility (HATS-1166 / ADR-0018).

File-hiding hacks (.GEMINI.md.ai_hats_bak_*) have been permanently retired.
Native provider context is enabled by default.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Sequence


@contextmanager
def hidden(project_dir: Path, filenames: Sequence[str]) -> Generator[None, None, None]:
    """No-op context manager — HATS-1166: file hiding retired."""
    yield
