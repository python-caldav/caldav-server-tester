"""A VEVENT without SUMMARY.

RFC 5545 section 3.6.1 makes SUMMARY optional in a VEVENT.  Bedework 5 refuses
such an event with 500 missingeventproperty.
"""

from unittest.mock import Mock

from caldav.compatibility_hints import FeatureSet
from caldav.lib.error import NotFoundError, PutError

from caldav_server_tester.checks import CheckEventWithoutSummary

FEATURE = "save-load.event.no-summary"


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


def test_refused_is_ungraceful():
    _, _, observed = _run(add_event=PutError(reason="500 Internal Server Error"))
    assert observed["support"] == "ungraceful"
    assert "RFC 5545" in observed["behaviour"]


def test_accepted_but_not_stored_is_unsupported():
    _, event, observed = _run(load=NotFoundError(reason="404 Not Found"))
    assert observed["support"] == "unsupported"
    event.delete.assert_called_once()
