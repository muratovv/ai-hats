---
name: e2e-rthook
description: E2E fixture skill (HATS-601). Declares provider runtime hooks for both PreToolUse and PostToolUse so the runtime-hook propagation e2e can prove the full materialize → settings.json → live-script chain. Not shipped.
ai_hats:
  runtime_hooks:
    PreToolUse:
      - matcher: Bash
        script: hooks/probe.sh
    PostToolUse:
      - matcher: Edit|Write
        script: hooks/probe.sh
---

# e2e-rthook

E2E fixture skill (HATS-601). Exists only so the runtime-hook propagation
test can compose a role that declares `runtime_hooks:` through the real
`ai-hats` binary. Not part of the shipped library.
