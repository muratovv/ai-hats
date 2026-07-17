"""ai-hats-rack — minimal backlog kernel (epic HATS-1014, K1 HATS-1020).

Public surface: the kernel, the FSM topology loader, the subscriber/dispatch
contract, the event kinds, plus the K2 doc store (fs-as-truth view + frozen
pins) and project-root resolver. Everything else (worktree, ownership, gates,
hooks) is an extension living outside this package.
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
from .docstore import (
    DocInfo,
    DocStore,
    DocumentNameError,
    FrozenDocumentError,
    FrozenPinDriftError,
    RemoveResult,
    UnknownDocumentError,
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
from .resolver import (
    NoProjectRootError,
    RackRoot,
    find_project_root,
    load_root,
    resolve_root,
)

__all__ = [
    "AbortOperation",
    "Delta",
    "DispatchContext",
    "DispatchRecord",
    "DocInfo",
    "DocStore",
    "DocumentNameError",
    "EdgeEvent",
    "EpicifyEvent",
    "Event",
    "ForceRequiresReasonError",
    "FrozenDocumentError",
    "FrozenPinDriftError",
    "InvalidTransitionError",
    "JournalSink",
    "JsonlJournalSink",
    "Kernel",
    "KernelResult",
    "LockTimeoutError",
    "NoProjectRootError",
    "OperationAborted",
    "Phase",
    "PreDestroyEvent",
    "RackRoot",
    "RemoveResult",
    "Subscriber",
    "SubscriberOutcome",
    "Subscription",
    "TaskCard",
    "TaskExistsError",
    "TaskTransition",
    "Topology",
    "TopologyError",
    "UnknownDocumentError",
    "UnknownStateError",
    "UnknownTaskError",
    "WorkLogEntry",
    "event_detail",
    "find_project_root",
    "load_root",
    "load_topology",
    "read_journal",
    "resolve_root",
]
