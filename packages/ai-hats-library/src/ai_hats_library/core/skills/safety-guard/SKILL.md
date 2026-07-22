---
name: safety-guard
description: PreToolUse hook for enforcing global_rule_destructive_actions. Prevents dangerous commands like in-place sed edits, unchecked destructive binaries, and git pushes.
ai_hats:
  runtime_hooks:
    PreToolUse:
      - matcher: Bash|run_command|execute
        script: hooks/safety_gate.py
license: MIT
---

# safety-guard

A PreToolUse runtime-hook for Bash/terminal tools. Checks the executed command line for dangerous tools and arguments before they run. If it finds a dangerous substring (e.g. `sed -i`, `git push`, `rm`, `drop table`), it returns an explicit `deny` instruction, enforcing `global_rule_destructive_actions` automatically.

## YOLO Mode
If you expect to run a large number of destructive commands (e.g. bulk file deletion, complex refactors requiring many in-place edits), you can request YOLO mode from the user to bypass the safety gate for the duration of your session. 
To do this, attempt to execute `export AI_HATS_YOLO=1` in your Bash tool. The safety gate will intercept this and prompt the supervisor for permission.

**CRITICAL RULES FOR YOLO MODE:**
1. You may request YOLO mode from the supervisor.
2. You MUST NOT request YOLO mode arbitrarily or "just in case".
3. When requesting YOLO mode (which triggers a prompt to the supervisor), you MUST explicitly output a message stating *why* you need it and exactly what destructive actions you plan to perform.
