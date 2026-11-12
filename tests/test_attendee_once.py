"""Whether each ATTENDEE of an event comes back once.

Bedework 5 stores every ATTENDEE twice: one line sent, two identical lines
back.  caldav drops the exact copy when it reads the data, so that is a quirk;
copies that differ would be broken.
"""

from unittest.mock import Mock

from caldav.compatibility_hints import FeatureSet

from caldav_server_tester.checks import CheckAttendeeOnce
from tests.test_attendee_params import RewritingServer

BASE = "http://example.com/cal/"
FEATURE = "save-load.icalendar.attendee-once"


class DuplicatingServer(RewritingServer):
    """Bedework 5's way: every ATTENDEE line is stored twice."""

    def __init__(self, copy=lambda line: line, **kwargs):
        super().__init__(**kwargs)
        self.copy = copy

    def put(self, url, body, headers=None):
        lines = []
        for line in body.split("\r\n"):
            lines.append(line)
            if line.startswith("ATTENDEE"):
                lines.append(self.copy(line))
        return super().put(url, "\r\n".join(lines), headers)


def _run(server):
    checker = Mock()
    checker._features_checked = FeatureSet()
    checker.debug_mode = None
    checker._client_obj = server
    checker.fixture_base_year = 2027
    checker.calendar.url.join.side_effect = lambda name: BASE + name
    CheckAttendeeOnce(checker)._run_check()
    return checker._features_checked.is_supported(FEATURE, return_type=dict)


def test_once_is_full():
    server = RewritingServer()
    assert _run(server) == {"support": "full"}
    assert not server.objects


def test_bedework_5_is_a_quirk():
    verdict = _run(DuplicatingServer())
    assert verdict["support"] == "quirk"
    assert verdict["behaviour"] == "stored twice"


def test_copies_that_differ_are_broken():
    verdict = _run(DuplicatingServer(copy=lambda line: line.replace(":mailto", ";RSVP=TRUE:mailto")))
    assert verdict["support"] == "broken"


def test_a_dropped_attendee_cannot_tell():
    verdict = _run(RewritingServer(rewrite=lambda line: None))
    assert verdict["support"] == "unknown"


def test_a_refused_event_is_ungraceful():
    assert _run(RewritingServer(put_status=403))["support"] == "ungraceful"


def test_one_attendee_doubled_names_it():
    server = DuplicatingServer()
    server.put = _copy_only(server, "csc-bob")
    verdict = _run(server)
    assert verdict["support"] == "quirk"
    assert "csc-bob" in verdict["behaviour"] and "csc-ase" not in verdict["behaviour"]
    assert not server.objects


def test_three_copies_say_so():
    server = DuplicatingServer(copy=lambda line: line + "\r\n" + line)
    verdict = _run(server)
    assert verdict == {"support": "quirk", "behaviour": "stored 3 times"}
    assert not server.objects


def test_an_attendee_the_server_adds_is_no_obstacle():
    """Some servers add the owner; the sent attendees are what is graded."""
    server = DuplicatingServer()
    original = server.put

    def put(url, body, headers=None):
        body = body.replace("END:VEVENT", "ATTENDEE:mailto:owner@example.com\r\nEND:VEVENT")
        return original(url, body, headers)

    server.put = put
    assert _run(server)["support"] == "quirk"


def test_broken_copies_are_cleaned_up():
    server = DuplicatingServer(copy=lambda line: line.replace(":mailto", ";RSVP=TRUE:mailto"))
    _run(server)
    assert not server.objects


def _copy_only(server, who):
    """A put that stores a copy of one attendee's line only."""
    original = RewritingServer.put.__get__(server)

    def put(url, body, headers=None):
        lines = []
        for line in body.split("\r\n"):
            lines.append(line)
            if line.startswith("ATTENDEE") and who in line:
                lines.append(line)
        return original(url, "\r\n".join(lines), headers)

    return put
