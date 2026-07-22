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
