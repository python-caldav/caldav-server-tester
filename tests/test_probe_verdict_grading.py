#!/usr/bin/env python
"""The two probes that shipped without tests, pinned at their verdict boundaries.

Both were found grading a *rejection* or a *non-observation* as though they had
observed something.  In this vocabulary that matters: "unsupported" tells a
client the server silently ignored the request, "ungraceful" that it raised and
the client can catch it, and "unknown" that nobody looked.  These pin the three
apart at the places the probes can reach them.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

from caldav.compatibility_hints import FeatureSet
from caldav.lib.error import DAVError

from caldav_server_tester.checks import CheckRescheduleRecurrenceSeries, CheckTodoNoDtstartSearch

utc = timezone.utc
BASE = 2027


def _checker(**kw):
    checker = Mock()
    checker._features_checked = FeatureSet(kw.pop("observed", None))
    checker.features_checked = checker._features_checked
    checker.debug_mode = None
    checker._client_obj = Mock()
    checker._client_obj.features = FeatureSet()
    checker.expected_features = FeatureSet()
    checker.fixture_base_year = BASE
    for k, v in kw.items():
        setattr(checker, k, v)
    return checker


class TestTodoNoDtstartSearch:
    FEATURE = "search.time-range.todo.no-dtstart"
    SUPPORTED = {"search.time-range.todo": {"support": "full"}}

    def _run(self, tasklist):
        checker = _checker(observed=dict(self.SUPPORTED), tasklist=tasklist)
        CheckTodoNoDtstartSearch(checker)._run_check()
        return checker._features_checked.is_supported(self.FEATURE, str)

    def test_a_server_that_raises_is_ungraceful(self) -> None:
        """This used to return, leaving the feature at its default of 'full' -
        publishing a positive verdict about a server that refused the query."""
        tl = Mock()
        tl.search.side_effect = DAVError("403 Forbidden")
        assert self._run(tl) == "ungraceful"

    def test_no_task_calendar_is_unknown(self) -> None:
        """Also defaulted to 'full', with nothing probed at all."""
        checker = _checker(observed=dict(self.SUPPORTED), tasklist=None)
        CheckTodoNoDtstartSearch(checker)._run_check()
        assert checker._features_checked.is_supported(self.FEATURE, str) == "unknown"

    def test_the_task_being_found_is_full(self) -> None:
        tl = Mock()
        tl.search.return_value = [Mock(data="UID:csc_simple_task2\n")]
        assert self._run(tl) == "full"

    def test_the_task_being_missing_is_unsupported(self) -> None:
        tl = Mock()
        tl.search.return_value = [Mock(data="UID:something_else\n")]
        assert self._run(tl) == "unsupported"

    def test_the_search_measures_the_server_not_the_client(self) -> None:
        """Without post_filter=False the library filters the result by its own
        reading of RFC4791 9.9, and can only drop rows - so the probe would
        blame the server for the client's filter."""
        tl = Mock()
        tl.search.return_value = []
        self._run(tl)
        kwargs = tl.search.call_args.kwargs
        assert kwargs["post_filter"] is False
        assert kwargs["compatibility_workarounds"] is False


class TestRescheduleRecurrenceSeries:
    FEATURE = "save-load.event.recurrences.exception.reschedule"

    def _run(self, *, save_raises=None, dtstart_moves=True):
        """Drive the probe against a calendar that behaves as asked."""
        obj = Mock()
        obj.etag = "etag-1"

        def _load():
            hour = timedelta(hours=1)
            ## the probe compares the master VEVENT's DTSTART with start + 1h;
            ## `start` is 45 days out, day 10, 12:00 UTC
            start = (datetime.now(tz=utc) + timedelta(days=45)).replace(
                day=10, hour=12, minute=0, second=0, microsecond=0
            )
            master = Mock()
            master.name = "VEVENT"
            master.__contains__ = Mock(return_value=False)  ## no RECURRENCE-ID
            master.__getitem__ = Mock(return_value=Mock(dt=start + (hour if dtstart_moves else timedelta(0))))
            obj.icalendar_instance = Mock(subcomponents=[master])

        obj.load.side_effect = _load
        if save_raises is not None:
            obj.save.side_effect = save_raises

        cal = Mock()
        cal.client = None
        checker = _checker(
            observed={"save-load.event.recurrences.exception": {"support": "full"}},
            calendar=cal,
        )
        check = CheckRescheduleRecurrenceSeries(checker)
        check.url_object = lambda *a, **k: obj
        import caldav_server_tester.checks as m

        real = m.url_object
        m.url_object = lambda *a, **k: obj
        try:
            check._run_check()
        finally:
            m.url_object = real
        return checker._features_checked.is_supported(self.FEATURE, str)

    def test_a_rejected_reschedule_is_ungraceful(self) -> None:
        """OX answers 409 Conflict even with a matching etag.  It raised, so the
        client can catch it - this used to be recorded as 'unsupported'."""
        assert self._run(save_raises=DAVError("409 Conflict")) == "ungraceful"

    def test_a_silently_dropped_reschedule_is_unsupported(self) -> None:
        """The PUT is accepted and the series does not move.  This is the
        data-loss case, and the probe could not see it at all before: it
        recorded 'full' on the strength of the PUT not raising."""
        assert self._run(dtstart_moves=False) == "unsupported"

    def test_a_reschedule_that_took_is_full(self) -> None:
        assert self._run(dtstart_moves=True) == "full"
