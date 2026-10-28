"""Whether a PARTICIPANT component and a VTIMEZONE survive a round trip.

CCS, Bedework 3, robur, GMX and Ox (and Zimbra, reportedly) refuse an event
carrying an RFC 9073 PARTICIPANT; CCS was seen to return an object without the VTIMEZONE it was
sent with.
"""

import re
from unittest.mock import Mock

import pytest
from caldav.compatibility_hints import FeatureSet
from caldav.lib.error import AuthorizationError

from caldav_server_tester.checks import CheckParticipant, CheckVtimezone, CheckXProperty

from .test_floating_event import RewritingServer

BASE = "http://example.com/cal/"
TASKS = "http://example.com/tasks/"


def _run(check, server, todo="full"):
    checker = Mock()
    checker._features_checked = FeatureSet({"save-load.todo": {"support": todo}})
    checker.features_checked = checker._features_checked
    checker.debug_mode = None
    checker._client_obj = server
    checker.fixture_base_year = 2027
    checker.calendar.url.join.side_effect = lambda name: BASE + name
    checker.tasklist.url.join.side_effect = lambda name: TASKS + name
    check(checker)._run_check()
    return checker._features_checked.is_supported(check.FEATURE, return_type=dict)


def _drop(component):
    return lambda body: re.sub(rf"BEGIN:{component}\r\n.*?END:{component}\r\n", "", body, flags=re.DOTALL)


@pytest.mark.parametrize("check", [CheckParticipant, CheckVtimezone])
def test_kept_is_full(check):
    server = RewritingServer()
    assert _run(check, server)["support"] == "full"
    assert not server.objects


def test_the_participant_probe_sends_a_participant_inside_the_event():
    sent = []
    _run(CheckParticipant, RewritingServer(rewrite=lambda body: sent.append(body) or body))
    event = sent[0][sent[0].index("BEGIN:VEVENT") : sent[0].index("END:VEVENT")]
    assert "BEGIN:PARTICIPANT\r\n" in event
    assert "\r\nPARTICIPANT-TYPE:" in event


def test_the_vtimezone_probe_sends_a_tzid_and_its_vtimezone():
    sent = []
    _run(CheckVtimezone, RewritingServer(rewrite=lambda body: sent.append(body) or body))
    tzid = re.search(r"\r\nDTSTART;TZID=([^:]+):", sent[0])[1]
    assert f"BEGIN:VTIMEZONE\r\nTZID:{tzid}\r\n" in sent[0]


def test_participant_dropped_is_unsupported():
    assert _run(CheckParticipant, RewritingServer(rewrite=_drop("PARTICIPANT")))["support"] == "unsupported"


def test_vtimezone_dropped_is_unsupported():
    """CCS: DTSTART keeps its TZID, the VTIMEZONE defining it is gone."""
    verdict = _run(CheckVtimezone, RewritingServer(rewrite=_drop("VTIMEZONE")))
    assert verdict["support"] == "unsupported"
    assert "Europe/Berlin" in verdict["behaviour"]


def test_vtimezone_for_another_tzid_is_unsupported():
    def rename(body):
        return body.replace("BEGIN:VTIMEZONE\r\nTZID:Europe/Berlin", "BEGIN:VTIMEZONE\r\nTZID:Etc/UTC")

    assert _run(CheckVtimezone, RewritingServer(rewrite=rename))["support"] == "unsupported"


def test_vtimezone_tzid_need_not_be_its_first_property():
    def push_down(body):
        return body.replace("BEGIN:VTIMEZONE\r\n", "BEGIN:VTIMEZONE\r\nLAST-MODIFIED:20200101T000000Z\r\n")

    assert _run(CheckVtimezone, RewritingServer(rewrite=push_down))["support"] == "full"


def test_times_converted_to_utc_say_nothing_about_the_vtimezone():
    def to_utc(body):
        body = _drop("VTIMEZONE")(body)
        return re.sub(r"(DT(?:START|END));TZID=[^:]*:(\d{8}T\d{6})", r"\1:\2Z", body)

    assert _run(CheckVtimezone, RewritingServer(rewrite=to_utc))["support"] == "unknown"


@pytest.mark.parametrize("check", [CheckParticipant, CheckVtimezone])
@pytest.mark.parametrize("answer", [400, 403, 500, AuthorizationError("403 Forbidden")])
def test_refused_is_ungraceful(check, answer):
    assert _run(check, RewritingServer(put_answer=answer))["support"] == "ungraceful"


@pytest.mark.parametrize("check", [CheckParticipant, CheckVtimezone])
def test_unanswered_or_unreadable_is_unknown(check):
    assert _run(check, RewritingServer(put_answer=503))["support"] == "unknown"
    assert _run(check, RewritingServer(get_status=500))["support"] == "unknown"


def test_x_property_kept_is_full_in_an_event_and_a_task():
    sent = []
    assert _run(CheckXProperty, RewritingServer(rewrite=lambda body: sent.append(body) or body))["support"] == "full"
    assert [re.search(r"BEGIN:(VEVENT|VTODO)", body)[1] for body in sent] == ["VEVENT", "VTODO"]
    assert all(re.search(r"\r\nX-[A-Z-]+:", body) for body in sent)


def test_x_property_dropped_is_unsupported():
    verdict = _run(CheckXProperty, RewritingServer(rewrite=lambda body: re.sub(r"X-[^\r]*\r\n", "", body)))
    assert verdict == {"support": "unsupported", "behaviour": "X-CALDAV-SERVER-TESTER is dropped, in an event"}


def test_x_property_dropped_from_a_task_only_is_unsupported():
    """Ox"""

    def from_tasks(body):
        return re.sub(r"X-[^\r]*\r\n", "", body) if "BEGIN:VTODO" in body else body

    verdict = _run(CheckXProperty, RewritingServer(rewrite=from_tasks))
    assert verdict == {"support": "unsupported", "behaviour": "X-CALDAV-SERVER-TESTER is dropped, in a task"}


def test_x_property_in_events_only_where_tasks_cannot_be_stored():
    sent = []
    verdict = _run(CheckXProperty, RewritingServer(rewrite=lambda body: sent.append(body) or body), todo="unsupported")
    assert verdict == {"support": "full", "behaviour": "probed in an event only, tasks cannot be stored"}
    assert len(sent) == 1 and "BEGIN:VEVENT" in sent[0]


def test_x_property_event_and_task_go_to_their_own_calendars_and_urls():
    server = RewritingServer()
    urls = []
    put = server.put
    server.put = lambda url, body, headers=None: urls.append(url) or put(url, body, headers)
    _run(CheckXProperty, server)
    assert urls[0].startswith(BASE) and urls[1].startswith(TASKS)
    assert urls[0].rsplit("/", 1)[1] != urls[1].rsplit("/", 1)[1]


@pytest.mark.parametrize("check", [CheckParticipant, CheckVtimezone])
def test_unread_without_a_status_says_so_plainly(check):
    verdict = _run(check, RewritingServer(get_status=503))
    assert verdict["support"] == "unknown"
    assert "None" not in verdict["behaviour"]
