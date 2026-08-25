# A contract that reads well

```python
# types.py — the one home for names whose type is not decided yet
StepId = NewType("StepId", str)
Tracer = object          # TODO(TICKET-412): real type once the tracer lands

# contract.py
class Isolation(Enum):            # a closed set, so not a str
    NONE = auto()
    WORKTREE = auto()

@dataclass(frozen=True)
class Target:                     # read by the planner
    step: StepId
    inputs: Mapping[InputName, Payload]

@dataclass(frozen=True)
class Placement:                  # read by the runner, and only by it
    workdir: Path
    isolation: Isolation

@dataclass(frozen=True)
class RunResult:
    """What a caller can act on once the run is over."""
    status: ExitStatus            # lifted out of the raw funnel — no raw twin
    artifacts: Mapping[ArtifactId, Path]
```

Every field's reader is nameable; no primitive stands for a domain value; no
member names a caller; nothing carries another subsystem's id; each value has
one spelling. What it does *not* say is as deliberate: no example from a
vocabulary it does not own, and no field parked for a consumer not yet written.

Each line above is one shape from `SKILL.md`; read it there for why.
