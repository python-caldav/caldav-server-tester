"""Expanded instances returned in DAV:responses of their own, under one href.

RFC 4918 section 14.24 forbids an href to appear more than once in a
multistatus, and RFC 4791 section 7.8.3 shows every expanded instance of a
resource in one calendar-data.  Bedework 5 answers an expanded calendar-query
with one DAV:response per instance, all under the href of the resource.  The
CalDAV library merges them, so the search result looks right - the probe has
to look at the raw REPORT response.
"""

import contextlib
from datetime import datetime, timezone

import pytest
from caldav.compatibility_hints import FeatureSet
from caldav.lib.error import DAVError
from lxml import etree

from caldav_server_tester.checks import CheckRecurrenceSearch

BASE = 2027
UTC = timezone.utc
FEATURE = "search.recurrences.expanded.event"


class _RecurrenceId:
    def __init__(self, dt):
        self.dt = dt


class _FakeObject:
    def __init__(self, dtstart, recurrence_id=True):
        self.component = {"dtstart": dtstart, "summary": None}
        if recurrence_id:
            self.component["RECURRENCE-ID"] = _RecurrenceId(dtstart)


class _FakeResponse:
    def __init__(self, hrefs):
        body = "".join(
            f"<D:response><D:href>{href}</D:href><D:propstat><D:prop/>"
            "<D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response>"
            for href in hrefs
        )
        self.tree = etree.fromstring(f'<D:multistatus xmlns:D="DAV:">{body}</D:multistatus>')


class _FakeCalendar:
    """Answers every range CheckRecurrenceSearch asks, and the raw REPORT of
    the Feb 12 - Mar 13 query - two instances of one resource - with
    ``hrefs``."""

    def __init__(self, hrefs, initial_recurrence_id=True):
        self.hrefs = hrefs
        self.initial_recurrence_id = initial_recurrence_id
        self.recorder = None

    def search(self, start, end, server_expand=False, **kwargs):
        if not server_expand:
            return [_FakeObject(start)]
        window = (start.month, start.day, end.month, end.day)
        if window == (2, 12, 3, 13):
            if self.recorder is not None:
                self.recorder.append(_FakeResponse(self.hrefs))
            return []
        if window == (2, 12, 2, 14):
            return [
                _FakeObject(datetime(BASE, 2, 12, 12, tzinfo=UTC), self.initial_recurrence_id),
                _FakeObject(datetime(BASE, 2, 13, 12, tzinfo=UTC)),
            ]
        if (start.day, end.day) == (12, 13):
            return [_FakeObject(datetime(BASE, 2, 12, 12, tzinfo=UTC))]
        return []


class _FakeChecker:
    def __init__(self, calendar):
        self._features_checked = FeatureSet({"search.time-range.todo": {"support": "unsupported"}})
        self.features_checked = self._features_checked
        self.calendar = calendar
        self.tasklist = calendar
        self.fixture_base_year = BASE
        self.debug_mode = None
        self._client_obj = None
        self._checks_run = set()
        self.recorded_methods = []

    @contextlib.contextmanager
    def record_responses(self, methods):
        self.recorded_methods.append(tuple(methods))
        captured = []
        self.calendar.recorder = captured
        try:
            yield captured
        finally:
            self.calendar.recorder = None


def _run(calendar):
    checker = _FakeChecker(calendar)
    check = CheckRecurrenceSearch(checker)
    check.expected_features = FeatureSet()
    check._run_check()
    assert checker.recorded_methods == [("REPORT",)]
    return checker._features_checked.is_supported(FEATURE, dict)


def test_one_response_per_instance_is_a_quirk() -> None:
    observed = _run(_FakeCalendar(["/cal/recurring.ics", "/cal/recurring.ics"]))
    assert observed["support"] == "quirk"
    assert "RFC 4918" in observed["behaviour"]


def test_one_response_per_resource_is_full() -> None:
    observed = _run(_FakeCalendar(["/cal/recurring.ics", "/cal/other.ics"]))
    assert observed["support"] == "full"
    assert "behaviour" not in observed


def test_the_recurrence_id_note_is_kept_beside_the_quirk() -> None:
    observed = _run(_FakeCalendar(["/cal/recurring.ics", "/cal/recurring.ics"], initial_recurrence_id=False))
    assert observed["support"] == "quirk"
    assert "RFC 4918" in observed["behaviour"]
    assert "RECURRENCE-ID" in observed["behaviour"]


class _FailingExceptionSearch(_FakeCalendar):
    """The exception-instance search - Feb 13, 11:00-13:00 - raises."""

    def search(self, start, end, server_expand=False, **kwargs):
        if server_expand and (start.day, start.hour, end.hour) == (13, 11, 13):
            raise DAVError(reason="500 Internal Server Error")
        return super().search(start, end, server_expand=server_expand, **kwargs)


def test_a_later_search_failing_keeps_the_event_verdict() -> None:
    """expanded.event is measured before the searches after it run; one of
    those raising must not leave run_check's handler to record it unknown."""
    checker = _FakeChecker(_FailingExceptionSearch(["/cal/recurring.ics", "/cal/other.ics"]))
    check = CheckRecurrenceSearch(checker)
    check.expected_features = FeatureSet()
    with pytest.raises(DAVError):
        check._run_check()
    assert checker._features_checked.is_supported(FEATURE, str, return_defaults=False) == "full"
