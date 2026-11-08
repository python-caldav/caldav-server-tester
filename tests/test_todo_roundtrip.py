"""Whether a task keeps what it was stored with.

Ox answers a task carrying an ATTENDEE with 201 and then 404 on GET, drops
ESTIMATED-DURATION, drops COMPLETED where the task has no STATUS, and
rewrites a task's VALARM.
"""

import re
from unittest.mock import Mock

import pytest
from caldav.compatibility_hints import FeatureSet

from caldav_server_tester.checks import (
    CheckCompletedWithoutStatus,
    CheckTodoAlarm,
    CheckTodoAttendee,
    CheckTodoPriority,
    CheckTodoStatus,
    CheckTodoStatusRfc10051,
    CheckUnknownProperty,
    PrepareCalendar,
)

from .test_floating_event import RewritingServer

BASE = "http://example.com/cal/"
TASKS = "http://example.com/tasks/"
CHECKS = [
    CheckTodoAttendee,
    CheckUnknownProperty,
    CheckCompletedWithoutStatus,
    CheckTodoAlarm,
    CheckTodoStatus,
    CheckTodoStatusRfc10051,
    CheckTodoPriority,
]


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


def _run(check, server, todo="full", tasklist=True, whole=False, recorded=None):
    """``whole``: through run_check(), with its check that every declared
    feature got a value"""
    checker = Mock()
    checker._checks_run = {PrepareCalendar}
    checker._features_checked = FeatureSet({"save-load.todo": {"support": todo}, **(recorded or {})})
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


def test_an_alarm_dropped_is_unsupported():
    def drop_alarm(body):
        return re.sub(r"BEGIN:VALARM\r\n.*END:VALARM\r\n", "", body, flags=re.DOTALL)

    verdict = _run(CheckTodoAlarm, RewritingServer(rewrite=drop_alarm))
    assert verdict == {"support": "unsupported", "behaviour": "the VALARM is dropped"}


def test_an_alarm_given_a_uid_is_full():
    def add_uid(body):
        return body.replace("BEGIN:VALARM\r\n", "BEGIN:VALARM\r\nUID:csc-alarm-uid\r\n")

    assert _run(CheckTodoAlarm, RewritingServer(rewrite=add_uid))["support"] == "full"


def test_an_alarm_rewritten_is_broken():
    """Ox: a relative TRIGGER made absolute, and the DESCRIPTION its own"""

    def rewrite(body):
        body = re.sub(r"\r\nTRIGGER[;:][^\r]*", "\r\nTRIGGER;VALUE=DATE-TIME:20270120T114500Z", body)
        return re.sub(r"(BEGIN:VALARM.*\r\nDESCRIPTION:)[^\r]*", r"\1Open-XChange", body, flags=re.DOTALL)

    verdict = _run(CheckTodoAlarm, RewritingServer(rewrite=rewrite))
    assert verdict["support"] == "broken"
    assert verdict["behaviour"] == (
        "the alarm's TRIGGER -PT15M is stored as TRIGGER;VALUE=DATE-TIME:20270120T114500Z; "
        "its DESCRIPTION is stored as Open-XChange"
    )


@pytest.mark.parametrize(
    "trigger",
    ["TRIGGER:-P0DT0H15M0S", "TRIGGER;VALUE=DURATION:-PT15M", "TRIGGER;RELATED=START;VALUE=DURATION:-PT15M"],
    ids=["spelled-out", "value-default", "both-defaults"],
)
def test_an_alarm_trigger_written_another_way_is_full(trigger):
    """The same duration, or a parameter at its RFC 5545 default, changes nothing"""

    def rewrite(body):
        return re.sub(r"\r\nTRIGGER[;:][^\r]*", "\r\n" + trigger, body)

    assert _run(CheckTodoAlarm, RewritingServer(rewrite=rewrite))["support"] == "full"


def test_an_alarm_description_with_a_language_is_full():
    def rewrite(body):
        return re.sub(r"(BEGIN:VALARM.*\r\n)DESCRIPTION:", r"\1DESCRIPTION;LANGUAGE=en:", body, flags=re.DOTALL)

    assert _run(CheckTodoAlarm, RewritingServer(rewrite=rewrite))["support"] == "full"


def test_an_alarm_kept_without_its_trigger_is_broken():
    def rewrite(body):
        return re.sub(r"\r\nTRIGGER[;:][^\r]*", "", body)

    verdict = _run(CheckTodoAlarm, RewritingServer(rewrite=rewrite))
    assert verdict == {"support": "broken", "behaviour": "the alarm's TRIGGER is dropped"}


def test_an_alarm_action_rewritten_is_broken():
    def rewrite(body):
        return body.replace("ACTION:DISPLAY", "ACTION:AUDIO")

    verdict = _run(CheckTodoAlarm, RewritingServer(rewrite=rewrite))
    assert verdict == {"support": "broken", "behaviour": "the alarm's ACTION is stored as AUDIO"}


