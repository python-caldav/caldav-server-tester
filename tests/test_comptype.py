"""Unit tests for CheckSearch._probe_comptype.

These exercise the ``search.comp-type`` decision logic in isolation, with
mocked calendars, so they are fast (not marked slow).

``search.comp-type`` is supported (``full``) when a calendar-query that
specifies a component type returns ONLY objects of that type.  When a typed
query returns wrong-typed objects, the comp-type-less listing of the same
calendar is the ground truth that tells two cases apart:

* a server that silently ignores the filter and returns the whole calendar -
  but drops nothing - is ``unsupported`` (the library post-filters and gets
  the right answer); Open-Xchange behaves this way;
* a server that *drops* correctly-typed objects that the calendar contains
  (e.g. Bedework returning nothing for a VTODO query) is ``broken``.
"""

from datetime import date
from unittest.mock import Mock

from caldav.compatibility_hints import FeatureSet

from caldav_server_tester.checker import ServerQuirkChecker
from caldav_server_tester.checks import CheckSearch


class _Comp(dict):
    """Minimal stand-in for an icalendar component (name + start date in the test base year)."""

    def __init__(self, name: str, day: int = 1) -> None:
        super().__init__()
        self["dtstart"] = True
        self.name = name
        self._start = date(2000, 1, day)

    @property
    def start(self) -> date:
        return self._start


def _obj(name: str, day: int = 1) -> Mock:
    o = Mock()
    o.component = _Comp(name, day)
    return o


def _calendar(events=(), todos=(), journals=()) -> Mock:
    """A mock calendar whose .search dispatches on the component-type kwarg."""
    cal = Mock()

    def _search(**kwargs):
        if kwargs.get("event"):
            return list(events)
        if kwargs.get("todo"):
            return list(todos)
        if kwargs.get("journal"):
            return list(journals)
        return list(events) + list(todos) + list(journals)

    cal.search.side_effect = _search
    return cal


def _make_check(calendar, tasklist=None, journallist=None) -> CheckSearch:
    client = Mock()
    client.features = FeatureSet()
    checker = ServerQuirkChecker(client, debug_mode=None)
    checker.calendar = calendar
    checker.tasklist = tasklist if tasklist is not None else calendar
    checker.journallist = journallist
    ## The probes filter results to the fixture year; the mock objects above use
    ## year-2000 dates, so pin the base year to 2000 for these unit tests.
    checker.fixture_base_year = 2000
    return CheckSearch(checker)


def _support(check: CheckSearch) -> str:
    return check.checker.features_checked.is_supported("search.comp-type", str)


def test_full_single_mixed_calendar() -> None:
    """Correct filtering: each typed query returns only its own type."""
    e1, e2, t1, j1 = _obj("VEVENT"), _obj("VEVENT", 2), _obj("VTODO"), _obj("VJOURNAL")
    cal = _calendar(events=[e1, e2], todos=[t1], journals=[j1])
    check = _make_check(cal)
    check._probe_comptype()
    assert _support(check) == "full"


def test_full_with_separate_task_calendar() -> None:
    """Events and todos in distinct calendars, each correctly filtered."""
    e1 = _obj("VEVENT")
    t1 = _obj("VTODO")
    main = _calendar(events=[e1])
    tasks = _calendar(todos=[t1])
    check = _make_check(main, tasklist=tasks)
    check._probe_comptype()
    assert _support(check) == "full"


def test_broken_when_typed_query_drops_objects() -> None:
    """The Bedework case: a VEVENT query returns everything, but a VTODO query
    returns nothing even though the calendar holds a VTODO - the todo is dropped
    (data loss the client cannot recover from)."""
    e1, t1 = _obj("VEVENT"), _obj("VTODO")
    ## event query returns both (filter ignored); todo query returns nothing;
    ## the comp-type-less listing still reveals the VTODO exists.
    cal = _calendar(events=[e1, t1], todos=[])
    check = _make_check(cal)
    check._probe_comptype()
    assert _support(check) == "broken"


def test_unsupported_when_filter_ignored() -> None:
    """Server ignores the comp-filter and returns everything for every type, but
    drops nothing - the library recovers by post-filtering (the OX case)."""
    e1, t1 = _obj("VEVENT"), _obj("VTODO")
    everything = [e1, t1]
    cal = Mock()
    cal.search.side_effect = lambda **kwargs: list(everything)
    check = _make_check(cal)
    check._probe_comptype()
    assert _support(check) == "unsupported"


def test_unknown_when_nothing_returned() -> None:
    """No typed query returns anything - inconclusive (unknown)."""
    cal = _calendar()
    check = _make_check(cal)
    check._probe_comptype()
    assert _support(check) == "unknown"
