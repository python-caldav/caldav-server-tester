"""Whether a task keeps what it was stored with.

Ox answers a task carrying an ATTENDEE with 201 and then 404 on GET, drops
ESTIMATED-DURATION, and drops COMPLETED where the task has no STATUS.
"""

import re
from unittest.mock import Mock

import pytest
from caldav.compatibility_hints import FeatureSet

from caldav_server_tester.checks import (
    CheckCompletedWithoutStatus,
    CheckTodoAttendee,
    CheckUnknownProperty,
    PrepareCalendar,
)

from .test_floating_event import RewritingServer

BASE = "http://example.com/cal/"
TASKS = "http://example.com/tasks/"
CHECKS = [CheckTodoAttendee, CheckUnknownProperty, CheckCompletedWithoutStatus]


class VanishingServer(RewritingServer):
    """Accepts the PUT, but stores nothing: with ``everything``, no object at
    all is readable at the URL it was put to; otherwise only a task with an
    ATTENDEE vanishes (Ox)."""

    def __init__(self, everything=False, **kwargs):
        super().__init__(**kwargs)
        self.everything = everything

    def put(self, url, body, headers=None):
        if self.everything or "\r\nATTENDEE" in body:
            return Mock(status=201, headers={})
        return super().put(url, body, headers)


def _run(check, server, todo="full", tasklist=True, whole=False):
    """``whole``: through run_check(), with its check that every declared
    feature got a value"""
    checker = Mock()
    checker._checks_run = {PrepareCalendar}
    checker._features_checked = FeatureSet({"save-load.todo": {"support": todo}})
    checker.features_checked = checker._features_checked
    checker.debug_mode = None
    checker._client_obj = server
    server.features = FeatureSet()
    checker.fixture_base_year = 2027
    checker.calendar.url.join.side_effect = lambda name: BASE + name
    if tasklist:
        checker.tasklist.url.join.side_effect = lambda name: TASKS + name
    else:
        checker.tasklist = None
    if whole:
        check(checker).run_check()
    else:
        check(checker)._run_check()
    return checker._features_checked.is_supported(check.FEATURE, return_type=dict, return_defaults=False)


def _drop(name):
    return lambda body: re.sub(rf"\r\n{name}[;:][^\r]*", "", body)


@pytest.mark.parametrize("check", CHECKS)
def test_kept_is_full_and_stored_as_a_task_on_the_task_list(check):
    sent = []
    server = RewritingServer(rewrite=lambda body: sent.append(body) or body)
    assert _run(check, server)["support"] == "full"
    assert list(server.objects) == []
    assert "BEGIN:VTODO\r\n" in sent[0] and "VEVENT" not in sent[0]


@pytest.mark.parametrize("check", CHECKS)
def test_a_task_lands_in_the_task_calendar(check):
    server = RewritingServer()
    urls = []
    put = server.put
    server.put = lambda url, body, headers=None: urls.append(url) or put(url, body, headers)
    _run(check, server)
    assert urls[0].startswith(TASKS)


def test_a_task_with_an_attendee_that_vanishes_is_broken():
    """Ox: 201, then 404, while a task without the ATTENDEE reads back"""
    server = VanishingServer()
    verdict = _run(CheckTodoAttendee, server)
    assert verdict["support"] == "broken"
    assert "404" in verdict["behaviour"]
    assert not server.objects


def test_a_404_for_every_object_says_nothing_about_the_attendee():
    """Objects not reachable at the URL they were put to (Zimbra's cal_id alias)"""
    assert _run(CheckTodoAttendee, VanishingServer(everything=True))["support"] == "unknown"


def test_the_attendee_dropped_is_unsupported():
    assert _run(CheckTodoAttendee, RewritingServer(rewrite=_drop("ATTENDEE")))["support"] == "unsupported"


def test_estimated_duration_dropped_is_unsupported():
    verdict = _run(CheckUnknownProperty, RewritingServer(rewrite=_drop("ESTIMATED-DURATION")))
    assert verdict["support"] == "unsupported"
    assert "ESTIMATED-DURATION" in verdict["behaviour"]


def test_completed_without_status_is_sent_without_status():
    sent = []
    _run(CheckCompletedWithoutStatus, RewritingServer(rewrite=lambda body: sent.append(body) or body))
    assert "\r\nCOMPLETED:" in sent[0]
    assert "\r\nSTATUS:" not in sent[0]


def test_completed_dropped_is_unsupported():
    assert _run(CheckCompletedWithoutStatus, RewritingServer(rewrite=_drop("COMPLETED")))["support"] == "unsupported"


def test_a_status_added_with_completed_kept_is_full():
    def add_status(body):
        return body.replace("\r\nCOMPLETED:", "\r\nSTATUS:COMPLETED\r\nCOMPLETED:")

    assert _run(CheckCompletedWithoutStatus, RewritingServer(rewrite=add_status))["support"] == "full"


@pytest.mark.parametrize("check", CHECKS)
def test_refused_is_ungraceful(check):
    assert _run(check, RewritingServer(put_answer=403))["support"] == "ungraceful"


@pytest.mark.parametrize("check", CHECKS)
@pytest.mark.parametrize("todo", ["unsupported", "ungraceful"])
def test_not_probed_where_tasks_cannot_be_stored(check, todo):
    """A save-load.todo child is left unset and inherits its status; any
    other feature is unknown"""
    server = RewritingServer(put_answer=AssertionError("nothing is to be stored"))
    verdict = _run(check, server, todo=todo, whole=True)
    if check.FEATURE.startswith("save-load.todo."):
        assert verdict["support"] == todo
    else:
        assert verdict == {"support": "unknown", "behaviour": "cannot test, tasks cannot be stored"}


@pytest.mark.parametrize("check", CHECKS)
def test_no_task_calendar_is_unknown(check):
    assert _run(check, RewritingServer(), tasklist=False)["support"] == "unknown"


def test_an_unreadable_task_other_than_404_is_unknown():
    assert _run(CheckTodoAttendee, RewritingServer(get_status=500))["support"] == "unknown"
