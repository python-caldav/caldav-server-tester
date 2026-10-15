"""A VEVENT with neither DTEND nor DURATION.

RFC 5545 section 3.6.1 allows one: an event on a date lasts that day, an event
at a date-time ends when it starts.  iCloud refuses both with a 404.
"""

from datetime import date, datetime
from unittest.mock import Mock

import pytest
from caldav.compatibility_hints import FeatureSet
from caldav.lib.error import AuthorizationError, DAVError, NotFoundError, PutError, RateLimitError

from caldav_server_tester.checks import CheckEventWithoutDtend

FEATURE = "save-load.event.no-dtend"
URL = "http://example.com/cal/csc_no_dtend.ics"


def _run(add_event=None, load=None):
    checker = Mock()
    checker._features_checked = FeatureSet()
    checker.debug_mode = None
    event = Mock()
    if load is not None:
        event.load.side_effect = load
    if add_event is not None:
        checker.calendar.add_event.side_effect = add_event
    else:
        checker.calendar.add_event.return_value = event
    CheckEventWithoutDtend(checker)._run_check()
    return checker, event, checker._features_checked.is_supported(FEATURE, dict)


def test_the_events_carry_no_end():
    checker, _, _ = _run()
    calls = checker.calendar.add_event.call_args_list
    assert [type(c.kwargs["dtstart"]) for c in calls] == [datetime, date]
    for c in calls:
        assert "dtend" not in c.kwargs and "duration" not in c.kwargs
        assert c.kwargs["uid"].startswith("csc_")


def test_stored_is_full_and_cleaned_up():
    _, event, observed = _run()
    assert observed["support"] == "full"
    assert event.delete.call_count == 2


@pytest.mark.parametrize(
    "error",
    [
        PutError("404 Not Found"),
        PutError("500 Internal Server Error"),
        AuthorizationError(url=URL, reason="Forbidden"),
    ],
    ids=["404", "500", "403"],
)
def test_refused_is_ungraceful(error):
    _, _, observed = _run(add_event=error)
    assert observed["support"] == "ungraceful"
    assert "RFC 5545" in observed["behaviour"]


def test_both_refused_names_both():
    _, _, observed = _run(add_event=PutError("404 Not Found"))
    assert "with a start time" in observed["behaviour"]
    assert "on a date" in observed["behaviour"]


def test_a_refusal_outweighs_an_outage():
    _, _, observed = _run(add_event=[PutError("503 Service Unavailable"), PutError("404 Not Found")])
    assert observed["support"] == "ungraceful"


def test_an_all_day_event_refused_alone_is_ungraceful():
    event = Mock()
    _, _, observed = _run(add_event=[event, PutError("404 Not Found")])
    assert observed["support"] == "ungraceful"
    assert "on a date" in observed["behaviour"]
    event.delete.assert_called_once()


@pytest.mark.parametrize(
    "error",
    [
        PutError("502 Bad Gateway"),
        PutError("503 Service Unavailable"),
        PutError("504 Gateway Timeout"),
        RateLimitError(reason="429 Too Many Requests"),
        AuthorizationError(url=URL, reason="Unauthorized"),
        PutError("connection reset"),
    ],
    ids=["502", "503", "504", "rate-limited", "401", "no-status"],
)
def test_a_save_failing_for_no_reason_of_its_own_is_unknown(error):
    """A gateway, an overloaded server or no answer says nothing about events
    without an end, and must not end up in a server profile as a refusal."""
    _, _, observed = _run(add_event=error)
    assert observed["support"] == "unknown"
    assert "RFC 5545" not in observed.get("behaviour", "")


def test_accepted_but_not_stored_is_unsupported():
    _, event, observed = _run(load=NotFoundError(reason="404 Not Found"))
    assert observed["support"] == "unsupported"
    assert event.delete.call_count == 2


@pytest.mark.parametrize(
    "error,status",
    [(DAVError("500 Internal Server Error"), "500"), (AuthorizationError(url=URL, reason="Forbidden"), "401/403")],
    ids=["500", "403"],
)
def test_a_failure_reading_it_back_is_ungraceful(error, status):
    _, event, observed = _run(load=error)
    assert observed["support"] == "ungraceful"
    assert status in observed["behaviour"]
    assert event.delete.call_count == 2


def test_an_outage_reading_it_back_is_unknown():
    _, event, observed = _run(load=DAVError("503 Service Unavailable"))
    assert observed["support"] == "unknown"
    assert event.delete.call_count == 2
