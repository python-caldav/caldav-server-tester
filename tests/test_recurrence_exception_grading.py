"""save-load.event.recurrences.exception, graded on what was observed.

A failed listing of the calendar used to be graded "ungraceful", without a
word of behaviour, and the event missing from it "unsupported".  Robur was
reported "ungraceful" run after run that way, while storing a master VEVENT
with its exception correctly.
"""

from unittest.mock import Mock

import icalendar
import niquests
import pytest
from caldav.compatibility_hints import FeatureSet
from caldav.lib.error import AuthorizationError, PutError, ReportError

from caldav_server_tester.checks import PrepareCalendar

FEATURE = "save-load.event.recurrences.exception"
UID = "csc_monthly_recurring_with_exception"
URL = "http://example.com/cal/csc_monthly_recurring_with_exception.ics"


def _obj(uid=UID, vevents=2):
    cal = icalendar.Calendar()
    for _ in range(vevents):
        ev = icalendar.Event()
        ev.add("uid", uid)
        cal.add_component(ev)
    obj = Mock()
    obj.icalendar_instance = cal
    obj.icalendar_component = cal.subcomponents[0]
    return obj


def _unparsable():
    obj = Mock()
    type(obj).icalendar_component = property(lambda self: (_ for _ in ()).throw(ValueError("not iCalendar")))
    return obj


def _run(add=None, listing=None, listing_error=None):
    checker = Mock()
    checker._features_checked = FeatureSet()
    checker.debug_mode = None
    calendar = Mock()
    if listing_error is not None:
        calendar.events.side_effect = listing_error
    else:
        calendar.events.return_value = listing if listing is not None else [_obj()]
    add_if_not_existing = Mock()
    if add is not None:
        add_if_not_existing.side_effect = add
    PrepareCalendar(checker)._store_recurrence_with_exception(calendar, add_if_not_existing, 2027)
    return checker._features_checked.is_supported(FEATURE, dict)


def test_master_and_exception_kept_together_is_full():
    assert _run()["support"] == "full"


def test_split_into_two_objects_is_unsupported():
    assert _run(listing=[_obj(vevents=1), _obj(vevents=1)])["support"] == "unsupported"


def test_expanded_into_three_vevents_is_unsupported():
    assert _run(listing=[_obj(vevents=3)])["support"] == "unsupported"


def test_someone_elses_unparsable_object_is_skipped():
    """The listing covers the whole shared test calendar."""
    assert _run(listing=[_unparsable(), _obj()])["support"] == "full"


@pytest.mark.parametrize(
    "error,status",
    [(PutError("400 Bad Request"), "400"), (AuthorizationError(url=URL, reason="Forbidden"), "401/403")],
    ids=["400", "403"],
)
def test_a_refused_put_is_ungraceful_and_says_so(error, status):
    observed = _run(add=error)
    assert observed["support"] == "ungraceful"
    assert status in observed["behaviour"]


@pytest.mark.parametrize(
    "error",
    [
        niquests.exceptions.ConnectionError("Connection reset by peer"),
        PutError("502 Bad Gateway"),
        PutError("connection reset"),
    ],
    ids=["connection-reset", "502", "no-status"],
)
def test_a_put_failing_without_an_answer_is_unknown(error):
    observed = _run(add=error)
    assert observed["support"] == "unknown"
    assert observed["behaviour"]


def test_a_bug_on_our_side_is_not_graded_as_no_answer():
    with pytest.raises(TypeError):
        _run(add=TypeError("unexpected keyword argument"))


@pytest.mark.parametrize(
    "error",
    [ReportError("500 Internal Server Error"), niquests.exceptions.ConnectionError("Connection reset by peer")],
    ids=["500", "connection-reset"],
)
def test_a_failed_listing_is_unknown(error):
    """Listing the calendar is not what this feature is about."""
    observed = _run(listing_error=error)
    assert observed["support"] == "unknown"
    assert "listing" in observed["behaviour"]


def test_own_object_missing_from_the_listing_is_unknown():
    """Nothing was split - the object just was not listed (yet)."""
    observed = _run(listing=[_obj(uid="csc_something_else")])
    assert observed["support"] == "unknown"
    assert "listing" in observed["behaviour"]
