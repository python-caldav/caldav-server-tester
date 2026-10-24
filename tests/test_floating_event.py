"""Whether an event with a floating time is stored floating.

SOGo adds a VTIMEZONE for Etc/UTC and gives DTSTART and DTEND TZID=Etc/UTC.
The probe reads the server's own text: the icalendar library writes such a
time back with a 'Z' after the TZID, which RFC 5545 section 3.3.5 forbids.
"""

import re
from unittest.mock import Mock

import pytest
from caldav.compatibility_hints import FeatureSet
from caldav.lib.error import AuthorizationError

from caldav_server_tester.checks import CheckFloatingEvent

BASE = "http://example.com/cal/"
FEATURE = "save-load.event.floating"
VTIMEZONE = (
    "BEGIN:VTIMEZONE\r\nTZID:Etc/UTC\r\nBEGIN:STANDARD\r\nTZOFFSETFROM:+0000\r\n"
    "TZOFFSETTO:+0000\r\nDTSTART:19700101T000000\r\nEND:STANDARD\r\nEND:VTIMEZONE\r\n"
)


class RewritingServer:
    """Stores what ``rewrite`` makes of the body; ``put_answer`` overrides the
    answer to the PUT (raised where it is an exception)."""

    def __init__(self, rewrite=lambda body: body, put_answer=None, get_status=200):
        self.rewrite = rewrite
        self.put_answer = put_answer
        self.get_status = get_status
        self.objects = {}

    def put(self, url, body, headers=None):
        if isinstance(self.put_answer, Exception):
            raise self.put_answer
        if self.put_answer is not None:
            return Mock(status=self.put_answer, headers={})
        self.objects[url] = self.rewrite(body)
        return Mock(status=201, headers={})

    def request(self, url, method="GET"):
        if url not in self.objects:
            return Mock(status=404, headers={}, raw="")
        return Mock(status=self.get_status, headers={}, raw=self.objects[url])

    def delete(self, url, headers=None):
        self.objects.pop(url, None)
        return Mock(status=204)


def _pin_to_utc(body, z=""):
    body = re.sub(r"(DTSTART|DTEND):(\d{8}T\d{6})\r\n", rf"\1;TZID=Etc/UTC:\2{z}\r\n", body)
    return body.replace("BEGIN:VEVENT", VTIMEZONE + "BEGIN:VEVENT")


def _run(server):
    checker = Mock()
    checker._features_checked = FeatureSet()
    checker.debug_mode = None
    checker._client_obj = server
    checker.fixture_base_year = 2027
    checker.calendar.url.join.side_effect = lambda name: BASE + name
    CheckFloatingEvent(checker)._run_check()
    return checker._features_checked.is_supported(FEATURE, return_type=dict)


def test_stored_floating_is_full():
    server = RewritingServer()
    assert _run(server)["support"] == "full"
    assert not server.objects


def test_the_probe_sends_floating_times():
    sent = []
    _run(RewritingServer(rewrite=lambda body: sent.append(body) or body))
    assert re.search(r"\r\nDTSTART:\d{8}T\d{6}\r\n", sent[0])
    assert re.search(r"\r\nDTEND:\d{8}T\d{6}\r\n", sent[0])


def test_pinned_to_a_time_zone_is_unsupported():
    """SOGo; the VTIMEZONE's own DTSTART is not mistaken for the event's."""
    verdict = _run(RewritingServer(rewrite=_pin_to_utc))
    assert verdict["support"] == "unsupported"
    assert verdict["behaviour"] == "a floating time is stored with TZID=Etc/UTC"


def test_tzid_and_z_both_is_broken():
    verdict = _run(RewritingServer(rewrite=lambda body: _pin_to_utc(body, "Z")))
    assert verdict["support"] == "broken"
    assert "5545" in verdict["behaviour"]


def test_converted_to_utc_is_unsupported():
    verdict = _run(RewritingServer(rewrite=lambda body: re.sub(r"(DT(?:START|END):\d{8}T\d{6})\r\n", r"\1Z\r\n", body)))
    assert verdict["support"] == "unsupported"
    assert verdict["behaviour"] == "a floating time is stored as UTC"


def test_a_folded_line_is_read_whole():
    def fold(body):
        return re.sub(r"DTSTART:(\d{8})", r"DTSTART;TZID=Etc/UTC:\r\n \1", body)

    assert _run(RewritingServer(rewrite=fold))["support"] == "unsupported"


@pytest.mark.parametrize("answer", [400, 403, AuthorizationError("403 Forbidden")])
def test_refused_is_ungraceful(answer):
    assert _run(RewritingServer(put_answer=answer))["support"] == "ungraceful"


def test_unreadable_is_unknown():
    assert _run(RewritingServer(get_status=500))["support"] == "unknown"
