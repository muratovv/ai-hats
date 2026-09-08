"""Deliver Codex rack-transition consent through the session command wrapper."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import uuid4

import anyio
from mcp import types
from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, ConfigDict

from ai_hats.consent_port import Outcome, verdict
from ai_hats.consent_wrapper import (
    CONFIG_ENV,
    REQUEST_ENV,
    WrapperConfig,
    _resolve_transition_source,
    load_config,
    match_operation,
)
from ai_hats.paths import tasks_dir
from ai_hats.session_identity import SessionIdentity
from ai_hats_library.hooks import consent_ticket
from ai_hats_library.hooks.bypass_journal import journal_bypass
from ai_hats_library.hooks.consent_gate import operations
from ai_hats_rack.ops import StateOp, parse_ops
from ai_hats_rack.verbs.transition import transition

from ai_hats_library.hooks.consent_gate.questions import RACK_FORM
from ..surfaces import ChainDecision
from ..surface_registry import get_surface
from .guards import check_transition
from .lifecycle import serve

logger = logging.getLogger(__name__)
EXECUTION_TIMEOUT_S = 300


def _form_schema(schema: dict[str, object]) -> None:
    # Codex rejects these metadata keys; server-side validation stays strict.
    schema.pop("title", None)
    schema.pop("additionalProperties", None)


class Confirmation(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra=_form_schema)


class Result(BaseModel):
    request_id: str
    task_id: str = ""
    decision: Literal["accept", "decline", "cancel", "covered", "error"] = "error"
    execution: Literal["not_started", "finished", "indeterminate"] = "not_started"
    exit_code: int | None = None
    output: str = ""


@dataclass(frozen=True)
class Binding:
    identity: SessionIdentity
    config: WrapperConfig
    config_path: Path
    config_text: str
    environ: dict[str, str]

    @classmethod
    def load(cls) -> Binding:
        env = dict(os.environ)
        identity = SessionIdentity.from_env(env)
        if identity is None or identity.provider != RACK_FORM.provider:
            raise ValueError("Consent server requires its Codex session identity")
        path = Path(env.get(CONFIG_ENV, "")).resolve()
        expected = Path(identity.session_cache_dir) / "consent-wrapper" / "config.json"
        if path != expected.resolve():
            raise ValueError("Consent wrapper belongs to another session")
        config = load_config(path)
        if config.project_dir.resolve() != identity.project_dir.resolve():
            raise ValueError("Consent wrapper belongs to another project")
        if Path.cwd().resolve() != identity.project_dir.resolve():
            raise ValueError("Consent server must start in its session project")
        if "rack.transition" not in config.policy:
            raise ValueError("The session does not declare rack.transition consent")
        get_surface(identity.provider).command_guard_rows(env)
        return cls(identity, config, path, path.read_text(), env)

    def source(self, task_id: str, argv: tuple[str, ...]) -> str:
        if self.config_path.read_text() != self.config_text:
            raise ValueError("Session wrapper configuration changed")
        return _resolve_transition_source(
            self.config.originals["rack"], task_id, argv, self.environ
        )


def read_transition(args: list[str], project: Path) -> tuple[tuple[str, ...], str]:
    if not args or any("\x00" in arg for arg in args):
        raise ValueError("Expected rack transition arguments without NUL bytes")
    with transition.make_context("transition", list(args), help_option_names=[]) as ctx:
        target_dir = ctx.params["tasks_dir"]
        if target_dir is not None and Path(target_dir).resolve() != tasks_dir(project).resolve():
            raise ValueError("Cross-project --tasks-dir is not supported")
        state_ops = [op for op in parse_ops(ctx.params["op_tokens"]) if isinstance(op, StateOp)]
        if len(state_ops) != 1:
            raise ValueError("Exactly one state transition is required per request")
        argv = ("transition", *args)
        reading = operations.read("rack.transition", "rack", argv)
        if (
            reading is None
            or reading.subject != ctx.params["task_id"]
            or reading.target != state_ops[0].to_state
        ):
            raise ValueError("Ambiguous rack transition arguments")
        return argv, reading.subject


def record(binding: Binding, result: Result, argv: tuple[str, ...], phase: str) -> bool:
    return journal_bypass(
        "consent",
        json.dumps(
            {
                "request_id": result.request_id,
                "task_id": result.task_id,
                "phase": phase,
                "decision": result.decision,
                "execution": result.execution,
                "exit_code": result.exit_code,
            }
        ),
        hook="codex.consent_server",
        cmd=shlex.join(("rack", *argv)),
        session_id=binding.identity.id,
        cwd=binding.config.project_dir,
    )


async def _stop_command(process: asyncio.subprocess.Process) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError as exc:
        logger.info("Transition process group already exited: %s", exc)
    with anyio.move_on_after(3) as grace:
        await process.communicate()
    if grace.cancel_called:
        logger.warning("Transition did not stop after SIGTERM; killing its process group")
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError as exc:
            logger.info("Transition process group already exited: %s", exc)
        with anyio.fail_after(3):
            await process.communicate()


async def execute(
    binding: Binding,
    argv: tuple[str, ...],
    result: Result,
    nonce: str | None,
    *,
    timeout_s: float,
) -> Result:
    env = {**binding.environ, REQUEST_ENV: result.request_id}
    env.pop(consent_ticket.TICKET_ENV, None)
    if nonce is not None:
        env[consent_ticket.TICKET_ENV] = nonce
    process = None
    completed = False
    try:
        process = await asyncio.create_subprocess_exec(
            str(binding.config_path.parent / "bin" / "rack"),
            *argv,
            cwd=binding.config.project_dir,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        async with asyncio.timeout(timeout_s):
            output, _ = await process.communicate()
        completed = True
        return result.model_copy(
            update={
                "execution": "finished",
                "exit_code": process.returncode,
                "output": output.decode(errors="replace"),
            }
        )
    except Exception as exc:
        return result.model_copy(
            update={
                "execution": "indeterminate" if process is not None else "not_started",
                "output": str(exc),
            }
        )
    finally:
        with anyio.CancelScope(shield=True):
            try:
                if process is not None and not completed:
                    await _stop_command(process)
            finally:
                store = consent_ticket.tickets_dir(binding.config.project_dir)
                if nonce is not None and store is not None and (store / f"{nonce}.json").is_file():
                    consent_ticket.consume(
                        result.task_id, start=binding.config.project_dir, nonce=nonce, argv=argv
                    )


def build_server(binding: Binding) -> FastMCP:
    server = FastMCP(
        "ai_hats_consent",
        instructions="Use rack_transition for consent-protected rack transitions. "
        "Pass arguments after rack transition as an argv list. Never launch this server yourself. "
        "Accept authorizes one exact command. If execution is indeterminate, inspect rack context; do not retry.",
    )

    @server.tool(
        annotations=types.ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=False,
        )
    )
    async def rack_transition(args: list[str], ctx: Context) -> Result:
        """Request human approval for one rack transition and return its real command result."""
        result = Result(request_id=uuid4().hex)
        argv: tuple[str, ...] = ()
        started = False
        try:
            argv, task_id = read_transition(args, binding.config.project_dir)
            result = result.model_copy(update={"task_id": task_id})
            source = await asyncio.to_thread(binding.source, task_id, argv)
            matched = match_operation("rack", argv, binding.config.policy, source_state=source)
            if matched is None:
                raise ValueError(
                    "Transition does not match the declared consent policy; use rack CLI"
                )
            guarded = await asyncio.to_thread(
                check_transition,
                argv,
                project_dir=binding.config.project_dir,
                rows=get_surface(binding.identity.provider).command_guard_rows(binding.environ),
                environ=binding.environ,
            )
            if guarded.decision is ChainDecision.DENY:
                raise ValueError(guarded.reason)
            answer = verdict(matched.operation, target_dir=binding.config.project_dir)
            covered = answer.outcome is Outcome.GRANTED or any(
                binding.environ.get(flag) == "1" for flag in matched.legacy_flags
            )
            if covered:
                result = result.model_copy(update={"decision": "covered"})
            else:
                if not ctx.session.check_client_capability(
                    types.ClientCapabilities(
                        elicitation=types.ElicitationCapability(),
                    )
                ):
                    raise ValueError("Client does not support MCP elicitation")
                async with asyncio.timeout(600):
                    response = await ctx.elicit(
                        message=f"Project: {binding.config.project_dir}\nTask: {task_id}\n"
                        f"From: {source}\nCommand: {shlex.join(('rack', *argv))}\n"
                        f"Request: {result.request_id}\nApprove this exact command once?",
                        schema=Confirmation,
                    )
                result = result.model_copy(update={"decision": response.action})
            if not record(binding, result, argv, "decision"):
                raise ValueError("Consent decision could not be journaled")
            if result.decision not in ("accept", "covered"):
                return result
            if await asyncio.to_thread(binding.source, task_id, argv) != source:
                raise ValueError("stale_request: task state changed while awaiting approval")
            if not record(binding, result, argv, "starting"):
                raise ValueError("Execution start could not be journaled")
            nonce = None
            if not covered:
                nonce = consent_ticket.mint(
                    task_id,
                    start=binding.config.project_dir,
                    session_id=binding.identity.id,
                    argv=argv,
                )
                if nonce is None:
                    raise ValueError("Authorization ticket could not be issued")
            started = True
            result = await execute(binding, argv, result, nonce, timeout_s=EXECUTION_TIMEOUT_S)
        except asyncio.CancelledError:
            result = result.model_copy(
                update={
                    "execution": "indeterminate" if started else "not_started",
                    "output": "Request cancelled; inspect task state before retrying",
                }
            )
            record(binding, result, argv, "cancelled")
            raise
        except Exception as exc:
            result = result.model_copy(
                update={
                    "output": str(exc),
                    "execution": "indeterminate" if started else "not_started",
                }
            )
        if not record(binding, result, argv, "result"):
            result = result.model_copy(update={"output": result.output + "\nResult journal failed"})
        return result

    return server


def main() -> None:
    anyio.run(serve, build_server(Binding.load()))


if __name__ == "__main__":
    main()
