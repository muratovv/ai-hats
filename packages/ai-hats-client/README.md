# ai-hats-client

Drive an `ai-hats headless` session from a program: send turns, wait for each
one to end, and read everything the session did.

`ai-hats headless` runs a role's interactive session with no terminal. Its stdin
takes commands, one JSON object per line (`commands/v1`). Its stdout opens with a
header line (`headless/v1`) and then copies the session's event log byte for
byte (`events/v1`). Its exit code says how the session ended. This package speaks
that contract and nothing else. It uses only the standard library and imports
nothing from ai-hats.

```python
from ai_hats_client import HeadlessSession

with HeadlessSession.start(["ai-hats", "headless", "-r", "maintainer"]) as s:
    first = s.turn("remember the word KIWI")
    second = s.turn("which word?")
    end = s.close()
assert "KIWI" in second.text and end.code == 0
```

- `HeadlessSession.start(argv, cwd=, env=)` launches the holder and reads the
  header. If the holder refuses before the start, it raises `SessionEnded`, which
  carries the exit code.
- `prompt(text, id=None)` sends a turn and returns its id. `turn_for(id)` waits
  for the `turn_ended` event that lists that id. `turn(text)` does both.
- `close()` closes stdin, which tells the holder to finish its turns and exit. It
  then reads to the end and returns an `Exit` with the code, every event and the
  holder's stderr. `terminate()` signals the holder instead.
- Leaving a `with` block closes the session. If the session does not end within
  `start(..., close_timeout=60)`, the holder is terminated and the
  `HeadlessTimeout` is raised, so no session keeps spending turns.

Every wait has a bound. Every error carries what was read so far and the tail of
the holder's stderr.

- A command the holder would refuse raises `ValueError` before it is sent: empty
  text, an `id` that is not a lowercase canonical UUID, a `decision` other than
  `allow` or `deny`.
- A command the holder does not run raises `HeadlessError`, and so does any
  command after the session has ended. The holder lists what it runs in
  `header.commands`.
- `prompt()`, `answer()` and `interrupt()` may be called from another thread, for
  instance a watchdog that interrupts a turn while the main thread waits on it.
  The waits themselves belong to one thread.

## Questions and interrupts

A session asks its stdin owner what it would ask a person at the terminal: a
gate's `ask`, a consent point, claude's own permission prompt, leaving plan mode,
the model's own question. Each question arrives as a `person_asked` event with a
`call_id`, and the call waits for your answer.

```python
def decide(question):
    s.answer(question["call_id"], "allow")          # or "deny", message="why"

turn = s.turn("push the branch", on_question=decide)
```

- A turn wait calls `on_question` once for each call id. Without a handler it
  raises `QuestionPending` instead of waiting out its bound. Answer
  `pending.question["call_id"]` and wait again: nothing read so far is lost.
- `answer(call_id, "allow", answers={"<question>": "<answer>"})` answers a
  question the model asked with `AskUserQuestion`. `tool_call(call_id)` returns
  the call the question is about, with the questions and their options in its
  `input`.
- A question stays offered until you answer it, its handler returns, or its call
  gets a result: a handler that raised sees it again on the next wait.
- The first answer on a call id wins. The holder refuses the rest and logs a
  `command_rejected` signal for each.
- `interrupt()` stops the running turn and keeps the session. The turn still
  ends with its `turn_ended`, and a question it had open is closed.

The holder never times out a question. If you will not wait, answer `deny`.

The client is synchronous. A caller that needs asyncio runs it in a thread.

## Testing without a model

`ai_hats_client.testing` has a stand-in `claude` binary that speaks the same wire.
Tests that use it need no model and no login:

```python
import os

from ai_hats_client.testing import install

stub = install(tmp_path)  # writes tmp_path/stub-bin/claude
env = {**os.environ, **stub.env(os.environ["PATH"])}
```

Each prompt's text picks what the stub does. The directives are listed in the
module docstring of `ai_hats_client.testing.stub_claude`.
