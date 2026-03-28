# Rollback Plan

Ensure every infrastructure change has an explicit revert path.

## Procedure
1. **Capture state**: Before applying changes, record the current known-good state (snapshot, config backup, git SHA).
2. **Define revert commands**: Write explicit rollback commands before executing the change.
3. **Set success criteria**: Define what "change succeeded" means (health check, endpoint response, metric threshold).
4. **Verify or revert**: After applying, run success criteria. If failed — execute rollback immediately.
5. **Time-box**: Set a maximum time window for verification. If criteria not met within the window, revert.
