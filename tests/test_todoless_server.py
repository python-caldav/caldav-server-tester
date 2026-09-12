"""A server with nowhere to put a VTODO must still be profiled.

Bedework 5 is the motivating case: it answers ``200 ok`` for
``supported-calendar-component-set`` on MKCALENDAR, extended MKCOL and
PROPPATCH and then ignores it, so every calendar a client can create is
VEVENT-only and a VTODO PUT into it is 403.  PrepareCalendar used to give up
the moment its task calendar could not be set up - ``_create_test_events``
returned False, no event or journal fixture was ever written, and run_check's
completeness assert then killed the whole run.  A server that cannot store
tasks can still be measured on everything else.
"""

from datetime import date
from typing import Any
from unittest.mock import Mock

import pytest
from caldav import Todo
from caldav.compatibility_hints import FeatureSet
from caldav.lib.error import AuthorizationError

from caldav_server_tester.checker import ServerQuirkChecker
from caldav_server_tester.checks import (
    CheckGetCurrentUserPrincipal,
    CheckMakeDeleteCalendar,
    PrepareCalendar,
)

pytestmark = pytest.mark.slow


def _fixture_object(uid: str) -> Mock:
    """A saved calendar object, good enough for the fixture bookkeeping."""
    obj = Mock()
    obj.component = {"uid": uid}
    obj.icalendar_component = {"UID": uid}
    obj.icalendar_instance.subcomponents = []
    obj.load = Mock()
    return obj


def _todo_hostile_calendar() -> Mock:
    """A calendar that stores anything but a VTODO - 403 on those, as Bedework."""
    calendar = Mock()

    saved: list[Mock] = []

    def save_object(*largs: Any, **kwargs: Any) -> Mock:
        if largs[0] is Todo:
            raise AuthorizationError(url="http://localhost/cal/x.ics", reason="Forbidden")
        uid = kwargs.get("uid", "csc_from_ical")
        obj = _fixture_object(uid)
        saved.append(obj)
        return obj

    calendar.save_object = save_object
    calendar.todos.return_value = []
    calendar.journals.return_value = []
    calendar.search.return_value = []
    ## First call is the existence probe in _find_or_create_calendar and must
    ## come back empty; later calls are fixture bookkeeping and the "is the
    ## calendar empty?" sanity check, which wants something in it.
    calls = {"n": 0}

    def events() -> list[Mock]:
        calls["n"] += 1
        return [] if calls["n"] <= 2 else saved or [_fixture_object("csc_simple_event1")]

    calendar.events = events
    return calendar


def _checker_for_todo_hostile_server() -> tuple[ServerQuirkChecker, Mock]:
    client = Mock()
    client.features = FeatureSet()
    client.features.copyFeatureSet({"test-calendar.compatibility-tests": {}}, collapse=False)
    checker = ServerQuirkChecker(client, debug_mode=None)
    checker.principal = Mock()
    checker.expected_features = client.features
    checker._checks_run.add(CheckGetCurrentUserPrincipal)
    checker._checks_run.add(CheckMakeDeleteCalendar)
    checker._features_checked.copyFeatureSet({"create-calendar": {"support": "full"}}, collapse=False)

    calendar = _todo_hostile_calendar()
    checker.principal.calendar.return_value = calendar
    checker.principal.make_calendar.return_value = calendar
    return checker, calendar


def _measured(checker: ServerQuirkChecker, feature: str) -> bool:
    """Recorded by a probe, rather than defaulted or filled in as unknown."""
    node = checker.features_checked._server_features.get(feature)
    return node is not None and node.get("support") != "unknown"


class TestTodolessServerIsStillProfiled:
    def test_the_mock_really_refuses_todos(self) -> None:
        """Guard on the fixture itself, so a green suite cannot be a vacuous one."""
        calendar = _todo_hostile_calendar()
        with pytest.raises(AuthorizationError):
            calendar.save_object(Todo, summary="t", uid="csc_x", dtstart=date(2027, 1, 7))

    def test_prepare_calendar_completes(self) -> None:
        checker, _calendar = _checker_for_todo_hostile_server()
        PrepareCalendar(checker).run_check()

    ## The assertions below ask what was *measured*: is_supported() on a
    ## feature nobody graded answers the library default, which is "full", and
    ## run_check's handler records "unknown" for whatever an exception left
    ## unprobed - neither tells a probe that ran from one that never did.

    def test_event_fixtures_are_still_written(self) -> None:
        checker, _calendar = _checker_for_todo_hostile_server()
        PrepareCalendar(checker).run_check()
        assert _measured(checker, "save-load.event")
        assert _measured(checker, "save-load.event.recurrences")

    def test_journal_is_still_probed(self) -> None:
        checker, _calendar = _checker_for_todo_hostile_server()
        PrepareCalendar(checker).run_check()
        assert _measured(checker, "save-load.journal")

    def test_only_the_todo_parent_is_graded(self) -> None:
        """The children collapse under the parent's verdict rather than being
        graded one by one."""
        checker, _calendar = _checker_for_todo_hostile_server()
        PrepareCalendar(checker).run_check()
        assert _measured(checker, "save-load.todo")
        assert not checker.features_checked.is_supported("save-load.todo")
        for feature in (
            "save-load.todo.mixed-calendar",
            "save-load.todo.recurrences",
            "save-load.todo.recurrences.count",
        ):
            ## run_check's handler fills in "unknown" for what the mock's late
            ## exception left unprobed; a verdict of its own must not be there.
            assert not _measured(checker, feature), feature

    def test_the_url_probes_still_run(self) -> None:
        """_check_get_by_url / _check_stable_url / _check_encode_at sit after the
        fixture creation, so the early return used to skip them entirely."""
        checker, _calendar = _checker_for_todo_hostile_server()
        PrepareCalendar(checker).run_check()
        assert _measured(checker, "save-load.get-by-url")
