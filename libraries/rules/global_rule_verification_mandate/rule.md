# Global Rule: Explicit Verification Mandate

## Core Directives

1. **Never Assume Success:** Do not assume that a file edit, configuration change, or code generation was successful simply because the tool completed without errors.
2. **Mandatory Post-Change Verification:**
   - After ANY modification to a codebase or configuration file, run a verification command to prove its validity.
   - For configuration files: run the application's built-in check command (e.g., `nginx -t`, `jq . file.json`).
   - For code files: run the associated linter, type-checker, or test suite (e.g., `ruff check`, `go test`, `pytest`).
3. **Show Your Work:** Verification output must be present before reporting the task as done.
4. **Iterative Fixing:** If verification fails, diagnose and fix in the same session, then re-verify.
5. **If No Automated Check Exists:** Verify by reading the parsed output or running in dry-run mode.
