# Global Rule: Resource Hygiene

1. **Cleanup Mandate**: You are responsible for any temporary artifacts you create. By default, delete temp files and directories before reporting task completion. Exception: if cleanup would destroy something the user might want to inspect (logs, repro state, intermediate output), leave it under `/tmp/` and surface the path in your report.
2. **Standard Temp Paths**: Prefer system temp directories (e.g., `/tmp/`) for temporary work.
3. **Idempotency**: Ensure cleanup commands do not fail if the file was already moved or deleted (e.g., `rm -rf file || true`).
