# Global Rule: Resource Hygiene

1. **Cleanup Mandate**: You are responsible for any temporary artifacts you create. Any temp file or directory MUST be deleted before reporting task completion.
2. **Standard Temp Paths**: Prefer system temp directories (e.g., `/tmp/`) for temporary work.
3. **Idempotency**: Ensure cleanup commands do not fail if the file was already moved or deleted (e.g., `rm -rf file || true`).
