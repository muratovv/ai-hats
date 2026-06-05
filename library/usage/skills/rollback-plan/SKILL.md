---
name: rollback-plan
description: Explicit revert paths for infrastructure and configuration changes. Use before applying any infrastructure or configuration change, before database migrations, or before any change with production impact.
---
# Rollback Plan

Ensure every infrastructure change has an explicit revert path.

## When to Use
The **pre-change revert path** for a config/infra change or migration — written
*before* you apply, so a bad change can be walked back. Distinct from
**backup-recovery**, which protects the *data itself*; rollback-plan reverts the
*change*. A risky DB migration typically needs both: a backup (data) and a
rollback plan (schema/app).

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
