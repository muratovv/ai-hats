---
name: e2e-lifecycle-tombstone
description: E2E fixture skill (HATS-1147). Declares the RETIRED lifecycle_hooks channel so the retirement e2e can prove that composition fails loudly through the real ai-hats binary instead of silently ignoring the declaration. Not shipped.
ai_hats:
  lifecycle_hooks:
    plan--execute:
      - hooks/gate.sh
---

# e2e-lifecycle-tombstone

E2E fixture skill (HATS-1147). Exists only so the retirement e2e can compose a
role whose skill declares `lifecycle_hooks:` — the channel deleted by ADR-0019
D8 — and assert that a real `ai-hats self init` refuses it by name. Not part of
the shipped library.
