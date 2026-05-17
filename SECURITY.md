# Security policy

## Reporting a vulnerability

ai-hats is a small project; security reports go to the maintainer directly.

**Primary channel — email:** [f@muratovv.me](mailto:f@muratovv.me)

**Backup channel — GitHub Security Advisories:**
https://github.com/muratovv/ai-hats/security/advisories/new (private
between you and the maintainer until a fix ships).

Please **do not** open a public issue for a security problem.

A useful report includes:

- A description of the issue and the impact you observed.
- Steps to reproduce (or a minimal proof-of-concept).
- The ai-hats version you tested against (`ai-hats --version`).
- Any suggested remediation, if you have one.

We aim to acknowledge reports within **3 business days** and to follow up
with an initial assessment within **7 business days**. Severe issues may
trigger a coordinated release; lower-severity issues are folded into the
next routine release. Reporters are credited in the changelog and the
security advisory unless they ask to remain anonymous.

## Supported versions

ai-hats is pre-1.0 software. Security fixes land on `master` and ship in
the next release. Older tagged releases are **not** patched.

| Version  | Status               |
| -------- | -------------------- |
| `master` | Active development   |
| `v0.x`   | Latest tag supported |
| `< v0.x` | Unsupported          |

## Scope

In scope:

- The ai-hats CLI (`ai-hats` binary) and the Python package (`pip install ai-hats`).
- The bash launcher (`scripts/ai-hats-launcher`) and the installer
  (`scripts/install-launcher.sh`).
- Pre-commit hooks shipped under `library/{core,usage}/skills/*/git_hooks/`
  — particularly the privacy hook.
- The skill library packaged with ai-hats (anything under `library/`).

Out of scope:

- Vulnerabilities in upstream providers (Claude CLI, Gemini CLI) — please
  report those to Anthropic or Google.
- Vulnerabilities in transitive Python dependencies — please report
  upstream first; ai-hats will track the fix and pin a safe version.
- Configuration mistakes in a user's own `ai-hats.yaml` or local skills.
