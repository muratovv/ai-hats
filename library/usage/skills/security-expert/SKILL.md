---
name: security-expert
description: Infrastructure security — secrets, access control, environment hardening
---
# Infrastructure Security Expert

Maintain zero-trust environment and protect production data.

## When to Use
- Configuring access control, SSH, or API keys
- Reviewing infrastructure for security posture
- Before committing code that touches secrets or credentials
- Setting up new servers or services

## 1. Secret Management
- **Scanning**: Before any commit, search for leaked keys/passwords/tokens.
- **Vaulting**: All credentials in Ansible/Tofu must be encrypted (`ansible-vault`, `sensitive` variables).
- **Rotation**: If a secret is leaked, immediately rotate it and notify the user.

## 2. Access Control
- **Least Privilege**: Configure SSH and API access with minimum necessary permissions.
- **Audit**: Regularly verify who has access to infrastructure nodes and gateways.

## 3. Environment Protection
- **Templates**: Use `.env.example` templates. Never commit actual `.env` files.
- **Hardening**: Follow CIS benchmarks when configuring servers via Ansible.

## Anti-Patterns
- Committing secrets "temporarily" — they're in git history forever
- Broad permissions for convenience — least privilege always
- Security as afterthought — integrate from the start, not as a final check
