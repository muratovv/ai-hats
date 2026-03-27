"""Runtime — PTY wrapping, hooks execution, sub-agent launch."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

from .assembler import Assembler
from .models import LifecycleEvent, ProfileConfig
from .observe import Session, SessionManager, TraceTag
from .providers import get_provider


class HooksRunner:
    """Executes lifecycle hook scripts."""

    def __init__(self, hooks_dir: Path, project_dir: Path) -> None:
        self.hooks_dir = hooks_dir
        self.project_dir = project_dir

    def run(self, event: LifecycleEvent, env: dict[str, str] | None = None) -> list[dict]:
        """Run all hook scripts for an event. Returns list of results."""
        results = []
        scripts = self._find_scripts(event)
        for script in scripts:
            result = self._execute(script, env or {})
            results.append(result)
        return results

    def _find_scripts(self, event: LifecycleEvent) -> list[Path]:
        """Find hook scripts in hooks dir matching the event pattern."""
        scripts = []
        if not self.hooks_dir.exists():
            return scripts
        # Convention: scripts starting with event name or in event subdir
        for f in sorted(self.hooks_dir.iterdir()):
            if f.is_file() and f.suffix in (".sh", ".py") and f.stem.startswith(event.value):
                scripts.append(f)
        event_dir = self.hooks_dir / event.value
        if event_dir.is_dir():
            for f in sorted(event_dir.iterdir()):
                if f.is_file() and f.suffix in (".sh", ".py"):
                    scripts.append(f)
        return scripts

    def _execute(self, script: Path, env: dict[str, str]) -> dict:
        """Execute a single hook script."""
        full_env = {**os.environ, **env}
        try:
            result = subprocess.run(
                [str(script)],
                cwd=str(self.project_dir),
                env=full_env,
                capture_output=True,
                text=True,
                timeout=60,
            )
            return {
                "script": str(script),
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        except subprocess.TimeoutExpired:
            return {"script": str(script), "returncode": -1, "error": "timeout"}
        except Exception as e:
            return {"script": str(script), "returncode": -1, "error": str(e)}


class WrapRunner:
    """PTY-proxied CLI wrapper for interactive sessions."""

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir
        self.assembler = Assembler(project_dir)
        self.session_mgr = SessionManager(project_dir)

    def run(
        self,
        provider_name: str,
        role_override: str | None = None,
        extra_args: list[str] | None = None,
    ) -> int:
        """Launch a wrapped CLI session with PTY proxying."""
        # Resolve provider
        provider = get_provider(provider_name)

        # Apply role override if specified, or re-assemble for target provider
        profile = ProfileConfig.load(self.project_dir / "profile.json")
        if role_override:
            self.assembler.set_role(role_override, provider_name)
        elif profile.active_role and profile.provider != provider_name:
            # Provider mismatch — re-assemble current role for target provider
            self.assembler.set_role(profile.active_role, provider_name)

        profile = ProfileConfig.load(self.project_dir / "profile.json")

        # Create session
        session = self.session_mgr.create_session()
        session.init_audit(
            role=profile.active_role,
            provider=provider_name,
        )
        session.log_trace(TraceTag.SYS, f"Session started: role={profile.active_role}")

        # Build environment
        env = {
            **os.environ,
            **session.get_env(),
            **provider.get_env(session.session_dir, self.project_dir),
            "AI_HATS_ROLE": profile.active_role,
        }

        # Run hooks: session_start
        hooks_runner = HooksRunner(
            self.project_dir / ".agent" / "hooks",
            self.project_dir,
        )
        hooks_runner.run(LifecycleEvent.SESSION_START, env=env)
        session.log_trace(TraceTag.SYS, "hooks.session_start completed")

        # Build CLI command
        cmd = provider.get_cli_command(extra_args)
        session.log_trace(TraceTag.SYS, f"Launching: {' '.join(cmd)}")
        session.append_audit(f"Launched {provider_name} CLI")

        # PTY proxy via pty.spawn
        exit_code = self._pty_spawn(cmd, env)

        # Run hooks: session_end
        session.log_trace(TraceTag.SYS, f"Session ended: exit_code={exit_code}")
        hooks_runner.run(LifecycleEvent.SESSION_END, env=env)
        session.append_audit(f"Session ended with code {exit_code}")

        session.finalize_audit({
            "exit_code": exit_code,
            "role": profile.active_role,
            "provider": provider_name,
        })

        return exit_code

    def _pty_spawn(self, cmd: list[str], env: dict[str, str]) -> int:
        """Spawn a process with PTY for interactive terminal passthrough."""
        import pty

        # Use pty.spawn for full terminal passthrough
        def _set_env():
            for k, v in env.items():
                os.environ[k] = v

        _set_env()

        try:
            exit_status = pty.spawn(cmd)
            if isinstance(exit_status, int):
                return os.waitstatus_to_exitcode(exit_status) if exit_status > 255 else exit_status
            return 0
        except FileNotFoundError:
            print(f"Error: '{cmd[0]}' not found. Is it installed?", file=sys.stderr)
            return 127
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1


class SubAgentRunner:
    """SDK-based sub-agent executor."""

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir
        self.assembler = Assembler(project_dir)
        self.session_mgr = SessionManager(project_dir)

    def run(
        self,
        role_name: str,
        task: str = "",
        ticket_id: str = "",
        model: str = "",
        parent_session: str | None = None,
    ) -> Session:
        """Execute a sub-agent in isolation."""
        # Create sub-session
        session = self.session_mgr.create_session(parent_session=parent_session)

        # Compose the role
        result = self.assembler.composer.compose(role_name)
        provider = get_provider(self.assembler.project_config.provider)

        # Build meta-prompt
        meta_prompt = self._build_meta_prompt(
            result=result,
            provider=provider,
            task=task,
            ticket_id=ticket_id,
        )
        session.save_meta_prompt(meta_prompt)
        session.init_audit(role=role_name, provider=provider.name, model=model)
        session.log_trace(TraceTag.SUB, f"Sub-agent started: role={role_name}")

        # Build system prompt
        system_prompt = provider.build_system_prompt(result)

        # For now, execute via CLI subprocess (SDK integration is provider-specific)
        env = {
            **os.environ,
            **session.get_env(),
            "AI_HATS_ROLE": role_name,
        }

        cmd = provider.get_cli_command()
        # Add prompt via stdin for non-interactive execution
        session.log_trace(TraceTag.SUB, f"Executing: {' '.join(cmd)}")

        try:
            proc = subprocess.run(
                cmd + ["--print", "-p", meta_prompt] if provider.name == "claude" else cmd,
                cwd=str(self.project_dir),
                env=env,
                capture_output=True,
                text=True,
                timeout=600,
            )
            session.log_trace(TraceTag.RES, f"Exit code: {proc.returncode}")

            # Save transcript
            transcript_path = session.session_dir / "transcript.txt"
            transcript_path.write_text(proc.stdout)

            if proc.stderr:
                reasoning_path = session.session_dir / "reasoning.log"
                reasoning_path.write_text(proc.stderr)

            session.finalize_audit({
                "exit_code": proc.returncode,
                "role": role_name,
                "model": model,
            })

        except subprocess.TimeoutExpired:
            session.log_trace(TraceTag.SYS, "Sub-agent timed out")
            session.append_audit("TIMEOUT")
        except Exception as e:
            session.log_trace(TraceTag.SYS, f"Sub-agent error: {e}")
            session.append_audit(f"ERROR: {e}")

        return session

    def _build_meta_prompt(self, result, provider, task: str, ticket_id: str) -> str:
        """Build the meta-prompt for sub-agent execution."""
        sections = []

        # SYSTEM_ROLE
        sections.append(f"# SYSTEM_ROLE\n{result.merged_injection}")

        # PROJECT_STATE
        state_md = self.project_dir / ".agent" / "STATE.md"
        if state_md.exists():
            sections.append(f"# PROJECT_STATE\n{state_md.read_text()}")

        # CONSTRAINTS
        if result.priorities:
            constraints = "\n".join(f"- {p}" for p in result.priorities)
            sections.append(f"# CONSTRAINTS\n{constraints}")

        # TICKET_CONTEXT
        if ticket_id:
            ticket_context = self._load_ticket(ticket_id)
            if ticket_context:
                sections.append(f"# TICKET_CONTEXT\n{ticket_context}")

        # TASK
        if task:
            sections.append(f"# TASK\n{task}")

        return "\n\n".join(sections)

    def _load_ticket(self, ticket_id: str) -> str:
        """Load ticket context from task card."""
        task_file = self.project_dir / ".agent" / "backlog" / "tasks" / ticket_id / "task.yaml"
        if task_file.exists():
            return task_file.read_text()
        return ""
