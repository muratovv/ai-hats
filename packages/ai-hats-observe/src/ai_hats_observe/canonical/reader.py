"""Readers: one per surface, each turning a session record into canonical events.

A reader is an object rather than a call because following a live run requires
remembering where the last read stopped. That memory is what keeps exactly-once
emission true: read a source that has grown and you get only what is new, never
a re-announcement of what was already emitted.

The same object serves a finished record and a growing one — a reader over a
transcript that stopped growing simply runs out of events.
"""

from __future__ import annotations

from typing import AsyncIterator, Iterator, Protocol, runtime_checkable

from .events import Event


@runtime_checkable
class EventReader(Protocol):
    """A surface whose session record can be read without awaiting."""

    def read(self) -> Iterator[Event]:
        """Yield events appended since the previous call."""
        ...

    @property
    def exhausted(self) -> bool:
        """Whether the source can still produce more.

        False for a run in progress, so a follower knows to come back rather
        than treating a pause in output as the end of the run.
        """
        ...

    def close(self) -> None:
        """Declare the run over: the next ``read()`` drains whatever a live
        source was holding back, and ``exhausted`` may then turn true."""
        ...


@runtime_checkable
class AsyncEventReader(Protocol):
    """A surface delivered as an awaitable stream.

    Same vocabulary, different transport: a consumer written against the events
    works with either reader.
    """

    def read(self) -> AsyncIterator[Event]: ...

    @property
    def exhausted(self) -> bool: ...
