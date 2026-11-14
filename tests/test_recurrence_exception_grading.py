"""save-load.event.recurrences.exception, graded on what was observed.

A failed listing of the calendar used to be graded "ungraceful", without a
word of behaviour, and the event missing from it "unsupported".  Robur was
reported "ungraceful" run after run that way, while storing a master VEVENT
with its exception correctly.

What is stored is read by a GET, not off the listing: Bedework 3.10 and 5
leave the exception out of the calendar-data of a search, and were graded
as not storing it.  The difference between the two is
search.recurrences.exception.in-calendar-data.
"""

import inspect
from unittest.mock import Mock

import icalendar
import niquests
import pytest
from caldav.calendarobjectresource import CalendarObjectResource
from caldav.compatibility_hints import FeatureSet
from caldav.lib.error import AuthorizationError, PutError, ReportError

from caldav_server_tester.checks import PrepareCalendar

FEATURE = "save-load.event.recurrences.exception"
IN_SEARCH = "search.recurrences.exception.in-calendar-data"
UID = "csc_monthly_recurring_with_exception"
URL = "http://example.com/cal/csc_monthly_recurring_with_exception.ics"


def _cal(uid, vevents):
    cal = icalendar.Calendar()
    for _ in range(vevents):
        ev = icalendar.Event()
        ev.add("uid", uid)
        cal.add_component(ev)
    return cal


def _obj(uid=UID, vevents=2, stored=None, get_error=None):
    """An object as the listing has it, with ``vevents`` VEVENTs; a GET
    (``load()``) gives it ``stored`` VEVENTs, the same number by default."""
    obj = Mock()
    obj.icalendar_instance = _cal(uid, vevents)
    obj.icalendar_component = obj.icalendar_instance.subcomponents[0]

    def load(*args, **kwargs):
        obj.load_kwargs = kwargs
        if get_error is not None:
            raise get_error
        obj.icalendar_instance = _cal(uid, vevents if stored is None else stored)
        return obj

    obj.load.side_effect = load
    return obj


def _unparsable():
    obj = Mock()
    type(obj).icalendar_component = property(lambda self: (_ for _ in ()).throw(ValueError("not iCalendar")))
    return obj


def _run(add=None, listing=None, listing_error=None, feature=FEATURE):
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
    return checker._features_checked.is_supported(feature, dict)


def _run_both(**kwargs):
    """Both verdicts: save-load first, then the search one."""
    checker = Mock()
    checker._features_checked = FeatureSet()
    checker.debug_mode = None
    calendar = Mock()
    calendar.events.return_value = kwargs.pop("listing")
    PrepareCalendar(checker)._store_recurrence_with_exception(calendar, Mock(), 2027)
    fs = checker._features_checked
    return fs.is_supported(FEATURE, dict), fs.is_supported(IN_SEARCH, dict)


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


def test_a_search_dropping_the_exception_is_not_a_storage_failure():
    """Bedework: the listing holds the master only, a GET has both."""
    stored, in_search = _run_both(listing=[_obj(vevents=1, stored=2)])
    assert stored["support"] == "full"
    assert in_search["support"] == "unsupported"
    assert "master only" in in_search["behaviour"]


def test_a_search_with_the_whole_object_is_full():
    stored, in_search = _run_both(listing=[_obj()])
    assert stored["support"] == "full"
    assert in_search["support"] == "full"


def test_an_exception_not_stored_is_graded_on_the_get():
    stored, in_search = _run_both(listing=[_obj(vevents=1, stored=1)])
    assert stored["support"] == "unsupported"
    assert in_search["support"] == "unknown"


def test_split_objects_leave_the_search_verdict_unknown():
    stored, in_search = _run_both(listing=[_obj(vevents=1), _obj(vevents=1)])
    assert stored["support"] == "unsupported"
    assert in_search["support"] == "unknown"


def test_a_failed_get_is_unknown():
    stored, in_search = _run_both(
        listing=[_obj(vevents=1, get_error=niquests.exceptions.ConnectionError("Connection reset by peer"))]
    )
    assert stored["support"] == "unknown"
    assert "GET" in stored["behaviour"]
    assert in_search["support"] == "unknown"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"add": PutError("400 Bad Request")},
        {"listing_error": ReportError("500 Internal Server Error")},
        {"listing": [_obj(uid="csc_something_else")]},
    ],
    ids=["put-refused", "listing-failed", "not-listed"],
)
def test_every_early_exit_grades_the_search_feature_too(kwargs):
    """PrepareCalendar asserts that every feature it declares got a verdict."""
    observed = _run(feature=IN_SEARCH, **kwargs)
    assert observed["support"] == "unknown"
    assert observed["behaviour"]


def test_the_stored_event_is_read_by_the_get_alone():
    """load() falls back to a multiget, and after a 404 to a search by UID:
    the probe would then compare a search with a search."""
    obj = _obj(vevents=1, stored=2)
    _run_both(listing=[obj])
    if "fallbacks" in inspect.signature(CalendarObjectResource.load).parameters:
        assert obj.load_kwargs == {"fallbacks": False}
    else:
        assert obj.load_kwargs == {"multiget_fallback": False}
