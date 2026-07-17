"""ai-hats-rack — minimal backlog kernel (epic HATS-1014, K1 HATS-1020).

Public surface: the kernel, the FSM topology loader, the subscriber/dispatch
contract, and the event kinds. Everything else (worktree, ownership, gates,
doc store, hooks) is an extension living outside this package.
"""

from .dispatch import (
    AbortOperation,
    Delta,
    DispatchContext,
    DispatchRecord,
    JournalSink,
    OperationAborted,
    Phase,
    Subscriber,
    SubscriberOutcome,
    Subscription,
)
from .events import EdgeEvent, EpicifyEvent, Event, PreDestroyEvent, event_detail
from .fsm import (
    InvalidTransitionError,
    Topology,
    TopologyError,
    UnknownStateError,
    load_topology,
)
from .journal import JsonlJournalSink, read_journal
from .kernel import (
    ForceRequiresReasonError,
    Kernel,
    KernelResult,
    LockTimeoutError,
    TaskExistsError,
    TaskTransition,
    UnknownTaskError,
)
from .models import TaskCard, WorkLogEntry

__all__ = [
    "AbortOperation",
    "Delta",
    "DispatchContext",
    "DispatchRecord",
    "EdgeEvent",
    "EpicifyEvent",
    "Event",
    "ForceRequiresReasonError",
    "InvalidTransitionError",
    "JournalSink",
    "JsonlJournalSink",
    "Kernel",
    "KernelResult",
    "LockTimeoutError",
    "OperationAborted",
    "Phase",
    "PreDestroyEvent",
    "Subscriber",
    "SubscriberOutcome",
    "Subscription",
    "TaskCard",
    "TaskExistsError",
    "TaskTransition",
    "Topology",
    "TopologyError",
    "UnknownStateError",
    "UnknownTaskError",
    "WorkLogEntry",
    "event_detail",
    "load_topology",
    "read_journal",
]
