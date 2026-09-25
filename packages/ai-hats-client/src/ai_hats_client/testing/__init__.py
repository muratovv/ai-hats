"""A stand-in claude binary for tests: a session with no model and no login."""

from .stub_claude import StubClaude, install

__all__ = ["StubClaude", "install"]
