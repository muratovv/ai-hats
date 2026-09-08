"""Project a registered MCP transition onto the composed command guards."""

from __future__ import annotations

import shlex
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path

from ai_hats_library.hooks.consent_gate.questions import RACK_FORM
from ..surfaces import (
    ChainDecision,
    ChainVerdict,
    HookCall,
    HookEvent,
    HookRow,
    run_chain,
    hook_profile,
)


def check_transition(
    argv: Sequence[str],
    *,
    project_dir: Path,
    rows: Sequence[HookRow],
    environ: Mapping[str, str],
) -> ChainVerdict:
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": shlex.join(("rack", *argv))},
        "cwd": str(project_dir),
        "ai_hats_consent_transport": RACK_FORM.id,
    }
    pending = ChainVerdict(decision=ChainDecision.ALLOW, event=HookEvent.PRE_TOOL_USE)
    for row in rows:
        verdict = run_chain(
            hook_profile(RACK_FORM.provider),
            event=HookEvent.PRE_TOOL_USE,
            rows=(row,),
            calls=(HookCall(payload, "exec"),),
            project_dir=project_dir,
            environ=environ,
            hook_environ=environ,
        )
        if verdict.decision is ChainDecision.DENY:
            return verdict
        if verdict.decision is ChainDecision.ASK:
            if (
                not row.tag.startswith("ai-hats:safety-guard:PreToolUse:")
                or row.command.name != "safety_gate.py"
                or verdict.updated_input
            ):
                return replace(
                    verdict,
                    decision=ChainDecision.DENY,
                    reason=f"Unsupported guard question: {row.tag}",
                    updated_input=None,
                )
            pending = verdict
    return pending
