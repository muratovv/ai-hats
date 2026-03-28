# Request Supervisor

Protocol for deciding whether to request help from supervisor (user or parent-agent).

## When to Use
- Before any communication with the user or parent-agent
- When uncertain whether to act autonomously or escalate

## Before Making Any Request — Checklist

1. **Do I have a tool that can do this?** (bash, curl, file read/write, etc.)
   → YES: Do it yourself. Do not request.
2. **Does this require credentials or auth I don't have?**
   → YES: Request, specifying exactly what you need.
3. **Does this require approval for a destructive or irreversible action?**
   → YES: Request approval, explain what and why.
4. **Does this require information only the supervisor has?** (business context, priorities, preferences)
   → YES: Request with specific questions.

## Valid Reasons to Request
- Authentication or authorization you cannot perform
- Approval for destructive or irreversible actions
- Business decisions or priority calls
- Access to systems you have no tools for

## Invalid Reasons (Do It Yourself)
- Running commands or scripts
- Checking endpoint availability
- Reading or writing files
- Running tests or verification
- Installing dependencies
- Looking up documentation

## Completion
- Decision made: either acted autonomously or sent a focused request
- If requesting: specific question with context, not a vague "what should I do?"
