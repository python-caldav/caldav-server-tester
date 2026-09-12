"""A successful creation answered by a 207 that says nothing at all.

RFC 4918 section 13 requires every ``DAV:response`` to carry either a
``DAV:status`` or at least one ``DAV:propstat``.  Bedework 5 answers a
property-less MKCALENDAR with a 207 whose single response holds nothing but
the href of the collection it created.  The CalDAV library reads that as the
success it is (there is nothing in the body saying otherwise), so the calendar
turns up and the probe would report plain "full" - the violation is only
visible in the raw response, which ``make_calendar()`` does not hand back.
"""

from unittest.mock import Mock

import pytest
from caldav.compatibility_hints import FeatureSet
from caldav.response import DAVResponse

import caldav_server_tester.checks as checks_mod
from caldav_server_tester.checker import ServerQuirkChecker
from caldav_server_tester.checks import CheckGetCurrentUserPrincipal, CheckMakeDeleteCalendar, is_statusless_multistatus

STATUSLESS = b'<multistatus xmlns="DAV:"><response><href>/dav/user/cal/</href></response></multistatus>'
ALL_OK = (
    b'<multistatus xmlns="DAV:"><response><href>/dav/user/cal/</href>'
    b"<propstat><prop><displayname/></prop><status>HTTP/1.1 200 ok</status></propstat>"
    b"</response></multistatus>"
)


class CreatingServer:
    """Creates and deletes calendars, answering MKCALENDAR with `answer`."""

    def __init__(self, client, answer, status=207):
        self.client = client
        self.answer = answer
        self.status = status
        self.existing = set()

    def calendar(self, cal_id=None, name=None):
        cal = Mock()
        cal.cal_id = cal_id
        cal.events.side_effect = lambda *a, **kw: (
            [] if cal_id in self.existing else _raise(checks_mod.NotFoundError("no such calendar"))
        )
        return cal

    def make_calendar(self, cal_id=None, method=None, **kwargs):
        ## What the library does: issue the request, look at the answer, hand
        ## back the Calendar object and forget the response.
        self.client.request(f"/dav/user/{cal_id}/", "MKCALENDAR", "<mkcalendar/>")
        self.existing.add(cal_id)
        return self.calendar(cal_id=cal_id)

    def request(self, url, method="GET", *args, **kwargs):
        if method == "MKCALENDAR":
            return DAVResponse.from_bytes(self.answer, status_code=self.status)
        return DAVResponse.from_bytes(b"", status_code=200)

    def delete(self, cal):
        self.existing.discard(cal.cal_id)


def _raise(exc):
    raise exc


def make_check(monkeypatch, answer, status=207):
    monkeypatch.setattr(checks_mod.time, "sleep", lambda _s: None)
    client = Mock()
    client.features = FeatureSet()
    server = CreatingServer(client, answer, status)
    client.request.side_effect = server.request
    monkeypatch.setattr(checks_mod.DAVObject, "delete", server.delete)
    checker = ServerQuirkChecker(client, debug_mode=None)
    checker.principal = Mock()
    checker.principal.calendar.side_effect = server.calendar
    checker.principal.make_calendar.side_effect = server.make_calendar
    checker.principal.calendars.return_value = []
    checker._checks_run.add(CheckGetCurrentUserPrincipal)
    probe = CheckMakeDeleteCalendar(checker)
    probe.expected_features = client.features
    return probe


def test_the_statusless_207_is_recorded_as_a_quirk(monkeypatch) -> None:
    check = make_check(monkeypatch, STATUSLESS)

    check._probe_make_delete()

    observed = check.checker.features_checked.is_supported("create-calendar", dict)
    assert observed["support"] == "quirk", observed
    assert observed["behaviour"] == "empty-207"


def test_a_207_spelling_out_its_success_is_not_the_quirk(monkeypatch) -> None:
    """Bedework answers this shape whenever the request carries properties.
    It violates nothing - RFC 4791 asks for 201, but the body says what it
    did."""
    check = make_check(monkeypatch, ALL_OK)

    check._probe_make_delete()

    assert check.checker.features_checked.is_supported("create-calendar", str) == "full"


def test_an_ordinary_201_is_full(monkeypatch) -> None:
    check = make_check(monkeypatch, b"", status=201)

    check._probe_make_delete()

    assert check.checker.features_checked.is_supported("create-calendar", str) == "full"


@pytest.mark.parametrize(
    "body,status,expected",
    [
        (STATUSLESS, 207, True),
        (ALL_OK, 207, False),
        (b"", 201, False),
        ## A multistatus reporting a failure is not this quirk; the library
        ## raises on it, and the probe never gets this far.
        (
            b'<multistatus xmlns="DAV:"><response><href>/dav/user/cal/</href>'
            b"<status>HTTP/1.1 403 Forbidden</status></response></multistatus>",
            207,
            False,
        ),
        ## Several responses, one of them status-less: not the Bedework shape,
        ## and a body that does report something about the collection.
        (
            b'<multistatus xmlns="DAV:"><response><href>/a/</href></response>'
            b"<response><href>/b/</href><status>HTTP/1.1 200 ok</status></response></multistatus>",
            207,
            False,
        ),
    ],
)
def test_is_statusless_multistatus(body, status, expected) -> None:
    assert is_statusless_multistatus(DAVResponse.from_bytes(body, status_code=status)) is expected
