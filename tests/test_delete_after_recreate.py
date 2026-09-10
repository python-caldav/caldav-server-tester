"""Unit tests for how a failing calendar DELETE is graded.

Measured against the Cyrus docker test server on 2026-09-10, over 20 runs of
each shape:

* a calendar created under a *fresh* cal_id deletes cleanly, 20/20;
* a calendar created under an id that was deleted moments earlier answers 500
  to DELETE, 17/20 - and a retry one second later always succeeds;
* with one second between the delete and the re-creation, 20/20 clean again;
* the window is per-name (a different fresh id deletes fine right after another
  id was deleted) and it does not affect reading or writing, only DELETE.

So MKCALENDAR is not asynchronous there, and the calendar is not slow to
delete: the *previous* delete is still settling server-side, and re-using the
name inside that window produces a calendar the server refuses to delete.  The
probe used to hit this by accident - it wipes its fixed cal_id before creating
it, so the verdict came out ``quirk`` when a previous run had left a calendar
behind and ``full`` when it had not.  Two consecutive runs of the same command
graded the same server differently.

Hence the two things tested here: the re-created case is probed deliberately,
and a DELETE whose outcome is not deterministic is ``fragile`` rather than
``quirk`` (which goes through on the one request that was sent, just slowly).
``fragile`` claims nothing beyond that non-determinism - retrying a few times
is what a client can do about it, not a diagnosis of it.
"""

from unittest.mock import Mock

import pytest
from caldav.compatibility_hints import FeatureSet
from caldav.lib.error import DeleteError, NotFoundError

import caldav_server_tester.checks as checks_mod
from caldav_server_tester.checker import ServerQuirkChecker
from caldav_server_tester.checks import CheckGetCurrentUserPrincipal, CheckMakeDeleteCalendar

CAL_ID = CheckMakeDeleteCalendar.MKDEL_CAL_ID


class FakeServer:
    """A calendar store that can be given the Cyrus re-creation window.

    ``hot_window`` makes a DELETE fail for a calendar that was created on an id
    deleted since (Cyrus, 500).  ``errors_but_deletes`` makes every DELETE
    report an error while carrying the deletion out anyway - nothing observed
    does that, but it is the case the grading has to tell apart from a delete
    that really did nothing.
    """

    def __init__(self, hot_window=False, errors_but_deletes=False, slow_delete=0):
        self.hot_window = hot_window
        self.errors_but_deletes = errors_but_deletes
        ## Ticks a deleted calendar keeps answering for - a clean DELETE that
        ## takes a moment to settle, which grades quirk rather than fragile.
        self.slow_delete = slow_delete
        self.existing = set()
        self.deleted = set()
        self.hot = set()
        self.pending = {}
        self.delete_attempts = []

    def calendar(self, cal_id=None, name=None):
        cal = Mock()
        cal.cal_id = cal_id

        def events(*args, **kwargs):
            if cal_id in self.existing:
                return []
            if self.pending.get(cal_id, 0) > 0:
                self.pending[cal_id] -= 1
                return []
            raise NotFoundError(f"no such calendar {cal_id}")

        cal.events.side_effect = events
        return cal

    def make_calendar(self, cal_id=None, **kwargs):
        self.existing.add(cal_id)
        if cal_id in self.deleted:
            self.hot.add(cal_id)
        return self.calendar(cal_id=cal_id)

    def delete(self, cal):
        """Stand-in for ``DAVObject.delete``."""
        cal_id = cal.cal_id
        self.delete_attempts.append(cal_id)
        if self.errors_but_deletes:
            self._remove(cal_id)
            raise DeleteError("500 Internal Server Error")
        if self.hot_window and cal_id in self.hot:
            ## One failure per re-creation, like the server's ~1s window: the
            ## retry a second later goes through.
            self.hot.discard(cal_id)
            raise DeleteError("500 Internal Server Error")
        if cal_id not in self.existing:
            raise NotFoundError(f"no such calendar {cal_id}")
        self._remove(cal_id)

    def _remove(self, cal_id):
        self.existing.discard(cal_id)
        self.deleted.add(cal_id)
        if self.slow_delete:
            self.pending[cal_id] = self.slow_delete


