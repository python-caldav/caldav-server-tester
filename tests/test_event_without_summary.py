"""A VEVENT without SUMMARY.

RFC 5545 section 3.6.1 makes SUMMARY optional in a VEVENT.  Bedework 5 refuses
such an event with 500 missingeventproperty.
"""

from unittest.mock import Mock

import pytest
from caldav.compatibility_hints import FeatureSet
from caldav.lib.error import AuthorizationError, DAVError, NotFoundError, PutError, RateLimitError

from caldav_server_tester.checks import CheckEventWithoutSummary

FEATURE = "save-load.event.no-summary"
URL = "http://example.com/cal/csc_no_summary.ics"


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
    CheckEventWithoutSummary(checker)._run_check()
    return checker, event, checker._features_checked.is_supported(FEATURE, dict)


def test_the_event_carries_no_summary():
    checker, _, _ = _run()
    kwargs = checker.calendar.add_event.call_args.kwargs
    assert "summary" not in kwargs
    assert kwargs["uid"].startswith("csc_")


def test_stored_is_full_and_cleaned_up():
    _, event, observed = _run()
    assert observed["support"] == "full"
    event.delete.assert_called_once()


@pytest.mark.parametrize(
    "error",
    [
        PutError("500 Internal Server Error"),
        PutError("400 Bad Request"),
        AuthorizationError(url=URL, reason="Forbidden"),
    ],
    ids=["500", "400", "401/403"],
)
def test_refused_is_ungraceful(error):
    _, _, observed = _run(add_event=error)
    assert observed["support"] == "ungraceful"
    assert "RFC 5545" in observed["behaviour"]


@pytest.mark.parametrize(
    "error",
    [
        PutError("502 Bad Gateway"),
        PutError("503 Service Unavailable"),
        PutError("504 Gateway Timeout"),
        RateLimitError(reason="429 Too Many Requests"),
        PutError("connection reset"),
    ],
    ids=["502", "503", "504", "rate-limited", "no-status"],
)
def test_a_save_failing_for_no_reason_of_its_own_is_unknown(error):
    """A gateway, an overloaded server or no answer says nothing about SUMMARY,
    and must not end up in a server profile as an RFC 5545 violation."""
    _, _, observed = _run(add_event=error)
    assert observed["support"] == "unknown"
    assert "RFC 5545" not in observed.get("behaviour", "")


def test_accepted_but_not_stored_is_unsupported():
    _, event, observed = _run(load=NotFoundError(reason="404 Not Found"))
    assert observed["support"] == "unsupported"
    event.delete.assert_called_once()


@pytest.mark.parametrize(
    "error,status",
    [(DAVError("500 Internal Server Error"), "500"), (AuthorizationError(url=URL, reason="Forbidden"), "401/403")],
    ids=["500", "401/403"],
)
def test_a_failure_reading_it_back_is_ungraceful(error, status):
    _, event, observed = _run(load=error)
    assert observed["support"] == "ungraceful"
    assert status in observed["behaviour"]
    event.delete.assert_called_once()


def test_an_outage_reading_it_back_is_unknown():
    _, event, observed = _run(load=DAVError("503 Service Unavailable"))
    assert observed["support"] == "unknown"
    event.delete.assert_called_once()
