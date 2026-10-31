# Driving a role over ACP

`ai-hats acp -p grok -r <role>` is one process. An ACP client owns its stdin
and stdout. Each stdout line is one JSON-RPC message. The journal stays on
disk. [1]

`ai-hats headless` writes the journal to stdout. That command is unchanged. [2]

## Start

```bash
ai-hats acp -p grok -r maintainer
```

The command drives grok. Any other provider is a usage error, and no session
starts.

The child process is:

```bash
grok --permission-mode default agent --no-leader stdio
```

A `--model` on the launch is appended. `--always-approve`, `-p`, `--print` and
`--session-id` are left off.

When the child exits, this process exits with that code.

## Stdout

Stdout is the ACP stream. A notice from code that does not know about the
client goes to stderr.

The answers to `session/new` and `session/resume` carry the journal path:

```json
{"_meta": {"ai-hats/log": "/absolute/path/events.jsonl"}}
```

That object is the field this page adds. The rest of the result belongs to the
child. The file is `events.jsonl` in the session directory. ai-hats writes it.

## Permissions and questions

The child starts with `--permission-mode default`. The session `config.toml`
sets `permission_mode` to `default` and `yolo` to false, so a person's `auto`
does not apply to this launch.

Measured on grok 1.0.70, `echo` and `touch` do not raise
`session/request_permission`. `rm` does. A bare
`grok --permission-mode default` behaves the same way.

A later `session/set_config_option` for `permission_mode` changes the word the
holder records. The child keeps the mode it started with.

The model asks the child with `_x.ai/ask_user_question`. When the client
declared elicitation and the request has questions, the client sees
`elicitation/create`. When the client did not, and the request is one question
with options, the client sees `session/request_permission`. Any other question
is declined, and the client is not asked.

A selected `reject_once` or `reject_always` is recorded in the journal as
"The person driving this session denied this call." The permission result has
no text field.

## Resume

A second process sends `session/resume` with the `sessionId` from the first
answer. The answer carries that same id. grok 1.0.70 omits `sessionId` on
resume. The holder copies the id from the request into the result.

The new journal does not contain the previous turn. The holder copies the
session directory into the real grok home when the first process exits. The
second process resumes from that directory.

## Holder and child

The holder is `AcpRunner` in `src/ai_hats/acp/`. It plans the session, owns the
client's stdin and stdout, writes `events.jsonl`, and relays JSON-RPC. It does
not name grok. The glossary's **Holder** is the `ai-hats headless` process.
This holder is the other one.

The provider supplies the child through `acp_child()`. For grok the object is
`GrokAcpChild` in `src/ai_hats/surfaces/grok/acp_child.py`. It builds the argv,
copies the session directory, translates `_x.ai/ask_user_question`, and fills a
missing resume `sessionId`.

The launch does not mint a session id. A minted id would publish an empty
directory. grok names the session in `session/new`.

## References

**[1]** — [`docs/glossary.md`](glossary.md) — **ACP session**, the one-line definition this page expands.

**[2]** — [`docs/how-to-orchestration.md`](how-to-orchestration.md#driving-a-session-over-stdinstdout) — `ai-hats headless`: commands on stdin, the journal on stdout.
