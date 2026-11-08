"""Whether a task with a DTSTART and no DUE keeps its DTSTART.

Zimbra drops it; with a DUE it keeps both.
"""

import re

from caldav_server_tester.checks import CheckTodoDtstartWithoutDue

from .test_floating_event import RewritingServer
from .test_icalendar_roundtrip import TASKS, _run


def _drop_dtstart(body):
    return re.sub(r"DTSTART[^\r\n]*\r\n", "", body)


def test_kept_is_full_and_the_probe_sends_a_task_without_due():
    sent = []
    server = RewritingServer(rewrite=lambda body: sent.append(body) or body)
    assert _run(CheckTodoDtstartWithoutDue, server)["support"] == "full"
    [body] = sent
    assert "BEGIN:VTODO" in body and "\r\nDTSTART:" in body
    assert "DUE" not in body
    assert not server.objects


def test_dropped_only_without_a_due_is_unsupported():
    """Zimbra: the control, the same task with its DUE, keeps both."""
    server = RewritingServer(rewrite=lambda body: body if "\r\nDUE" in body else _drop_dtstart(body))
    verdict = _run(CheckTodoDtstartWithoutDue, server)
    assert verdict == {"support": "unsupported", "behaviour": "the DTSTART is dropped; with a DUE, both are kept"}
    assert not server.objects


def test_dropped_with_a_due_too_says_so():
    server = RewritingServer(rewrite=_drop_dtstart)
    verdict = _run(CheckTodoDtstartWithoutDue, server)
    assert verdict == {"support": "unsupported", "behaviour": "the DTSTART is dropped, with a DUE too"}
    assert not server.objects


def test_refused_is_ungraceful():
    assert _run(CheckTodoDtstartWithoutDue, RewritingServer(put_answer=403))["support"] == "ungraceful"


def test_the_task_goes_to_the_task_calendar():
    server = RewritingServer()
    seen = []
    put = server.put
    server.put = lambda url, body, headers=None: seen.append(url) or put(url, body, headers)
    _run(CheckTodoDtstartWithoutDue, server)
    assert seen and all(url.startswith(TASKS) for url in seen)


def test_not_probed_where_tasks_cannot_be_stored():
    sent = []
    _run(
        CheckTodoDtstartWithoutDue, RewritingServer(rewrite=lambda body: sent.append(body) or body), todo="unsupported"
    )
    assert sent == []
