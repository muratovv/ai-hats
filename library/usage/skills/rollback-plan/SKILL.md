---
name: rollback-plan
description: Explicit revert paths for infrastructure and configuration changes
---
# Rollback Plan

Ensure every infrastructure change has an explicit revert path.

## When to Use
- Before applying any infrastructure or configuration change
- Before database migrations
- Any change with production impact

## Procedure
1. **Capture state**: Before applying changes, record the current known-good state (snapshot, config backup, git SHA).
2. **Define revert commands**: Write explicit rollback commands before executing the change.
3. **Set success criteria**: Define what "change succeeded" means (health check, endpoint response, metric threshold).
4. **Verify or revert**: After applying, run success criteria. If failed — execute rollback immediately.
5. **Time-box**: Set a maximum time window for verification. If criteria not met within the window, revert.

## Completion
- Rollback commands documented before the change is applied
- Success criteria defined and verifiable
- Post-change verification passed (or rollback executed)

## Anti-Patterns
- Applying changes without a revert path — "we'll figure it out" leads to extended outages
- Vague success criteria — "it looks fine" is not verifiable
- Unbounded verification window — set a time limit or you'll wait forever
