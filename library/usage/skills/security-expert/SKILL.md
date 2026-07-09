---
name: security-expert
description: Infrastructure security covering secrets, access control, and environment hardening. Use when configuring access control, SSH, or API keys, reviewing infrastructure for security posture, before committing code that touches secrets or credentials, or setting up new servers or services.
license: MIT
---
# Infrastructure Security Expert

Maintain zero-trust environment and protect production data.

## When to Use
**Infrastructure hardening** — secrets, access control, SSH, env, server setup.
Two siblings own adjacent surfaces: the trust model of an *AI agent* that can act
on infra (where agent output meets privileged execution) is
**trust-boundary-mapping**, and security review of *application code* is one lens
of **audit-reviewer**. This skill is the infra/credential layer, not the agent
design or the code diff.

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
