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

Every wait has a bound. Every error carries what was read so far and the tail of
the holder's stderr.

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
