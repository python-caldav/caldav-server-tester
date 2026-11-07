"""Whether a time-range search finds an event or task overlapping the range
without starting inside it.

RFC 4791 section 9.9 has an object overlap a range; Robur only finds the
ones whose DTSTART is in it.
"""

from unittest.mock import Mock

import pytest
from caldav.compatibility_hints import FeatureSet
from caldav.lib.error import DAVError

from caldav_server_tester.checks import CheckTimeRangeOverlap

from .fake_time_range import fake_search
from .test_floating_event import RewritingServer

BASE = "http://example.com/cal/"
TASKS = "http://example.com/tasks/"
EVENT = "search.time-range.event.overlap"
TODO = "search.time-range.todo.overlap"


def _run(server, mode="overlap", search=None, tasklist=True, event_range="full", todo_range="full"):
    checker = Mock()
    checker._features_checked = FeatureSet(
        {"search.time-range.event": {"support": event_range}, "search.time-range.todo": {"support": todo_range}}
    )
    checker.features_checked = checker._features_checked
    checker.debug_mode = None
    checker._client_obj = server
    checker.fixture_base_year = 2027
    checker.calendar.url.join.side_effect = lambda name: BASE + name
    checker.calendar.search.side_effect = search or fake_search(server, mode=mode)
    if tasklist:
        checker.tasklist.url.join.side_effect = lambda name: TASKS + name
        checker.tasklist.search.side_effect = search or fake_search(server, mode=mode)
    else:
        checker.tasklist = None
    CheckTimeRangeOverlap(checker)._run_check()
    return tuple(checker._features_checked.is_supported(f, return_type=dict) for f in (EVENT, TODO))


def test_found_overlapping_is_full():
    server = RewritingServer()
    event, todo = _run(server)
    assert event["support"] == "full"
    assert todo["support"] == "full"
    assert not server.objects


def test_the_task_goes_to_the_task_calendar():
    sent = {}

    class Recording(RewritingServer):
        def put(self, url, body, headers=None):
            sent[url] = body
            return super().put(url, body, headers)

    _run(Recording())
    assert any(url.startswith(TASKS) and "BEGIN:VTODO" in body for url, body in sent.items())
    assert any(url.startswith(BASE) and "BEGIN:VEVENT" in body for url, body in sent.items())


def test_a_range_starting_at_the_end_does_not_confuse_it():
    server = RewritingServer()
    assert _run(server, search=fake_search(server, inclusive=True))[0]["support"] == "full"


def test_found_only_by_its_start_is_unsupported():
    """Robur"""
    server = RewritingServer()
    event, todo = _run(server, mode="dtstart")
    assert event["support"] == "unsupported"
    assert "start" in event["behaviour"]
    assert todo["support"] == "unsupported"
    assert not server.objects


def test_missed_by_a_range_straddling_its_end_is_unsupported():
    server = RewritingServer()
    search = fake_search(server)

    def no_straddle(start, end, **kwargs):
        return [] if (start.hour, start.minute) == (12, 45) else search(start=start, end=end, **kwargs)

    event, todo = _run(server, search=no_straddle)
    assert event["support"] == "unsupported"
    assert "DTEND" in event["behaviour"]
    assert todo["support"] == "unsupported"
    assert "DUE" in todo["behaviour"]


def test_missed_by_a_range_inside_it_is_unsupported():
    server = RewritingServer()
    search = fake_search(server)

    def not_inside(start, end, **kwargs):
        return [] if (start.hour, start.minute) == (12, 15) else search(start=start, end=end, **kwargs)

    event, _ = _run(server, search=not_inside)
    assert event["support"] == "unsupported"
    assert "inside it" in event["behaviour"]


def test_not_found_at_its_start_is_unknown():
    server = RewritingServer()
    event, todo = _run(server, search=lambda **kwargs: [])
    assert event["support"] == "unknown"
    assert todo["support"] == "unknown"
    assert not server.objects


def test_a_failing_search_is_unknown():
    server = RewritingServer()
    event, _ = _run(server, search=DAVError("500 Internal Server Error"))
    assert event["support"] == "unknown"
    assert not server.objects


@pytest.mark.parametrize("answer", [403, DAVError("500")])
def test_an_object_that_cannot_be_stored_is_unknown(answer):
    event, _ = _run(RewritingServer(put_answer=answer))
    assert event["support"] == "unknown"


def test_without_a_task_calendar_the_task_is_unknown():
    event, todo = _run(RewritingServer(), tasklist=False)
    assert event["support"] == "full"
    assert todo["support"] == "unknown"


def test_without_time_range_search_for_tasks_the_task_is_unknown():
    server = RewritingServer()
    event, todo = _run(server, todo_range="unsupported")
    assert event["support"] == "full"
    assert todo["support"] == "unknown"
    assert not server.objects


def test_without_time_range_search_for_events_the_event_is_unknown():
    event, todo = _run(RewritingServer(), event_range="unsupported")
    assert event["support"] == "unknown"
    assert todo["support"] == "full"
