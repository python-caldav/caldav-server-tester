"""A server that wants MKCOL and cannot delete must still be described right.

The profile the tester hands the CalDAV library is what makes the library pick
MKCOL over MKCALENDAR, and it picks it for exactly one shape:
`quirk` + `behaviour: "mkcol-required"` (caldav/collection.py).  Any later
`set_feature("create-calendar", True)` merges `support: full` over that shape
and leaves the behaviour text stranded there — a profile that reads
"mkcol-required" while telling the library to use MKCALENDAR.

And a server that has just been measured as unable to delete a calendar is the
last one to create another calendar on.
"""

from unittest.mock import Mock

import pytest
from caldav.compatibility_hints import FeatureSet
from caldav.lib.error import DeleteError, MkcalendarError, NotFoundError

import caldav_server_tester.checks as checks_mod
from caldav_server_tester.checker import ServerQuirkChecker
from caldav_server_tester.checks import CheckGetCurrentUserPrincipal, CheckMakeDeleteCalendar


class MkcolOnlyServer:
    """Answers 405 to MKCALENDAR, accepts MKCOL, and never deletes anything."""

    def __init__(self):
        self.existing = set()
        self.created = []

    def calendar(self, cal_id=None, name=None):
        cal = Mock()
        cal.cal_id = cal_id

        def events(*args, **kwargs):
            if cal_id not in self.existing:
                raise NotFoundError("no such calendar")
            return []

        cal.events.side_effect = events
        return cal

    def make_calendar(self, cal_id=None, method=None, **kwargs):
        if method != "mkcol":
            raise MkcalendarError("405 Method Not Allowed\n\n<html>use MKCOL</html>")
        self.existing.add(cal_id)
        self.created.append(cal_id)
        return self.calendar(cal_id=cal_id)

    def delete(self, cal):
        raise DeleteError("403 Forbidden\n\n<html>deleting calendars is not allowed here</html>")


@pytest.fixture
def server(monkeypatch):
    fake = MkcolOnlyServer()
    monkeypatch.setattr(checks_mod.time, "sleep", lambda _s: None)
    monkeypatch.setattr(checks_mod.DAVObject, "delete", fake.delete)
    return fake


@pytest.fixture
def check(server):
    client = Mock()
    client.features = FeatureSet()
    checker = ServerQuirkChecker(client, debug_mode=None)
    checker.principal = Mock()
    checker.principal.calendar.side_effect = server.calendar
    checker.principal.make_calendar.side_effect = server.make_calendar
    checker.principal.calendars.return_value = []
    checker._checks_run.add(CheckGetCurrentUserPrincipal)
    probe = CheckMakeDeleteCalendar(checker)
    probe.expected_features = client.features
    return probe


def test_the_mkcol_required_verdict_survives(check) -> None:
    check._probe_make_delete()

    observed = check.checker.features_checked.is_supported("create-calendar", dict)
    assert observed["support"] == "quirk", observed
    assert observed["behaviour"] == "mkcol-required"


def test_no_calendar_is_created_on_a_server_that_cannot_delete(check, server) -> None:
    """The gate that stopped this was removed as a duplicate.  It was not one."""
    check._probe_make_delete()

    assert check.checker.features_checked.is_supported("delete-calendar", str) == "unsupported"
    ## One calendar for the create probe itself; nothing beyond it, since the
    ## server just proved it will not delete what we make.
    assert len(server.created) == 1, server.created
