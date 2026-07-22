#!/usr/bin/env python3
import json
import sys

def main() -> int:
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

    # Tokenize cmd
    tokens = cmd.split()
    cmd_bin = ""
    for token in tokens:
        if "=" in token:
            continue
        if token in ("sudo", "env", "nohup", "xargs", "time"):
            continue
        cmd_bin = token
        break

    reason = ""

    # Exception 1: sed with -i flag
    if cmd_bin == "sed" and " -i" in cmd:
        reason = "Остановлено: sed с флагом -i модифицирует файлы. Требуется разрешение."
    
    # Exception 2: git push
    if cmd_bin == "git" and " push" in cmd:
        reason = "Остановлено: подкоманда git push требует разрешения."
    
    # Dangerous binaries
    if cmd_bin in ("rm", "mkfs", "truncate", "chown", "dd"):
        reason = f"Остановлено: вызов потенциально деструктивной утилиты ({cmd_bin})."
    
    # Dangerous substrings
    cmd_lower = cmd.lower()
    dangerous_substrings = ["drop table", "drop database"]
    for sub in dangerous_substrings:
        if sub in cmd_lower:
            reason = f"Остановлено: обнаружена опасная подстрока ({sub})."
            break

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