def test_an_alarm_with_the_task_description_unchanged_is_not_confused_with_it():
    """Only the VALARM's own DESCRIPTION counts"""

    def add_task_description(body):
        return body.replace("\r\nBEGIN:VALARM", "\r\nDESCRIPTION:the task's own\r\nBEGIN:VALARM")

    assert _run(CheckTodoAlarm, RewritingServer(rewrite=add_task_description))["support"] == "full"


class ValueServer(RewritingServer):
    """Stores ``name:value`` as ``stored[value]`` (None drops it), and refuses the values in ``refused``."""

    def __init__(self, name, stored=None, refused=()):
        super().__init__()
        self.name, self.stored, self.refused = name, stored or {}, refused

    def put(self, url, body, headers=None):
        value = re.search(rf"\r\n{self.name}:([^\r]*)", body)[1]
        if value in self.refused:
            return Mock(status=403, headers={})
        if value in self.stored:
            new = self.stored[value]
            body = body.replace(f"\r\n{self.name}:{value}", "" if new is None else f"\r\n{self.name}:{new}")
        return super().put(url, body, headers)


def test_the_rfc_5545_statuses_and_the_rfc_10051_ones_are_swept_apart():
    for check, expected in (
        (CheckTodoStatus, ["NEEDS-ACTION", "IN-PROCESS", "COMPLETED", "CANCELLED"]),
        (CheckTodoStatusRfc10051, ["PENDING", "FAILED"]),
    ):
        sent = []
        _run(check, RewritingServer(rewrite=lambda body: sent.append(body) or body))
        assert [re.search(r"\r\nSTATUS:([^\r]*)", b)[1] for b in sent] == expected


def test_rfc_10051_statuses_dropped_where_not_advertised():
    """Ox keeps every RFC 5545 status, drops PENDING and FAILED, and does not advertise RFC 10051"""
    server = ValueServer("STATUS", {"PENDING": None, "FAILED": None})
    assert _run(CheckTodoStatus, server)["support"] == "full"
    not_advertised = {"supported-task-modes": {"support": "unsupported"}}
    verdict = _run(CheckTodoStatusRfc10051, server, recorded=not_advertised)
    assert verdict["support"] == "unsupported"
    assert verdict["values"] == []
    assert verdict["behaviour"] == "PENDING is dropped; FAILED is dropped; the server does not advertise RFC 10051"


def test_rfc_10051_statuses_dropped_where_advertised_say_so():
    advertised = {"supported-task-modes": {"support": "full", "values": ["AUTOMATIC-COMPLETION"]}}
    verdict = _run(CheckTodoStatusRfc10051, ValueServer("STATUS", {"PENDING": None}), recorded=advertised)
    assert verdict["behaviour"] == "PENDING is dropped; although the server advertises RFC 10051"


def test_rfc_10051_statuses_dropped_where_advertising_is_unknown_say_nothing_more():
    verdict = _run(CheckTodoStatusRfc10051, ValueServer("STATUS", {"PENDING": None}))
    assert verdict["behaviour"] == "PENDING is dropped"


def test_statuses_stored_as_another_and_refused():
    """Zimbra"""
    server = ValueServer("STATUS", {"PENDING": "NEEDS-ACTION", "FAILED": "NEEDS-ACTION"}, refused=("CANCELLED",))
    verdict = _run(CheckTodoStatus, server)
    assert verdict["support"] == "ungraceful"
    assert verdict["values"] == ["NEEDS-ACTION", "IN-PROCESS", "COMPLETED"]
    assert verdict["refused"] == ["CANCELLED"]
    assert "CANCELLED is refused (403)" in verdict["behaviour"]
    assert not server.objects
    assert _run(CheckTodoStatusRfc10051, server)["map"] == {"PENDING": "NEEDS-ACTION", "FAILED": "NEEDS-ACTION"}


def test_priorities_kept_as_three_levels():
    """Ox: 1, 5 and 9, each other value stored as the nearest"""
    stored = {"0": "5", "2": "1", "3": "5", "4": "5", "6": "5", "7": "9", "8": "9"}
    verdict = _run(CheckTodoPriority, ValueServer("PRIORITY", stored))
    assert verdict["support"] == "unsupported"
    assert verdict["values"] == [1, 5, 9]
    assert verdict["map"] == {0: 5, 2: 1, 3: 5, 4: 5, 6: 5, 7: 9, 8: 9}


def test_priority_zero_dropped_means_the_same():
    """No PRIORITY is no priority given, as 0 is"""
    verdict = _run(CheckTodoPriority, ValueServer("PRIORITY", {"0": None}))
    assert verdict["support"] == "full"
    assert "dropped" in verdict["behaviour"]


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


def test_a_priority_written_back_as_no_number_is_mapped_as_text():
    verdict = _run(CheckTodoPriority, ValueServer("PRIORITY", {"3": "MEDIUM"}))
    assert verdict["map"] == {3: "MEDIUM"}


def test_one_value_unreadable_makes_the_sweep_unknown():
    class OneUnreadable(ValueServer):
        def request(self, url, method="GET"):
            if "in-process" in url:
                return Mock(status=500, headers={}, raw="")
            return super().request(url, method)

    assert _run(CheckTodoStatus, OneUnreadable("STATUS"))["support"] == "unknown"
