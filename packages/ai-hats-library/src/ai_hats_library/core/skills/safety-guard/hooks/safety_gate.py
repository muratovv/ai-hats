#!/usr/bin/env python3
"""
Enforces `global_rule_destructive_actions` by blocking dangerous commands 
(e.g., `rm`, `sed -i`, `drop table`).

Note: This script strictly focuses on blocking dangerous/destructive actions.
It does NOT handle git repository hygiene (like blocking direct edits on master) —
that logic is intentionally kept separate in `wt_gate.py` (worktree-isolation) 
so that data protection rules remain universally enforced regardless of worktree usage.
"""
import json
import sys
import re
import os

def parse_commands(cmd_string: str):
    # Split by ;, &&, ||, |
    parts = re.split(r';|&&|\|\||\|', cmd_string)
    return [p.strip() for p in parts if p.strip()]

def get_bin(cmd: str):
    tokens = cmd.split()
    for token in tokens:
        if "=" in token:
            continue
        if token in ("sudo", "env", "nohup", "xargs", "time"):
            continue
        return token
    return ""

def check_sed(cmd: str) -> str:
    if " -i" in cmd:
        return "Stopped: sed with -i flag modifies files. Explicit permission required."
    return ""

def check_git(cmd: str) -> str:
    tokens = cmd.split()
    if "push" in tokens:
        return "Stopped: git push requires explicit permission."
    return ""

def check_dangerous_bin(cmd_bin: str) -> str:
    if cmd_bin in ("rm", "mkfs", "truncate", "chown", "dd"):
        return f"Stopped: potentially destructive binary detected ({cmd_bin})."
    return ""

def check_dangerous_substrings(cmd_string: str) -> str:
    cmd_lower = cmd_string.lower()
    dangerous = ["drop table", "drop database", "ai_hats_yolo"]
    for sub in dangerous:
        if sub in cmd_lower:
            return f"Stopped: dangerous substring detected ({sub})."
    return ""

def check_command(cmd_string: str) -> str:
    reason = check_dangerous_substrings(cmd_string)
    if reason:
        return reason

    subcommands = parse_commands(cmd_string)
    handlers = {
        "sed": check_sed,
        "git": check_git
    }

    for subcmd in subcommands:
        cmd_bin = get_bin(subcmd)
        if not cmd_bin:
            continue
        
        reason = check_dangerous_bin(cmd_bin)
        if reason:
            return reason
            
        handler = handlers.get(cmd_bin)
        if handler:
            reason = handler(subcmd)
            if reason:
                return reason

    return ""

def main() -> int:
    if os.environ.get("AI_HATS_YOLO") == "1":
        return 0

    try:
        payload = json.loads(sys.stdin.read())
    except Exception:
        return 0  # unparsable / empty -> fail-open allow

    tool_input = payload.get("tool_input")
    if not tool_input:
        tool_call = payload.get("toolCall") or {}
        tool_input = tool_call.get("args") or {}

    cmd = tool_input.get("command") or tool_input.get("CommandLine") or ""
    if not cmd:
        return 0

    cmd = cmd.strip()
    if not cmd:
        return 0

    reason = check_command(cmd)

    if reason:
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": reason,
                    }
                }
            )
        )
    return 0

if __name__ == "__main__":
    sys.exit(main())