@pytest.fixture
def server(monkeypatch):
    fake = FakeServer()
    monkeypatch.setattr(checks_mod.time, "sleep", lambda _s: None)
    monkeypatch.setattr(checks_mod.DAVObject, "delete", fake.delete)
    return fake


def _check(server):
    client = Mock()
    client.features = FeatureSet()
    checker = ServerQuirkChecker(client, debug_mode=None)
    checker.principal = Mock()
    checker.principal.calendar.side_effect = server.calendar
    checker.principal.make_calendar.side_effect = server.make_calendar
    checker._checks_run.add(CheckGetCurrentUserPrincipal)
    ## _probe_make_delete settles this before the delete branch reads it.
    checker._features_checked.set_feature("create-calendar.auto", False)
    check = CheckMakeDeleteCalendar(checker)
    check.expected_features = client.features
    return check, checker


def _delete_verdict(checker):
    return checker.features_checked.is_supported("delete-calendar", dict)


def test_a_delete_that_must_be_re_issued_is_fragile(server) -> None:
    """An unreliable delete is 'fragile', not 'quirk'.

    'quirk' promises the client that the one DELETE it sent is enough and only
    the wait varies.  Here it was not, which is all 'fragile' claims - and it is
    the level Calendar.delete() reads to switch on its retry loop.
    """
    server.hot_window = True
    check, checker = _check(server)
    ## Put the id in the "recently deleted" state the window needs.
    server.make_calendar(cal_id=CAL_ID)
    server.delete(server.calendar(cal_id=CAL_ID))

    check._try_make_calendar(cal_id=CAL_ID)

    observed = _delete_verdict(checker)
    assert observed["support"] == "fragile"
    assert observed["delay"] == 1


def test_a_delete_that_errors_but_took_effect_is_ungraceful(server) -> None:
    """The 500 is not evidence the calendar is still there - look.

    Re-issuing the DELETE against a calendar the server did delete just gets a
    404 for as long as the probe is willing to wait, and the old code graded
    that server 'unsupported' when deletion in fact works.
    """
    server.errors_but_deletes = True
    check, checker = _check(server)

    check._try_make_calendar(cal_id=CAL_ID)

    observed = _delete_verdict(checker)
    assert observed["support"] == "ungraceful"
    assert "deleted" in observed["behaviour"]
    ## One DELETE, and no retry storm: the calendar was already gone.
    assert server.delete_attempts.count(CAL_ID) == 1


def test_the_recreated_case_is_probed_even_when_the_clean_one_passes(server) -> None:
    """The verdict must not depend on whether a previous run left a calendar."""
    server.hot_window = True
    check, checker = _check(server)

    check._probe_make_delete()

    observed = _delete_verdict(checker)
    assert observed["support"] == "fragile"
    assert "re-created" in observed["behaviour"]


def test_a_clean_server_stays_full_through_the_recreate_probe(server) -> None:
    check, checker = _check(server)

    check._probe_make_delete()

    assert checker.features_checked.is_supported("delete-calendar", str) == "full"
    assert checker.features_checked.is_supported("delete-calendar.free-namespace", str) == "full"
    ## Nothing left behind on the server.
    assert server.existing == set()


def test_free_namespace_is_still_probed_when_the_delete_is_fragile(server) -> None:
    """A fragile delete deletes - with a retry - so the question is answerable.

    is_supported() is False for 'fragile', which used to make the probe
    self-skip; that was the argument for grading the retry case 'quirk'.  The
    gate accepts fragile instead.
    """
    server.hot_window = True
    check, checker = _check(server)

    check._probe_make_delete()

    assert checker.features_checked.is_supported("delete-calendar", str) == "fragile"
    assert checker.features_checked.is_supported("delete-calendar.free-namespace", str) == "full"


def test_probe_calendars_are_not_leaked_on_a_server_with_the_window(server) -> None:
    """The discard after the namespace probe has to survive the 500 too.

    Leaked probe calendars are what made the next run's verdict differ from
    this one's.
    """
    server.hot_window = True
    check, _checker = _check(server)

    check._probe_make_delete()

    assert server.existing == set()


