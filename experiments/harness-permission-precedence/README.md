# Harness permission precedence

`probe.sh` answers one question the docs answer only half of: **does a
`permissions.allow` rule silence a PreToolUse hook that returned `ask`?**

It matters because ai-hats holds its consent gate in a PreToolUse hook. If a
broad allow-rule could override that hook, every gate would be one config line
from off — which is what `consent_permission_lint.py` was built to report, on
a measurement taken in mid-August 2026.

Run it against the Claude Code you actually have:

```bash
experiments/harness-permission-precedence/probe.sh          # all four modes
MODES=auto experiments/harness-permission-precedence/probe.sh
```

Two of the four arms are controls, and they are what make the other two
admissible — an arm that can neither run nor block a command testifies to
nothing:

| arm | hook verdict | allow rule | ask rule | reads                          |
| --- | ------------ | ---------- | -------- | ------------------------------ |
| A4  | allow        | –          | –        | control: this path can run     |
| A2  | ask          | –          | –        | control: this path can block   |
| A1  | ask          | ✔          | –        | can allow disarm the hook?     |
| A3  | allow        | –          | ✔        | can ask override the hook?     |

## Recorded verdict

Claude Code **2.1.247**, 2026-08-27, macOS — A4 EXECUTED, A2/A1/A3 BLOCKED, in
every one of `default`, `auto`, `dontAsk`, `bypassPermissions`.

So an allow-rule does **not** disarm a hook, and an ask-rule **does** override
one. What that decided, and what was deleted because of it:
`docs/adr/0031-the-hook-holds-the-gate-alone.md`.

Re-run this before trusting the ADR on a newer Claude Code — it is a claim about
someone else's release, and nothing in CI can hold it.
