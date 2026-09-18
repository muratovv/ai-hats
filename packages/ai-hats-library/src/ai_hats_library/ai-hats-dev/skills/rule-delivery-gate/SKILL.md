---
name: rule-delivery-gate
description: Pre-commit gate over staged trait and role injections. Use when composing a role that carries the `skill-engineer` trait, or when diagnosing why the rule-delivery hook blocked a commit.
ai_hats:
  # hook-carrier skill. The assembler installs the script below
  # into `.githooks/pre-commit.d/` at composition time. On a staged
  # `{ai_hats_library,library,libraries}/**/config.yaml` (three spellings, one
  # live here) it runs `python -m ai_hats.rule_delivery library`
  # and blocks the commit if a `see rule X` pointer names an undeliverable
  # rule. Fail-open if python/ai_hats absent; override AI_HATS_RULE_DELIVERY_ACK=1.
  git_hooks:
    pre-commit:
      - git_hooks/pre-commit-rule-delivery.sh
license: MIT
---
# Rule Delivery Gate

Pure-infrastructure hook-carrier skill. It contributes one git pre-commit hook
that enforces the rule-delivery contract: a `see rule X` pointer in a
delivered trait/role injection must name a rule the agent can actually read.
There is no agent-side decision logic here — the value is delivered entirely
through composition.

## What it gates

- **Scope:** fires only when a commit stages a trait/role injection —
  `{ai_hats_library,library,libraries}/**/config.yaml`, the three layouts the
  filter knows. The check then scans the whole working-tree library, because a
  pointer's validity
  depends on rule existence across the library, not a single diff.
- **Invariant:** every `see rule X` must resolve to a rule that exists in the library.
  A pointer to a non-existent rule blocks the commit.
- **Effect:** the same pure function the G2 unit test uses
  (`python -m ai_hats.rule_delivery library`) returns non-zero → commit blocked
  with the offending `source: see rule X` lines.

Because the scope is the changed files, the gate only fires on commits that
touch an injection — it never retro-blocks the pre-existing library.

## Who gets it

Whatever composes this skill, and nothing else: the hook is written into
`.githooks/pre-commit.d/` at composition time, so a role that does not carry
it never sees the gate. The server-side counterpart is the G2 unit test, run by CI on every PR/push to master.

## How to bypass

Fix the pointer — create the missing rule in the library or drop the pointer — or, after
confirming the pointer is intentional, skip the gate for a single commit:

```bash
AI_HATS_RULE_DELIVERY_ACK=1 git commit ...
```

If `python` / the `ai_hats` package is unavailable the hook is a loud no-op
(fail-open): it prints a SKIPPED notice and allows the commit, so a missing dev
tool never wedges work. Inside an ai-hats dev/agent env the package is present,
so the gate is live.

## Overrides

- `AI_HATS_RULE_DELIVERY_ACK=1` — allow the current commit despite findings.
- `AI_HATS_RULE_DELIVERY_CMD` — override the checker invocation (default
  `python3 -m ai_hats.rule_delivery`); used by the test suite to inject a stub.
