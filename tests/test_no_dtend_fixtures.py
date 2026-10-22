"""PrepareCalendar against a server refusing events without an end.

iCloud answers 404 to a VEVENT with neither DTEND nor DURATION.
PrepareCalendar used to stop at the first such fixture, so none of the later
ones were written and every check reading them reported a false verdict -
e.g. search.recurrences unsupported.  A refused all-day fixture is now stored
with the one-day DTEND RFC 5545 section 3.6.1 implies, and the timed one is
skipped.
"""

from datetime import timedelta
from typing import Any
from unittest.mock import Mock

import pytest
from caldav import Event
from caldav.compatibility_hints import FeatureSet
from caldav.lib.error import AuthorizationError, NotFoundError, PutError, RateLimitError

from caldav_server_tester import checks
from caldav_server_tester.checker import ServerQuirkChecker
from caldav_server_tester.checks import (
    CheckGetCurrentUserPrincipal,
    CheckMakeDeleteCalendar,
    PrepareCalendar,
)

pytestmark = pytest.mark.slow

URL = "http://example.com/cal/csc_simple_event2.ics"
ALL_DAY_FIXTURES = ("csc_simple_event3", "csc_monthly_recurring_allday_event")


def _fixture_object(uid: str) -> Mock:
    """A saved calendar object, good enough for the fixture bookkeeping."""
    obj = Mock()
    obj.component = {"uid": uid}
    obj.icalendar_component = {"UID": uid}
    obj.icalendar_instance.subcomponents = []
    return obj


def _checker_for_server_refusing(error: Exception) -> tuple[ServerQuirkChecker, list[dict]]:
    """A server answering ``error`` to a VEVENT with neither DTEND nor DURATION."""
    saved: list[dict] = []

    def save_object(*largs: Any, **kwargs: Any) -> Mock:
        if largs[0] is Event and "dtstart" in kwargs and "dtend" not in kwargs and "duration" not in kwargs:
            raise error
        saved.append(kwargs)
        return _fixture_object(kwargs.get("uid", "csc_from_ical"))

    calendar = Mock()
    calendar.save_object = save_object
    calendar.todos.return_value = []
    calendar.journals.return_value = []
    calendar.search.return_value = []
    ## First calls are the existence probes and must come back empty; later
    ## ones are the "is the calendar empty?" sanity check, which wants
    ## something in it.
    calls = {"n": 0}

    def events() -> list[Mock]:
        calls["n"] += 1
        return [] if calls["n"] <= 2 else [_fixture_object("csc_simple_event1")]

    calendar.events = events

    client = Mock()
    client.features = FeatureSet()
    client.features.copy_feature_set({"test-calendar.compatibility-tests": {}}, collapse=False)
    checker = ServerQuirkChecker(client, debug_mode=None)
    checker.principal = Mock()
    checker.expected_features = client.features
    checker._checks_run.add(CheckGetCurrentUserPrincipal)
    checker._checks_run.add(CheckMakeDeleteCalendar)
    checker._features_checked.copy_feature_set({"create-calendar": {"support": "full"}}, collapse=False)
    checker.principal.calendar.return_value = calendar
    checker.principal.make_calendar.return_value = calendar
    return checker, saved


@pytest.fixture(autouse=True)
def _nothing_behind_a_failed_put(monkeypatch):
    """add_if_not_existing follows a failed PUT with a GET of the object; on a
    server that refused it, there is nothing there."""
    obj = Mock()
    obj.load.side_effect = NotFoundError("404 Not Found\n\n")
    monkeypatch.setattr(checks, "url_object", lambda *a, **kw: obj)


REFUSALS = pytest.mark.parametrize(
    "error",
    [
        PutError("404 Not Found"),
        AuthorizationError(url=URL, reason="Forbidden"),
        PutError("500 Internal Server Error"),
    ],
    ids=["404", "403", "500"],
)


@REFUSALS
def test_the_later_fixtures_are_written(error):
    checker, saved = _checker_for_server_refusing(error)
    PrepareCalendar(checker).run_check()
    uids = {kwargs.get("uid") for kwargs in saved}
    assert {"csc_monthly_recurring_event", "csc_event_with_duration"} <= uids
    assert checker.features_checked.is_supported("save-load.event.recurrences")


@REFUSALS
def test_all_day_fixtures_get_the_implied_dtend(error):
    checker, saved = _checker_for_server_refusing(error)
    PrepareCalendar(checker).run_check()
    by_uid = {kwargs.get("uid"): kwargs for kwargs in saved}
    for uid in ALL_DAY_FIXTURES:
        assert by_uid[uid]["dtend"] == by_uid[uid]["dtstart"] + timedelta(days=1)
    assert "csc_simple_event2" not in by_uid


@pytest.mark.parametrize(
    "error",
    [
        PutError("503 Service Unavailable"),
        RateLimitError(reason="429 Too Many Requests"),
        AuthorizationError(url=URL, reason="Unauthorized"),
        PutError("connection reset"),
    ],
    ids=["503", "rate-limited", "401", "no-status"],
)
def test_no_answer_changes_no_fixture(error):
    """A failure that is no answer from the server says nothing about events
    without an end: no fixture is stored with a DTEND it should not have."""
    checker, saved = _checker_for_server_refusing(error)
    PrepareCalendar(checker).run_check()
    assert not any(kwargs.get("uid") in ALL_DAY_FIXTURES for kwargs in saved)
