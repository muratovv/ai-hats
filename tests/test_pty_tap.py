"""HATS-1192: the PTY-tap seam contract (`ai_hats.pty_tap`)."""

from __future__ import annotations

from ai_hats_core.layout import ProjectLayout

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


def test_load_pty_tap_factory_returns_make_fd_pty_tap_when_empty(monkeypatch):
    from ai_hats import pty_tap
    from ai_hats.pty_relay import make_fd_pty_tap

    monkeypatch.setattr(pty_tap, "_pty_tap_entry_points", lambda: [])
    assert pty_tap.load_pty_tap_factory() is make_fd_pty_tap


def test_load_pty_tap_factory_returns_first_factory(monkeypatch):
    from ai_hats import pty_tap

    class DummyEP:
        def __init__(self, name, factory):
            self.name = name
            self._factory = factory

        def load(self):
            return self._factory

    def dummy_factory(*, inject, resize, session):
        return _FullTap(inject=inject, resize=resize, session=session)

    ep1 = DummyEP("relay", dummy_factory)
    monkeypatch.setattr(pty_tap, "_pty_tap_entry_points", lambda: [ep1])

    loaded = pty_tap.load_pty_tap_factory()
    assert loaded is dummy_factory


def test_load_pty_tap_factory_warns_and_takes_first_on_multiple(monkeypatch, caplog):
    from ai_hats import pty_tap

    class DummyEP:
        def __init__(self, name, factory):
            self.name = name
            self._factory = factory

        def load(self):
            return self._factory

    def f1(**kwargs):
        return None

    def f2(**kwargs):
        return None

    ep1 = DummyEP("relay1", f1)
    ep2 = DummyEP("relay2", f2)
    monkeypatch.setattr(pty_tap, "_pty_tap_entry_points", lambda: [ep1, ep2])

    with caplog.at_level("WARNING"):
        loaded = pty_tap.load_pty_tap_factory()
        assert loaded is f1
        assert "Multiple ai_hats.pty_tap entry points found" in caplog.text


def test_load_pty_tap_factory_handles_non_callable_and_load_failure(monkeypatch, caplog):
    from ai_hats import pty_tap
    from ai_hats.pty_relay import make_fd_pty_tap

    class BadEP:
        def __init__(self, name, val, fail=False):
            self.name = name
            self.val = val
            self.fail = fail

        def load(self):
            if self.fail:
                raise ImportError("plugin broken")
            return self.val

    monkeypatch.setattr(pty_tap, "_pty_tap_entry_points", lambda: [BadEP("bad", "not_callable")])
    with caplog.at_level("WARNING"):
        assert pty_tap.load_pty_tap_factory() is make_fd_pty_tap
        assert "did not return a callable" in caplog.text

    monkeypatch.setattr(
        pty_tap, "_pty_tap_entry_points", lambda: [BadEP("broken", None, fail=True)]
    )
    with caplog.at_level("WARNING"):
        assert pty_tap.load_pty_tap_factory() is make_fd_pty_tap
        assert "Failed to load ai_hats.pty_tap entry point" in caplog.text


def test_pty_relay_helpers_and_fd_tap(monkeypatch):
    import os
    from ai_hats.pty_relay import FdPtyTap, parse_fd_env, resolve_pty_fds, wire_raw, wire_resize

    monkeypatch.setenv("AI_HATS_PTY_IN_FD", "10")
    monkeypatch.delenv("AI_HATS_PTY_OUT_FD", raising=False)
    assert parse_fd_env("AI_HATS_PTY_IN_FD") == 10
    assert parse_fd_env("AI_HATS_PTY_OUT_FD") is None

    # Single FD fallback
    assert resolve_pty_fds() == (10, 10)

    monkeypatch.setenv("AI_HATS_PTY_OUT_FD", "11")
    assert resolve_pty_fds() == (10, 11)

    monkeypatch.delenv("AI_HATS_PTY_IN_FD", raising=False)
    monkeypatch.delenv("AI_HATS_PTY_OUT_FD", raising=False)
    assert resolve_pty_fds() == (None, None)

    # FdPtyTap I/O test with pipes
    in_r, in_w = os.pipe()
    out_r, out_w = os.pipe()
    os.set_blocking(in_r, False)
    os.set_blocking(in_w, False)
    os.set_blocking(out_r, False)
    os.set_blocking(out_w, False)

    injected = []
    resized = []

    class DummySession:
        def __init__(self):
            self.audits = []

        def append_audit(self, entry):
            self.audits.append(entry)

    sess = DummySession()

    def mock_inject(b):
        injected.append(b)

    def mock_resize(r, c):
        resized.append((r, c))

    tap = FdPtyTap(
        inject=mock_inject,
        resize=mock_resize,
        session=sess,
        in_fd=in_r,
        out_fd=out_w,
        close_fds=True,
    )

    assert in_r in tap.extra_read_fds()

    tap.on_output(b"hello output")
    buf = os.read(out_r, 1024)
    assert buf == wire_raw(b"hello output")

    os.write(in_w, wire_raw(b"cmd\r"))
    tap.on_readable(in_r)
    assert injected == [b"cmd"]
    assert tap._pending_r

    tap.on_readable(tap._wake_r)
    assert injected == [b"cmd", b"\r"]
    assert len(sess.audits) == 1

    os.write(in_w, wire_resize(100, 30))
    tap.on_readable(in_r)
    assert resized == [(30, 100)]

    tap.close()
    os.close(in_w)
    os.close(out_r)


def test_provider_step_seeds_pty_tap_factory_from_env(monkeypatch):
    from ai_hats.pipeline.steps.launch import Provider

    class DummyWrapRunner:
        def __init__(self, project_dir, composition, session_mgr=None, tracer_factory=None):
            pass

        def run(self, extra_args=None, tags=None, pty_tap_factory=None):
            class DummySession:
                session_id = "s1"
                session_dir = "/tmp/s1"
                trace_path = "/tmp/s1/trace.txt"

            return 0, DummySession()

    monkeypatch.setattr("ai_hats.runtime.WrapRunner", DummyWrapRunner)

    def dummy_factory(**kw):
        return None

    monkeypatch.setattr("ai_hats.pty_tap.load_pty_tap_factory", lambda: dummy_factory)

    step = Provider()

    monkeypatch.delenv("AI_HATS_PTY_IN_FD", raising=False)
    monkeypatch.delenv("AI_HATS_PTY_OUT_FD", raising=False)
    captured = {}

    def mock_run_unset(self, extra_args=None, tags=None, pty_tap_factory=None):
        captured["factory"] = pty_tap_factory

        class DummySession:
            session_id = "s1"
            session_dir = "/tmp/s1"
            trace_path = "/tmp/s1/trace.txt"

        return 0, DummySession()

    monkeypatch.setattr(DummyWrapRunner, "run", mock_run_unset)
    step.run(
        interactive=True,
        layout=ProjectLayout.at("."),
        composition=None,
        session_mgr=None,
        tracer_factory=None,
    )
    assert captured["factory"] is None

    monkeypatch.setenv("AI_HATS_PTY_IN_FD", "10")
    step.run(
        interactive=True,
        layout=ProjectLayout.at("."),
        composition=None,
        session_mgr=None,
        tracer_factory=None,
    )
    assert captured["factory"] is dummy_factory
