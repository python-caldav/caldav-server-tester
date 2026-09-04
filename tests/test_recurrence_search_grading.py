#!/usr/bin/env python
"""A rejected recurrence query is 'ungraceful', a wrong answer is 'unsupported'.

``CheckRecurrenceSearch`` asks the server several time-range questions.  Two of
them can be refused outright by servers that enforce a min/max-date-time (CCS
answers 403), and refusing is not the same as answering wrongly: in the feature
vocabulary 'unsupported' means the request was *silently ignored*, which is the
dangerous case for a client, while 'ungraceful' means the server raised and the
client can catch it.  These pin the distinction at the three places the class
can hit it.
"""

from datetime import datetime

import pytest
from caldav.compatibility_hints import FeatureSet
from caldav.lib.error import AuthorizationError

from caldav_server_tester.checks import CheckRecurrenceSearch

BASE = 2027
UTC = datetime(2000, 1, 1).tzinfo  ## searches are compared on the year only


class _FakeObject:
    """Just enough object for the expansion assertions to short-circuit on.

    They are `and`-chains starting with a comparison against a fixture date, so
    a component that answers "not that date" ends every one of them early.
    """

    component: dict = {"dtstart": None, "summary": None}


class _FakeCalendar:
    """A calendar whose answer depends on which range is asked for.

    ``reject`` names a range (by the ``(year, month, day)`` of its start) that
    the server refuses; everything in ``found`` returns one object, anything
    else returns none.
    """

    def __init__(self, found=(), reject=None, count=1):
        self.found = set(found)
        self.reject = reject
        self.count = count

    def search(self, **kwargs):
        start = kwargs["start"]
        key = (start.year, start.month, start.day)
        if self.reject is not None and key == self.reject:
            raise AuthorizationError("403 Forbidden")
        if key in self.found:
            return [_FakeObject() for _ in range(self.count)]
        return []


class _FakeChecker:
    def __init__(self, calendar, tasklist=None):
        self._features_checked = FeatureSet({"search.time-range.todo": {"support": "unsupported"}})
        self.features_checked = self._features_checked
        self.calendar = calendar
        self.tasklist = tasklist if tasklist is not None else calendar
        self.fixture_base_year = BASE
        self.debug_mode = None
        self._client_obj = None
        self._checks_run = set()


## The ranges CheckRecurrenceSearch has to see answered to reach the far-future
## probe: the Jan precondition and the Feb 13 exception.
_REACH_FAR_FUTURE = {(BASE, 1, 12), (BASE, 2, 13)}
_ALL_FEATURES = CheckRecurrenceSearch.features_to_be_checked


def _run(calendar):
    checker = _FakeChecker(calendar)
    check = CheckRecurrenceSearch(checker)
    check.expected_features = FeatureSet()
    check._run_check()
    return checker._features_checked


@pytest.mark.parametrize("feature", sorted(_ALL_FEATURES))
def test_a_rejected_precondition_is_ungraceful(feature) -> None:
    """The server refused the Jan range: it raised, so nothing is 'unsupported'."""
    observed = _run(_FakeCalendar(reject=(BASE, 1, 12)))
    assert observed.is_supported(feature, str) == "ungraceful"


@pytest.mark.parametrize("feature", sorted(_ALL_FEATURES))
def test_a_wrong_precondition_answer_is_still_unsupported(feature) -> None:
    """The server answered the Jan range, with the wrong objects.  Silently wrong."""
    observed = _run(_FakeCalendar(found={(BASE, 1, 12)}, count=2))
    assert observed.is_supported(feature, str) == "unsupported"


def test_a_rejected_far_future_query_is_ungraceful() -> None:
    """The 403 CCS answers a max-date-time violation with."""
    observed = _run(_FakeCalendar(found=_REACH_FAR_FUTURE, reject=(BASE + 45, 3, 12)))
    assert observed.is_supported("search.recurrences.includes-implicit.infinite-scope", str) == "ungraceful"


def test_a_far_future_query_that_finds_nothing_is_unsupported() -> None:
    """A sliding window hides the occurrence without complaining - that is the
    silent case, and the one 'unsupported' is for."""
    observed = _run(_FakeCalendar(found=_REACH_FAR_FUTURE))
    assert observed.is_supported("search.recurrences.includes-implicit.infinite-scope", str) == "unsupported"


def test_a_rejected_allday_query_is_not_folded_into_the_datetime_verdict() -> None:
    """The all-day query raising used to be recorded as whatever the datetime
    query said, which asserts something the server never answered."""
    observed = _run(_FakeCalendar(found=_REACH_FAR_FUTURE | {(BASE, 2, 12)}, reject=(BASE, 3, 1)))
    assert observed.is_supported("search.recurrences.includes-implicit.event", str) == "ungraceful"
