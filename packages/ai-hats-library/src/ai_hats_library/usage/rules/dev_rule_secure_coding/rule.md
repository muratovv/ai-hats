# Secure Coding Standards

## Core Principles

### 1. Layered Security (Defense in Depth)
- **Validation**: Never trust user input. Validate all data at the entry point using schemas or strict types.
- **Parametrization**: NEVER use raw string concatenation for SQL queries, shell commands, or HTML rendering. Always use parameterized queries or safe libraries.
- **Least Privilege**: Grant only the minimum permissions required for a task or service.

### 2. Automated Security Checks (SAST)
- Run available security scanners before finalizing changes (e.g., `bandit` for Python, `gosec` for Go, `tfsec` for Terraform).
- Verify that no secrets, API keys, or private keys are present in code or logs.

### 3. Secure Defaults
- Prefer HTTPS/TLS for all communications.
- Use strong hashing (e.g., Argon2, bcrypt) for sensitive data.

### 4. Vulnerability Management
- Use `pip-audit`, `npm audit`, or `cargo audit` to check for known vulnerabilities in dependencies.
- Proactively update dependencies to their latest secure versions.

## Verification
A security-related change is not "Done" until a SAST scan passes and the "Least Privilege" principle has been applied.
