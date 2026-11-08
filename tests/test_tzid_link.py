"""Whether a TZID that is a tz database link name is stored.

Bedework 5 refuses Europe/Oslo (a link to Europe/Berlin since tzdata 2022b)
with 403 unknownTimezone, and stores Europe/Berlin.
"""

import re
from unittest.mock import Mock

import pytest
from caldav.lib.error import AuthorizationError

from caldav_server_tester.checks import CheckTzidLink

from .test_floating_event import RewritingServer
from .test_icalendar_roundtrip import _run

FEATURE = CheckTzidLink.FEATURE


class RefusingLinks(RewritingServer):
    """Refuses a body naming ``refused``: Bedework 5, where it is a link name."""

    def __init__(self, refused="Europe/Oslo", answer=AuthorizationError("403 Forbidden"), **kwargs):
        super().__init__(**kwargs)
        self.refused = refused
        self.answer = answer
        self.sent = []

    def put(self, url, body, headers=None):
        self.sent.append(body)
        if any(f"TZID={tzid}" in body or f"TZID:{tzid}" in body for tzid in self.refused.split()):
            if isinstance(self.answer, Exception):
                raise self.answer
            return Mock(status=self.answer, headers={})
        return super().put(url, body, headers)


def test_kept_is_full_and_the_probe_sends_a_link_with_its_vtimezone():
    server = RefusingLinks(refused="nothing")
    assert _run(CheckTzidLink, server)["support"] == "full"
    [body] = server.sent
    assert re.search(r"\r\nDTSTART;TZID=Europe/Oslo:", body)
    assert "BEGIN:VTIMEZONE\r\nTZID:Europe/Oslo\r\n" in body
    assert not server.objects


@pytest.mark.parametrize("answer", [AuthorizationError("403 Forbidden"), 403, 400])
def test_refused_while_the_zone_it_points_to_is_stored_is_ungraceful(answer):
    server = RefusingLinks(answer=answer)
    verdict = _run(CheckTzidLink, server)
    assert verdict["support"] == "ungraceful"
    assert "Europe/Berlin" in server.sent[-1], "the control is the zone the link points to"
    assert not server.objects


def test_refused_with_the_zone_it_points_to_too_says_nothing():
    verdict = _run(CheckTzidLink, RefusingLinks(refused="Europe/Oslo Europe/Berlin"))
    assert verdict["support"] == "unknown"


def test_refused_with_no_answer_for_the_zone_it_points_to_is_unknown():
    server = RefusingLinks(refused="Europe/Oslo")
    put = server.put
    server.put = lambda url, body, headers=None: (
        Mock(status=503, headers={}) if "TZID=Europe/Berlin" in body else put(url, body, headers)
    )
    verdict = _run(CheckTzidLink, server)
    assert verdict["support"] == "unknown"
    assert "no answer" in verdict["behaviour"]
    assert not server.objects


def test_stored_as_the_zone_it_points_to_is_a_quirk():
    verdict = _run(CheckTzidLink, RewritingServer(rewrite=lambda body: body.replace("Europe/Oslo", "Europe/Berlin")))
    assert verdict["support"] == "quirk"
    assert "Europe/Berlin" in verdict["behaviour"]


def test_unanswered_or_unreadable_is_unknown():
    assert _run(CheckTzidLink, RewritingServer(put_answer=503))["support"] == "unknown"
    assert _run(CheckTzidLink, RewritingServer(get_status=500))["support"] == "unknown"


def test_stored_under_another_name_is_unsupported():
    server = RewritingServer(rewrite=lambda body: body.replace("Europe/Oslo", "Europe/Paris"))
    verdict = _run(CheckTzidLink, server)
    assert verdict["support"] == "unsupported"
    assert "Europe/Paris" in verdict["behaviour"]
    assert not server.objects


def test_every_tzid_renamed_says_nothing():
    """A server renaming every TZID says nothing about link names."""
    server = RewritingServer(rewrite=lambda body: re.sub(r"TZID([=:])Europe/", r"TZID\1/vendor/Europe/", body))
    verdict = _run(CheckTzidLink, server)
    assert verdict["support"] == "unknown"
    assert "/vendor/Europe/Berlin" in verdict["behaviour"]
    assert not server.objects
