"""LLMCaller — abstraction over single-shot LLM calls used by SessionRetroBuilder.

Two implementations:
- SubprocessLLMCaller: direct `<provider> --print -p <prompt>` subprocess (default, fast)
- SubAgentLLMCaller: wraps SubAgentRunner with a minimal session-summarizer role
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from ..runtime import SubAgentRunner


class LLMCaller(Protocol):
    """Single-shot LLM call: takes a prompt, returns the model's text response."""

    def __call__(self, prompt: str) -> str: ...


class SubprocessLLMCaller:
    """Direct subprocess call to the project's provider CLI.

    Reads provider name from `ai-hats.yaml` (e.g. claude / gemini) and runs
    the equivalent of `claude --print -p <prompt>`. No worktree, no role
    composition, no session — purely a single-turn LLM call.
    """

    def __init__(self, project_dir: Path, *, timeout: int = 600) -> None:
        self.project_dir = project_dir
        self.timeout = timeout

    def __call__(self, prompt: str) -> str:
        from ..models import ProjectConfig

        cfg = ProjectConfig.from_yaml(self.project_dir / "ai-hats.yaml")
        provider_name = cfg.provider or "claude"
        model = cfg.feedback.session_retro.model
        cmd = self._build_cmd(provider_name, prompt, model=model)
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(self.project_dir),
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except FileNotFoundError as e:
            raise RuntimeError(f"Provider CLI not found: {provider_name}") from e
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(
                f"Provider CLI timed out after {self.timeout}s"
            ) from e
        if proc.returncode != 0:
            raise RuntimeError(
                f"Provider CLI exited {proc.returncode}: {proc.stderr.strip()}"
            )
        return proc.stdout

    @staticmethod
    def _build_cmd(
        provider_name: str, prompt: str, *, model: str | None = None,
    ) -> list[str]:
        """Build the non-interactive CLI invocation for the given provider.

        ``model`` is an optional explicit model name (passed as ``--model
        <name>``); when None, the provider CLI's default applies.
        """
        extra = ["--model", model] if model else []
        if provider_name == "claude":
            return ["claude", *extra, "--print", "-p", prompt]
        if provider_name == "gemini":
            return ["gemini", *extra, "-p", prompt]
        # Generic fallback — assume `-p`/`--prompt` is supported
        return [provider_name, *extra, "-p", prompt]


class SubAgentLLMCaller:
    """Wraps SubAgentRunner with a minimal role for one-shot summarization.

    Heavier than SubprocessLLMCaller (worktree + role composition + audit
    artifacts) but uniform with the judge pipeline. Useful when the LLM call
    needs to be auditable as a sub-session.
    """

    def __init__(
        self,
        project_dir: Path,
        *,
        role_name: str = "assistant",
        runner: SubAgentRunner | None = None,
    ) -> None:
        self.project_dir = project_dir
        self.role_name = role_name
        self._runner = runner

    def __call__(self, prompt: str) -> str:
        from ..runtime import SubAgentRunner

        runner = self._runner or SubAgentRunner(self.project_dir)
        session = runner.run(
            role_name=self.role_name,
            task=prompt,
            isolation_mode="discard",
        )
        transcript_path = session.session_dir / "transcript.txt"
        if not transcript_path.exists():
            return ""
        return transcript_path.read_text()
