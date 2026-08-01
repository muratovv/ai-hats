---
name: safety-guard
description: PreToolUse hook for enforcing global_rule_destructive_actions. Prevents destructive commands like in-place sed edits, deletion of protected data, and disk-formatting binaries.
ai_hats:
  runtime_hooks:
    PreToolUse:
      - matcher: Bash|run_command|execute
        script: hooks/safety_gate.py
license: MIT
---

# safety-guard

A PreToolUse runtime-hook for Bash/terminal tools. It parses the command line
(quote-aware) and matches **what the command targets**, not what it is named,
because `global_rule_destructive_actions` protects paths.

## The three outcomes

| Target                                                                                                                                                                                            | Outcome                                      |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------- |
| Scratch space, build artefacts, `/tmp` — the cleanup `global_rule_resource_hygiene` mandates                                                                                                      | allowed                                      |
| Protected data — anything not regenerable from repo + toolchain; matched by the fast path `*.db` `*.sqlite*` `*.sql` `*.dump`, `volumes/` `data/` `storage/` `runs/`, `terraform.tfstate`, `.env` | denied; `AI_HATS_DESTRUCTIVE_ACK=1` opens it |
| Filesystem root, `$HOME`, disk-formatting (`mkfs.*`), `dd of=/dev/…`                                                                                                                              | denied; no flag opens it                     |

Every denial names the flag that unblocks it, or says plainly that none does.
A guard that can only say "no" pushes the agent toward blunt instruments.

`git push` is **not** handled here — `pre_bash_shared_state_guard.sh` owns it
(`rule_pause_before_shared_state_write`). Two gates on one concern means the
coarser one silently wins (HATS-1253).

## YOLO Mode

If you expect to run a large number of destructive commands (e.g. bulk file deletion, complex refactors requiring many in-place edits), you can request YOLO mode from the user to bypass the safety gate for the duration of your session.
To do this, attempt to execute `export AI_HATS_YOLO=1` in your Bash tool. The safety gate will intercept this and prompt the supervisor for permission.

**Export it; do not prefix it.** The gate reads `AI_HATS_YOLO` from its own
environment, so `AI_HATS_YOLO=1 <command>` inline is refused by design: a guard
the agent can switch off per-command is not a guard. Only an exported value,
which the supervisor's shell grants, takes effect.

**CRITICAL RULES FOR YOLO MODE:**

1. You may request YOLO mode from the supervisor.
2. You MUST NOT request YOLO mode arbitrarily or "just in case".
3. When requesting YOLO mode (which triggers a prompt to the supervisor), you MUST explicitly output a message stating *why* you need it and exactly what destructive actions you plan to perform.
