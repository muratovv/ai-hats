"""HATS-1192: the PTY-tap seam contract (`ai_hats.pty_tap`)."""

from __future__ import annotations

from ai_hats.pty_tap import NullPtyTap, PtyTap, PtyTapFactory


class _FullTap:
    def __init__(self, *, inject, resize, session):
        self.inject = inject
        self.resize = resize
        self.session = session

    def extra_read_fds(self):
        return []

    def on_output(self, data):  # noqa: D401
        pass

    def on_readable(self, fd):
        pass

    def close(self):
        pass


class _PartialTap:
    """Missing on_readable + close — not a PtyTap."""

    def extra_read_fds(self):
        return []

    def on_output(self, data):
        pass


def test_full_tap_satisfies_protocol():
    tap = _FullTap(inject=lambda b: None, resize=lambda r, c: None, session=object())
    assert isinstance(tap, PtyTap)


def test_partial_tap_fails_protocol():
    assert not isinstance(_PartialTap(), PtyTap)


def test_null_tap_satisfies_protocol_and_is_inert():
    tap = NullPtyTap()
    assert isinstance(tap, PtyTap)
    assert tap.extra_read_fds() == []
    tap.on_output(b"x")  # no-ops must not raise
    tap.on_readable(3)
    tap.close()
    tap.close()  # idempotent


def test_provider_step_declares_pty_tap_factory_optional():
    """The provider step forwards a funnel-seeded factory into the HITL seam."""
    from ai_hats.pipeline.steps.launch import Provider

    assert "pty_tap_factory" in Provider().io.optional


def test_factory_call_convention_builds_a_tap():
    """A PtyTapFactory is called factory(*, inject, resize, session) -> PtyTap."""
    factory: PtyTapFactory = _FullTap
    calls = []
    tap = factory(
        inject=lambda b: calls.append(("inject", b)),
        resize=lambda r, c: calls.append(("resize", r, c)),
        session=object(),
    )
    assert isinstance(tap, PtyTap)
    # the runtime callbacks are wired through, not the loop's business here
    tap.inject(b"x")
    tap.resize(24, 80)
    assert calls == [("inject", b"x"), ("resize", 24, 80)]
