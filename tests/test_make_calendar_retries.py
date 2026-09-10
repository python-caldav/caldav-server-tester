"""A calendar creation that may be flaky is asked for more than once.

A single MKCALENDAR cannot tell "this server cannot create calendars" from
"that one did not take" - so the probe asks a few times, the way a client has
to, and a creation that fails and then works is reported as the fragility it is
rather than as a failure.

``fragile`` claims no more than non-determinism.  It does not promise that a
retry is what fixes it: the cause may be a timing window, a cap on how many
calendars an account may have, or something deterministic that nobody has
probed for yet - a cal_id length limit, say.  Retrying a few times is what a
client can do about it today, not an explanation of it.
"""

from unittest.mock import Mock

import pytest
from caldav.compatibility_hints import FeatureSet
from caldav.lib.error import MkcalendarError, NotFoundError

import caldav_server_tester.checks as checks_mod
from caldav_server_tester.checker import ServerQuirkChecker
from caldav_server_tester.checks import CheckGetCurrentUserPrincipal, CheckMakeDeleteCalendar

MKCOL_ONLY = MkcalendarError("405 Method Not Allowed\n\n<html>the requested method is not allowed</html>")
FLAKY = MkcalendarError("500 Internal Server Error\n\n<html>try again</html>")


@pytest.fixture
def check(monkeypatch):
    monkeypatch.setattr(checks_mod.time, "sleep", lambda _s: None)
    monkeypatch.setattr(checks_mod.DAVObject, "delete", lambda _self: None)
    client = Mock()
    client.features = FeatureSet()
    checker = ServerQuirkChecker(client, debug_mode=None)
    checker.principal = Mock()
    checker._checks_run.add(CheckGetCurrentUserPrincipal)
    checker._features_checked.set_feature("create-calendar.auto", False)
    probe = CheckMakeDeleteCalendar(checker)
    probe.expected_features = client.features
    return probe


def _calendar(exists=True):
    cal = Mock()

    def events(*args, **kwargs):
        if not exists:
            raise NotFoundError("no such calendar")
        return []

    cal.events.side_effect = events
    return cal


def test_a_creation_that_only_works_on_a_retry_is_fragile(check) -> None:
    principal = check.checker.principal
    cal = _calendar()
    principal.calendar.return_value = cal
    principal.make_calendar.side_effect = [FLAKY, cal]

    assert check._try_make_calendar(cal_id="x") is True

    observed = check.checker.features_checked.is_supported("create-calendar", dict)
    assert observed["support"] == "fragile"
    assert "failed 1 time(s) before it succeeded" in observed["behaviour"]


def test_an_unflappable_server_stays_full(check) -> None:
    principal = check.checker.principal
    cal = _calendar()
    principal.calendar.return_value = cal
    principal.make_calendar.return_value = cal

    check._try_make_calendar(cal_id="x")

    assert check.checker.features_checked.is_supported("create-calendar", str) == "full"
    assert principal.make_calendar.call_count == 1


def test_a_refused_method_is_not_retried(check) -> None:
    """Cyrus answers 405 to MKCALENDAR and wants MKCOL.

    Asking twice more cannot change the server's mind about the method, and the
    probe has a real answer to it - retrying with MKCOL - so it should get
    there without three rounds of the same refusal.
    """
    principal = check.checker.principal
    principal.calendar.return_value = _calendar(exists=False)
    principal.make_calendar.side_effect = MKCOL_ONLY

    assert check._try_make_calendar(cal_id="x") is False
    assert principal.make_calendar.call_count == 1


def test_a_creation_that_never_works_is_asked_a_few_times(check) -> None:
    principal = check.checker.principal
    principal.calendar.return_value = _calendar(exists=False)
    principal.make_calendar.side_effect = FLAKY

    assert check._try_make_calendar(cal_id="x") is False
    assert principal.make_calendar.call_count == CheckMakeDeleteCalendar.RETRY_ATTEMPTS


REFUSED = MkcalendarError("403 Forbidden\n\n<html>read-only account</html>")
## The library raises this one itself, without making any request at all, when
## the *profile* says the server cannot create calendars.
LIBRARY_REFUSAL = MkcalendarError("Creation of calendars (allegedly) not supported on this server")


@pytest.mark.parametrize("error", [REFUSED, LIBRARY_REFUSAL], ids=["403", "library-refusal"])
def test_a_refusal_that_cannot_change_is_asked_once(check, error) -> None:
    """Retrying is for bad luck, not for an answer the server means.

    A 4xx says the request is wrong, and the library's own pre-flight refusal
    never even reached the network - three attempts and two sleeps buy nothing,
    four times over per probe.
    """
    principal = check.checker.principal
    principal.calendar.return_value = _calendar(exists=False)
    principal.make_calendar.side_effect = error

    assert check._try_make_calendar(cal_id="x") is False
    assert principal.make_calendar.call_count == 1
