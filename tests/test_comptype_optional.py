"""Unit tests for CheckSearch._probe_comptype_optional.

These exercise the search.comp-type.optional decision logic in isolation,
with mocked calendars, so they are fast (not marked slow).

Regression target: the probe used to compare a single comp-type-less search
against the global checker.cnt counter.  On servers that store journals (or
tasks) in a separate calendar - because save-load.journal.mixed-calendar is
unsupported - cnt counts the journal but the comp-type-less search on the main
calendar cannot return it, so fully-working servers (Baikal, Davis, CCS) were
mislabelled "fragile"/"ungraceful".  See
https://github.com/python-caldav/caldav/issues/681
"""

from datetime import date
from unittest.mock import Mock

from caldav.compatibility_hints import FeatureSet

from caldav_server_tester.checker import ServerQuirkChecker
from caldav_server_tester.checks import CheckSearch


class _Comp(dict):
    """Minimal stand-in for an icalendar component as seen by _filter_2000."""

    def __init__(self, start: date) -> None:
        super().__init__()
        self["dtstart"] = True
        self._start = start

    @property
    def start(self) -> date:
        return self._start


def _obj(url: str, day: int = 1) -> Mock:
    """A calendar object in year 2000 (so it survives _filter_2000)."""
    o = Mock()
    o.url = url
    o.component = _Comp(date(2000, 1, day))
    return o


def _calendar(bare=(), events=(), todos=(), journals=()) -> Mock:
    """A mock calendar whose .search dispatches on the component-type kwarg."""
    cal = Mock()

    def _search(**kwargs):
        if kwargs.get("event"):
            return list(events)
        if kwargs.get("todo"):
            return list(todos)
        if kwargs.get("journal"):
            return list(journals)
        return list(bare)

    cal.search.side_effect = _search
    return cal


def _make_check(calendar, tasklist=None, journallist=None) -> CheckSearch:
    client = Mock()
    client.features = FeatureSet()
    checker = ServerQuirkChecker(client, debug_mode=None)
    checker.calendar = calendar
    checker.tasklist = tasklist if tasklist is not None else calendar
    checker.journallist = journallist
    return CheckSearch(checker)


def _support(check: CheckSearch) -> str:
    return check.checker.features_checked.is_supported("search.comp-type.optional", str)


def test_full_simple_single_calendar() -> None:
    e1, e2, t1 = _obj("e1"), _obj("e2"), _obj("t1")
    cal = _calendar(bare=[e1, e2, t1], events=[e1, e2], todos=[t1])
    check = _make_check(cal)
    check._probe_comptype_optional()
    assert _support(check) == "full"


def test_full_when_journal_in_separate_calendar() -> None:
    """The Baikal/Davis case: journal lives in its own calendar.

    The comp-type-less search on the main calendar returns the events+todos,
    and the comp-type-less search on the journal calendar returns the journal.
    Together they cover every comp-type-specific result -> full.  (cnt would be
    inflated here, which is exactly what used to break this.)
    """
    e1, e2, t1, j1 = _obj("e1"), _obj("e2"), _obj("t1"), _obj("j1")
    main = _calendar(bare=[e1, e2, t1], events=[e1, e2], todos=[t1])
    journals = _calendar(bare=[j1], journals=[j1])
    check = _make_check(main, tasklist=main, journallist=journals)
    check._probe_comptype_optional()
    assert _support(check) == "full"


def test_full_with_separate_task_calendar() -> None:
    """The CCS case: events in one calendar, todos in a dedicated one."""
    e1, e2, t1 = _obj("e1"), _obj("e2"), _obj("t1")
    main = _calendar(bare=[e1, e2], events=[e1, e2])
    tasks = _calendar(bare=[t1], todos=[t1])
    check = _make_check(main, tasklist=tasks)
    check._probe_comptype_optional()
    assert _support(check) == "full"


def test_unsupported_when_comptypeless_returns_nothing() -> None:
    """The SOGo case: comp-type-less query silently returns nothing."""
    e1, t1 = _obj("e1"), _obj("t1")
    cal = _calendar(bare=[], events=[e1], todos=[t1])
    check = _make_check(cal)
    check._probe_comptype_optional()
    assert _support(check) == "unsupported"


def test_fragile_when_todos_dropped() -> None:
    """The Ox case: comp-type-less query returns events but drops todos."""
    e1, e2, t1 = _obj("e1"), _obj("e2"), _obj("t1")
    cal = _calendar(bare=[e1, e2], events=[e1, e2], todos=[t1])
    check = _make_check(cal)
    check._probe_comptype_optional()
    assert _support(check) == "fragile"


def test_ungraceful_when_comptypeless_raises() -> None:
    cal = Mock()
    cal.search.side_effect = RuntimeError("boom")
    check = _make_check(cal)
    check._probe_comptype_optional()
    assert _support(check) == "ungraceful"
