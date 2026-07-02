# ai-hats-core

Dependency-free core primitives for the
[ai-hats](https://github.com/muratovv/ai-hats) framework — **pure standard
library, zero third-party dependencies**.

Today it ships the canonical **atomic file-write** helper: write to a unique
temp file in the same directory, then `os.replace` it into place, so a reader
never observes a half-written file and a crash never leaves a truncated one.

## Install

```sh
pip install ai-hats-core
```

Requires Python 3.11+.

## Usage

```python
from pathlib import Path
from ai_hats_core import atomic_write_text, atomic_write_bytes

atomic_write_text(Path("config.json"), '{"ok": true}\n')
atomic_write_bytes(Path("data.bin"), b"\x00\x01", mode=0o600)
```

Both helpers write atomically (unique-tmp-in-same-dir + `os.replace`); pass
`mode` to set explicit permissions, otherwise the file lands with the umask
default.

## Public API

`ai_hats_core.__all__`:

- `atomic_write_text(path, text, *, mode=None)`
- `atomic_write_bytes(path, data, *, mode=None)`

## Dependencies

None — standard library only.

## Versioning

[SemVer](https://semver.org/). The public API is `ai_hats_core.__all__`.

## License

MIT. See the [ai-hats repository](https://github.com/muratovv/ai-hats).
