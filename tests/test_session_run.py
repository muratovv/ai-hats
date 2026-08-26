from types import SimpleNamespace

import pytest

from ai_hats.session_run import SessionRun


def test_session_run_owns_session_and_closes_resources_in_reverse_order() -> None:
    events: list[object] = []
    session = SimpleNamespace(
        session_id="sid",
        log_sys=lambda message: events.append(("notice", message)),
    )

    class Manager:
        def create_session(self, parent_session=None):
            events.append(("create", parent_session))
            return session

    with SessionRun.create(Manager(), parent_session="parent") as run:
        assert run.session is session
        run.defer("session cache", lambda: events.append("cache"))
        run.defer("provider artifacts", lambda: events.append("provider"))

    assert events == [("create", "parent"), "provider", "cache"]


def test_session_run_reports_cleanup_failure_and_continues() -> None:
    events: list[str] = []
    notices: list[str] = []
    session = SimpleNamespace(session_id="sid", log_sys=notices.append)
    run = SessionRun(session)
    run.defer("session cache", lambda: events.append("cache"))

    def fail_provider_cleanup() -> None:
        events.append("provider")
        raise RuntimeError("database busy")

    run.defer("provider artifacts", fail_provider_cleanup)

    with run:
        pass

    assert events == ["provider", "cache"]
    assert notices == ["Provider artifacts cleanup FAILED — RuntimeError: database busy"]


def test_session_run_continues_when_cleanup_notice_cannot_be_written(caplog) -> None:
    events: list[str] = []

    def fail_notice(_message: str) -> None:
        raise OSError("trace unavailable")

    def fail_provider_cleanup() -> None:
        events.append("provider")
        raise RuntimeError("database busy")

    run = SessionRun(SimpleNamespace(session_id="sid", log_sys=fail_notice))
    run.defer("session cache", lambda: events.append("cache"))
    run.defer("provider artifacts", fail_provider_cleanup)

    with run:
        pass

    assert events == ["provider", "cache"]
    assert "cleanup diagnostic failed" in caplog.text


def test_provider_interface_does_not_publish_session_resource_lifecycle() -> None:
    from ai_hats.surfaces import Surface

    assert "recover_session_artifacts" not in Surface.__dict__
    assert "finalize_session_artifacts" not in Surface.__dict__


def test_session_run_closes_remaining_resources_before_reraising_base_exception() -> None:
    events: list[str] = []
    run = SessionRun(SimpleNamespace(session_id="sid", log_sys=lambda _message: None))
    run.defer("session cache", lambda: events.append("cache"))

    def interrupt_provider_cleanup() -> None:
        events.append("provider")
        raise KeyboardInterrupt

    run.defer("provider artifacts", interrupt_provider_cleanup)

    with pytest.raises(KeyboardInterrupt):
        with run:
            pass

    assert events == ["provider", "cache"]
