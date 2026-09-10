"""A verdict that is not "full" must not switch probes off by accident.

`FeatureSet._POSITIVE_STATUSES` is `{full, quirk}`, so `is_supported()` is
False for `fragile` and for `ungraceful` — both of which this tool records for
servers that *do* the thing, just unreliably or rudely.  Every gate that asks
"can we still probe this?" therefore has to ask the question it means rather
than the plain boolean, or measuring a fragility silently costs the probes
downstream of it.

That is the trap these tests exist to hold shut: a probe that grades honestly
and then disables itself is worse than one that never looked.
"""

from unittest.mock import Mock

import pytest
from caldav.compatibility_hints import FeatureSet
from caldav.lib.error import DeleteError, NotFoundError

import caldav_server_tester.checks as checks_mod
from caldav_server_tester.checker import ServerQuirkChecker
from caldav_server_tester.checks import (
    CheckGetCurrentUserPrincipal,
    CheckMakeDeleteCalendar,
    PrepareCalendar,
)

CAL_ID = CheckMakeDeleteCalendar.MKDEL_CAL_ID


def _checker(monkeypatch, **features):
    monkeypatch.setattr(checks_mod.time, "sleep", lambda _s: None)
    client = Mock()
    client.features = FeatureSet()
    checker = ServerQuirkChecker(client, debug_mode=None)
    checker.principal = Mock()
    checker._checks_run.add(CheckGetCurrentUserPrincipal)
    checker._features_checked.set_feature("create-calendar.auto", False)
    for feature, value in features.items():
        checker._features_checked.set_feature(feature.replace("_", "-"), value)
    return checker


def _calendar(exists=True):
    cal = Mock()

    def events(*args, **kwargs):
        if not exists:
            raise NotFoundError("no such calendar")
        return []

    cal.events.side_effect = events
    return cal


class TestFragileCreation:
    """A creation that works on the second ask still creates calendars."""

    def test_the_run_is_not_aborted(self, monkeypatch) -> None:
        """PrepareCalendar raised RuntimeError and took the whole run with it."""
        checker = _checker(monkeypatch, create_calendar={"support": "fragile", "behaviour": "flaky"})
        checker.principal.calendar.side_effect = NotFoundError("nothing there yet")
        checker.principal.make_calendar.side_effect = NotFoundError("and creation just failed too")
        prepare = PrepareCalendar(checker)
        prepare.expected_features = checker._client_obj.features

        ## The interesting part is which exception comes out: a RuntimeError
        ## says "this server cannot create calendars", which is not what
        ## fragile means and aborts the run.
        with pytest.raises(Exception) as caught:  # noqa: PT011 - the type is the assertion
            prepare._find_or_create_calendar("cal-id", "name", {})
        assert not isinstance(caught.value, RuntimeError), caught.value

    def test_set_displayname_is_still_probed(self, monkeypatch) -> None:
        checker = _checker(monkeypatch)
        check = CheckMakeDeleteCalendar(checker)
        check.expected_features = checker._client_obj.features
        checker._features_checked.set_feature("create-calendar", {"support": "fragile", "behaviour": "flaky"})
        probed = []
        monkeypatch.setattr(check, "_check_set_displayname", lambda: probed.append(True))
        monkeypatch.setattr(check, "_probe_make_delete", lambda: None)

        check._run_check()

        assert probed == [True]

    def test_cleanup_still_deletes_what_it_made(self, monkeypatch) -> None:
        checker = _checker(monkeypatch)
        checker._features_checked.set_feature("create-calendar", {"support": "fragile", "behaviour": "flaky"})
        checker._features_checked.set_feature("delete-calendar", True)
        checker.calendar = Mock()
        checker.tasklist = checker.calendar
        checker.journallist = checker.calendar
        checker.calendar_was_created = True
        checker._client_obj.features.copyFeatureSet(
            {"test-calendar.compatibility-tests": {"cleanup": True}}, collapse=False
        )
        monkeypatch.setattr(checker, "_purge_probe_calendars", lambda: None)

        checker.cleanup()

        checker.calendar.delete.assert_called_once()


class TestUngracefulDelete:
    """A DELETE that errors but deletes has still deleted."""

    def _server(self, monkeypatch):
        """A fake whose every DELETE reports 500 and deletes anyway."""
        existing = set()

        def calendar(cal_id=None, name=None):
            cal = Mock()
            cal.cal_id = cal_id

            def events(*args, **kwargs):
                if cal_id not in existing:
                    raise NotFoundError("gone")
                return []

            cal.events.side_effect = events
            return cal

        def make_calendar(cal_id=None, **kwargs):
            existing.add(cal_id)
            return calendar(cal_id=cal_id)

        def delete(cal):
            existing.discard(cal.cal_id)
            raise DeleteError("500 Internal Server Error")

        monkeypatch.setattr(checks_mod.DAVObject, "delete", delete)
        return calendar, make_calendar, existing

    def test_the_namespace_probe_still_runs(self, monkeypatch) -> None:
        calendar, make_calendar, _existing = self._server(monkeypatch)
        checker = _checker(monkeypatch)
        checker.principal.calendar.side_effect = calendar
        checker.principal.make_calendar.side_effect = make_calendar
        check = CheckMakeDeleteCalendar(checker)
        check.expected_features = checker._client_obj.features

        check._probe_make_delete()

        assert checker.features_checked.is_supported("delete-calendar", str) == "ungraceful"
        assert checker.features_checked.is_supported("delete-calendar.free-namespace", str) == "full"

    def test_cleanup_still_deletes_what_it_made(self, monkeypatch) -> None:
        checker = _checker(monkeypatch)
        checker._features_checked.set_feature("create-calendar", True)
        checker._features_checked.set_feature(
            "delete-calendar", {"support": "ungraceful", "behaviour": "errors but deletes"}
        )
        checker.calendar = Mock()
        checker.tasklist = checker.calendar
        checker.journallist = checker.calendar
        checker.calendar_was_created = True
        checker._client_obj.features.copyFeatureSet(
            {"test-calendar.compatibility-tests": {"cleanup": True}}, collapse=False
        )
        monkeypatch.setattr(checker, "_purge_probe_calendars", lambda: None)

        checker.cleanup()

        checker.calendar.delete.assert_called_once()


class TestCleanupSurvivesAFailingDelete:
    """One calendar that will not go must not cost the whole sweep.

    `Calendar.delete()` switches its retry loop on from the *profile*, not from
    what this run observed, so a server measured fragile whose profile says
    otherwise raises straight out of the call — and an unguarded call there
    skips the probe-calendar purge, which is the pile-up the gate exists to
    prevent.
    """

    def test_the_purge_still_runs(self, monkeypatch) -> None:
        checker = _checker(monkeypatch)
        checker._features_checked.set_feature("create-calendar", True)
        checker._features_checked.set_feature("delete-calendar", {"support": "fragile", "behaviour": "flaky"})
        checker.calendar = Mock()
        checker.calendar.delete.side_effect = DeleteError("500 Internal Server Error")
        checker.tasklist = checker.calendar
        checker.journallist = checker.calendar
        checker.calendar_was_created = True
        checker._client_obj.features.copyFeatureSet(
            {"test-calendar.compatibility-tests": {"cleanup": True}}, collapse=False
        )
        purged = []
        monkeypatch.setattr(checker, "_purge_probe_calendars", lambda: purged.append(True))

        checker.cleanup()

        assert purged == [True]
