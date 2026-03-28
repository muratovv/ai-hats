# Git Mastery

Advanced git operations for development workflow.

## When to Use
- Any git operation beyond basic add/commit/push
- Branch management, rebasing, conflict resolution
- Setting up commit conventions for a project

## Capabilities
- Create feature branches with naming conventions
- Stage and commit with conventional commit format
- Resolve merge conflicts intelligently
- Manage worktrees for parallel development
- Interactive rebase for clean history
- Cherry-pick and backport changes

## Conventions
- Branch naming: `type/description` (e.g., `feat/add-auth`, `fix/login-bug`)
- Conventional commits: `type(scope): description`
- Types: feat, fix, refactor, docs, test, chore, ci
- Write meaningful commit messages explaining WHY, not WHAT
- Commit frequently with atomic changes
- Always check status before operations
- Never force push without explicit approval

## Anti-Patterns
- Force push without approval — can destroy team members' work
- Giant commits mixing multiple concerns — keep commits atomic
- Commit messages describing what ("changed X") instead of why
