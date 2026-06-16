"""Project + RunResult — subprocess driver for e2e tests.

A subprocess-driven, one-shot ``ai-hats <cmd>`` surface with a fluent
``.expect_*`` style: each verb is one assertion, returns ``self`` for
chaining. Its niche is testing CLI commands that don't spawn an agent
(e.g. ``ai-hats task list``, ``ai-hats config ...``).
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path


def pin_edge_channel(project_path) -> None:
    """HATS-764: pin ``harness: channel: edge`` so a managed ``self update``
    resolves the local ``AI_HATS_REPO_URL`` source the e2e harness provides.

    Without a harness block the channel defaults to ``stable`` → the PyPI JSON
    API (the ``ai-hats`` name is unpublished until HATS-765 → 404, fail-loud).
    Appends to an existing config; writes a minimal valid one otherwise.
    """
    p = Path(project_path) / "ai-hats.yaml"
    if p.exists():
        text = p.read_text()
        if "harness:" not in text:
            if text and not text.endswith("\n"):
                text += "\n"  # robust to a config without a trailing newline
            p.write_text(text + "harness:\n  channel: edge\n")
    else:
        p.write_text(
            "schema_version: 4\n"
            "ai_hats_dir: .agent/ai-hats\n"
            "provider: claude\n"
            "harness:\n  channel: edge\n"
        )


@dataclass(frozen=True)
class RunResult:
    """Outcome of one ``ai-hats`` (or ``python -m ai_hats``) invocation.

    Fluent ``.expect_*()`` verbs (one verb = one assertion, each
    returns ``self``). Subclassable for mocked variants in W2.2-style
    follow-ups if those ever land.
    """

    cmd: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str
    duration_s: float
    cwd: Path

    # ---- core verbs ----

    def expect_ok(self) -> "RunResult":
        """Exit code must be 0."""
        if self.exit_code != 0:
            raise AssertionError(
                f"expected exit 0, got {self.exit_code}\n"
                f"cmd: {' '.join(self.cmd)}\n"
                f"stdout (tail 500):\n{self.stdout[-500:]}\n"
                f"stderr (tail 500):\n{self.stderr[-500:]}"
            )
        return self

    def expect_failure(self) -> "RunResult":
        """Inverse of :meth:`expect_ok`. Useful for testing CLI error paths."""
        if self.exit_code == 0:
            raise AssertionError(
                f"expected non-zero exit, got 0\n"
                f"cmd: {' '.join(self.cmd)}\n"
                f"stdout (tail 500):\n{self.stdout[-500:]}"
            )
        return self

    def expect_stdout_contains(self, *markers: str) -> "RunResult":
        """All ``markers`` must appear in ``stdout``."""
        missing = [m for m in markers if m not in self.stdout]
        if missing:
            raise AssertionError(
                f"stdout missing markers: {missing}\n"
                f"cmd: {' '.join(self.cmd)}\n"
                f"stdout (tail 500):\n{self.stdout[-500:]}"
            )
        return self

    def expect_file(self, rel_path: str, *, contains: str | None = None) -> "RunResult":
        """``cwd/rel_path`` must exist; optionally check substring."""
        target = self.cwd / rel_path
        if not target.exists():
            raise AssertionError(
                f"expected file {target} to exist after `{' '.join(self.cmd)}`"
            )
        if contains is not None:
            text = target.read_text()
            if contains not in text:
                raise AssertionError(
                    f"expected {target} to contain {contains!r}; "
                    f"got (tail 200):\n{text[-200:]}"
                )
        return self

    def __bool__(self) -> bool:
        return self.exit_code == 0


@dataclass
class Project:
    """One tmp project directory + an installed ``ai-hats`` binary.

    Tests interact via :meth:`run` (subprocess one-shot).
    """

    path: Path
    ai_hats_binary: Path
    env: dict[str, str] = field(default_factory=dict)

    @property
    def yaml(self) -> Path:
        return self.path / "ai-hats.yaml"

    @property
    def agent_dir(self) -> Path:
        return self.path / ".agent" / "ai-hats"

    def run(self, *args: str, timeout: float = 60.0,
            extra_env: dict[str, str] | None = None) -> RunResult:
        """Run ``ai-hats <args>`` against this project's binary."""
        cmd = (str(self.ai_hats_binary), *args)
        env = {**os.environ, **self.env}
        if extra_env:
            env.update(extra_env)
        t0 = time.monotonic()
        cp = subprocess.run(
            cmd,
            cwd=str(self.path),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        duration = time.monotonic() - t0
        return RunResult(
            cmd=cmd,
            exit_code=cp.returncode,
            stdout=cp.stdout,
            stderr=cp.stderr,
            duration_s=duration,
            cwd=self.path,
        )
