# A contract that reads well

Every line is one shape from `SKILL.md`; read it there for why.

## The names

```python
# types.py — the one home for names whose type is not decided yet
StepId = NewType("StepId", str)
Tracer = object          # placeholder: real type lands with the tracer


class Isolation(Enum):   # a closed set of values, so not a str
    NONE = auto()
    WORKTREE = auto()
```

## The data

```python
@dataclass(frozen=True)
class Target:
    step: StepId
    inputs: Mapping[InputName, Payload]


@dataclass(frozen=True)
class Placement:
    workdir: Path
    isolation: Isolation     # NONE keeps the caller's tree; WORKTREE gets a fresh one


@dataclass(frozen=True)
class RunResult:
    """What a caller can act on once the run is over."""

    status: ExitStatus       # lifted out of the raw funnel, so it has no raw twin
    artifacts: Mapping[ArtifactId, Path]
```

## The interface

```python
class StepRunner(Protocol):
    """Runs one step to completion and reports what it produced."""

    def run(self, target: Target, placement: Placement) -> RunResult: ...


class ArtifactStore(Protocol):
    def put(self, artifact: ArtifactId, body: bytes) -> Path: ...
    def open(self, artifact: ArtifactId) -> BinaryIO: ...
```

`StepRunner` publishes one method because one action is on offer. A
`run_with_defaults(...)` beside it would be a second way to perform the same
action, and the next migration would standardise on whichever was in front of it.
Defaults belong in `Placement`, where the caller can see and override them.

`ArtifactStore` publishes `put` and `open` because they are two actions, not two
spellings of one. Neither takes an id belonging to another subsystem: a caller
resolves that and hands over the value.

## What it does not say

No comment names a consumer — consumers change and the comment would not, and a
contract that names its callers has re-imported them. Comments here explain a
value that is not self-evident (`isolation`) or a decision a reader would
otherwise question (`status` lifted out of the funnel), and stay silent where the
type already speaks.

Nothing is parked for a consumer not yet written, and no example borrows a
vocabulary the contract does not own.
