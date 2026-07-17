"""Shared test helpers. Unique module name on purpose: ``import conftest``
is ambiguous when the workspace suite puts several test dirs on sys.path."""

from __future__ import annotations

from pathlib import Path

from ai_hats_rack.dispatch import Delta, DispatchContext, Phase, Subscription
from ai_hats_rack.kernel import Kernel


class StubSubscriber:
    """Configurable test subscriber: records contexts, optionally acts."""

    def __init__(self, name, subs, action=None):
        self.name = name
        self._subs = list(subs)
        self._action = action
        self.contexts: list[DispatchContext] = []

    def subscriptions(self):
        return self._subs

    def on_event(self, ctx: DispatchContext) -> Delta | None:
        self.contexts.append(ctx)
        if self._action is not None:
            return self._action(ctx)
        return None


class CollectingSink:
    def __init__(self):
        self.records = []

    def record(self, record):
        self.records.append(record)


def in_lock(event_key: str, priority: int = 100) -> Subscription:
    return Subscription(event_key, Phase.IN_LOCK, priority)


def post_lock(event_key: str, priority: int = 100) -> Subscription:
    return Subscription(event_key, Phase.POST_LOCK, priority)


def make_kernel(tasks_dir: Path, **kwargs) -> Kernel:
    kwargs.setdefault("prefix", "T")
    return Kernel(tasks_dir, **kwargs)


def walk(kernel: Kernel, task_id: str, *states: str, cwd: Path) -> None:
    for state in states:
        kernel.transition(task_id, state, actor="test", caller_cwd=cwd)