class TestTheRecreatedVerdictOnlyEverDowngrades:
    """The re-created case reports bad news, never good news.

    It runs after the ordinary probe has already recorded a verdict, so writing
    whatever it happens to see would let the *easier* case overwrite the harder
    one - and lose the `delay` measured with it.  The docstring and the
    CHANGELOG both promise it only downgrades; these hold them to it.
    """

    def test_a_fragile_verdict_is_not_upgraded(self, server) -> None:
        """The re-created case comes out `quirk` here, and must not be written.

        The point is a re-created delete that is *milder* than what the
        ordinary probe found: with the window off, a re-created DELETE only
        takes a moment to settle, which grades quirk.  Recording that would
        both soften the verdict and hand it the wrong `delay`.
        """
        server.slow_delete = 1
        check, checker = _check(server)
        checker._features_checked.set_feature(
            "delete-calendar",
            {"support": "fragile", "behaviour": "the ordinary probe saw this", "delay": 3},
        )
        cal = server.make_calendar(cal_id=CAL_ID)

        check._probe_delete_after_recreate(cal, CAL_ID)

        observed = _delete_verdict(checker)
        assert observed["support"] == "fragile", observed
        assert observed["delay"] == 3, observed

    def test_a_re_created_delete_that_never_works_is_not_unsupported(self, server) -> None:
        """Deletion demonstrably works here - for a fresh id, every time."""

        def never(cal):
            server.delete_attempts.append(cal.cal_id)
            raise DeleteError("500 Internal Server Error")

        check, checker = _check(server)
        checker._features_checked.set_feature("delete-calendar", True)
        cal = server.make_calendar(cal_id=CAL_ID)
        server.delete = never
        import caldav_server_tester.checks as _checks

        _checks.DAVObject.delete = staticmethod(never)

        check._probe_delete_after_recreate(cal, CAL_ID)

        assert checker.features_checked.is_supported("delete-calendar", str) == "fragile"


def test_a_calendar_that_is_not_visible_yet_is_not_called_deleted(server, monkeypatch) -> None:
    """An asynchronous create is not evidence that a DELETE took effect.

    The re-created calendar was handed to the probe straight out of
    make_calendar(), with no poll - so on a server that takes a moment to
    publish a new collection, "not queryable" read as "deleted", and a wrong
    `ungraceful` verdict overwrote a correct one.
    """
    invisible = {"ticks": 2}
    real_calendar = server.calendar

    def calendar(cal_id=None, name=None):
        cal = real_calendar(cal_id=cal_id, name=name)
        inner = cal.events.side_effect

        def events(*args, **kwargs):
            if invisible["ticks"] > 0:
                invisible["ticks"] -= 1
                raise NotFoundError("not published yet")
            return inner(*args, **kwargs)

        cal.events.side_effect = events
        return cal

    server.hot_window = True
    check, checker = _check(server)
    checker.principal.calendar.side_effect = calendar
    checker.principal.make_calendar.side_effect = lambda cal_id=None, **kw: calendar(cal_id=cal_id)
    server.existing.add(CAL_ID)
    server.deleted.add(CAL_ID)
    server.hot.add(CAL_ID)
    cal = calendar(cal_id=CAL_ID)

    check._probe_delete_after_recreate(cal, CAL_ID)

    observed = checker.features_checked.is_supported("delete-calendar", dict)
    assert observed.get("support") != "ungraceful", observed


def test_an_auto_creating_server_cannot_be_graded_unsupported(server, monkeypatch) -> None:
    """Where every lookup answers, "still there" is not an observation.

    `create-calendar.auto` means a calendar() probe always succeeds, so the
    "was it deleted after all?" question has no answer on such a server - and
    the honest verdict for a DELETE that keeps raising there is `unknown`, not
    `unsupported`.
    """

    def always_there(cal_id=None, name=None):
        cal = Mock()
        cal.cal_id = cal_id
        cal.events.return_value = []
        return cal

    def never(cal):
        raise DeleteError("500 Internal Server Error")

    monkeypatch.setattr(checks_mod.DAVObject, "delete", never)
    check, checker = _check(server)
    checker._features_checked.set_feature("create-calendar.auto", True)
    checker.principal.calendar.side_effect = always_there
    checker.principal.make_calendar.side_effect = always_there

    verdict = check._probe_delete(always_there(cal_id=CAL_ID), CAL_ID)

    assert isinstance(verdict, dict), verdict
    assert verdict["support"] == "unknown"
