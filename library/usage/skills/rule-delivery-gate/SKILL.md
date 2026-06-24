---
name: rule-delivery-gate
description: Pre-commit check that staged trait/role injections never point `see rule X` at a rule the agent cannot read. Use when composing the maintainer or role-curator role, when a commit touches a library/**/config.yaml injection, or when diagnosing why a commit was blocked by the rule-delivery hook.
ai_hats:
  # HATS-700 — hook-carrier skill. The assembler installs the script below
  # into `.githooks/pre-commit.d/` at composition time. On a staged
  # `library/**/config.yaml` it runs `python -m ai_hats.rule_delivery library`
  # and blocks the commit if a `see rule X` pointer names an undeliverable
  # rule. Fail-open if python/ai_hats absent; override AI_HATS_RULE_DELIVERY_ACK=1.
  git_hooks:
    pre-commit:
      - git_hooks/pre-commit-rule-delivery.sh
---
# Rule Delivery Gate

Pure-infrastructure hook-carrier skill. It contributes one git pre-commit hook
that enforces the HATS-700 rule-delivery contract: a `see rule X` pointer in a
delivered trait/role injection must name a rule the agent can actually read.
There is no agent-side decision logic here — the value is delivered entirely
through composition.

## What it gates

- **Scope:** fires only when a commit stages a `library/**/config.yaml` (a
  trait/role injection — where a dangling pointer is introduced). The check then
  scans the whole working-tree `library/`, because a pointer's deliverability
  depends on the global `ALWAYS_ON_RULES` + `SUMMARIZED_IN_INJECTION` + every
  config, not a single diff.
- **Invariant:** every `see rule X` must resolve to a rule that is either
  always-on (`providers.ALWAYS_ON_RULES` — full body in the prompt) or registered
  in `SUMMARIZED_IN_INJECTION` (`ai_hats/rule_delivery.py` — essence summarized
  inline). A pointer to an undelivered, unregistered rule blocks the commit.
- **Effect:** the same pure function the G2 unit test uses
  (`python -m ai_hats.rule_delivery library`) returns non-zero → commit blocked
  with the offending `source: see rule X` lines.

Because the scope is the changed files, the gate only fires on commits that
touch an injection — it never retro-blocks the pre-existing library.

## Who gets it

Installed via the `skill-engineer` trait, composed only by the `maintainer` and
`role-curator` roles. Other roles do not receive the hook. The server-side
counterpart is the G2 unit test, run by CI on every PR/push to master.

## How to bypass

Fix the pointer — make the rule always-on, fold its essence into the injection
and register it in `SUMMARIZED_IN_INJECTION`, or drop the pointer — or, after
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
