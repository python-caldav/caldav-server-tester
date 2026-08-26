"""Unit tests for the ``delete-calendar.free-namespace`` probe.

The verdict used to be inferred from whether a *fixed* cal_id could be created
at all, i.e. from whether a previous run's leftover calendar had been freed: no
evidence whatsoever on a first run, and a transient MKCALENDAR failure was
recorded as "the namespace is not freed on delete".  That is exactly what
happened against Robur on 2026-08-26 - one run red, the next green, with a
direct delete/re-create succeeding in between.  The probe now creates, deletes
and re-creates the same id inside one run, and only blames the namespace when a
fresh id succeeds where the deleted one keeps failing.
"""

from unittest.mock import Mock

import pytest
from caldav.compatibility_hints import FeatureSet
from caldav.lib.error import AuthorizationError, DAVError

import caldav_server_tester.checks as checks_mod
from caldav_server_tester.checker import ServerQuirkChecker
from caldav_server_tester.checks import CheckMakeDeleteCalendar

FEATURE = "delete-calendar.free-namespace"
CAL_ID = "caldav-server-checker-mkdel-test"


@pytest.fixture(autouse=True)
def _no_sleeping(monkeypatch):
    monkeypatch.setattr(checks_mod.time, "sleep", lambda _s: None)
    monkeypatch.setattr(checks_mod.DAVObject, "delete", lambda _self: None)


def _check(make_calendar, delete_calendar_supported=True):
    client = Mock()
    client.features = FeatureSet()
    checker = ServerQuirkChecker(client, debug_mode=None)
    ## _probe_make_delete only gets here after _try_make_calendar has recorded a
    ## verdict on delete-calendar, so the feature is always set by then.
    checker._features_checked.set_feature("delete-calendar", delete_calendar_supported)
    checker.principal = Mock()
    checker.principal.make_calendar.side_effect = make_calendar
    check = CheckMakeDeleteCalendar(checker)
    check.expected_features = client.features
    return check, checker


def test_recreating_the_deleted_id_means_the_namespace_is_free() -> None:
    check, checker = _check(make_calendar=lambda cal_id, **kw: Mock())
    check._probe_free_namespace(CAL_ID)
    assert checker.features_checked.is_supported(FEATURE, str) == "full"


def test_a_reserved_name_is_reported_as_not_free() -> None:
    """Nextcloud's trashbin: the deleted id stays refused while a fresh one works."""
    calls = []

    def make_calendar(cal_id, **kwargs):
        calls.append(cal_id)
        if cal_id == CAL_ID:
            raise AuthorizationError("calendar already exists")
        return Mock()

    check, checker = _check(make_calendar)
    check._probe_free_namespace(CAL_ID)
    assert checker.features_checked.is_supported(FEATURE, str) == "unsupported"
    assert any(c.startswith("testcalendar-") for c in calls), "no fresh cal_id was tried"


def test_a_transient_failure_is_unknown_rather_than_not_free() -> None:
    """The regression: when even a fresh cal_id cannot be created, the server is
    simply refusing to create calendars right now - that says nothing about the
    namespace, and must not be recorded as a feature the server lacks."""
    check, checker = _check(make_calendar=Mock(side_effect=DAVError("500 Internal Server Error")))
    check._probe_free_namespace(CAL_ID)
    assert checker.features_checked.is_supported(FEATURE, str) == "unknown"


def test_a_delayed_free_still_counts_as_free() -> None:
    """Some servers free the name a moment after the DELETE; polling covers it."""
    state = {"n": 0}

    def make_calendar(cal_id, **kwargs):
        state["n"] += 1
        if state["n"] < 3:
            raise AuthorizationError("calendar already exists")
        return Mock()

    check, checker = _check(make_calendar)
    check._probe_free_namespace(CAL_ID)
    assert checker.features_checked.is_supported(FEATURE, str) == "full"
    assert state["n"] == 3, "the probe gave up before the namespace was freed"


def test_untestable_when_delete_calendar_is_unsupported() -> None:
    check, checker = _check(make_calendar=lambda cal_id, **kw: Mock(), delete_calendar_supported=False)
    check._probe_free_namespace(CAL_ID)
    observed = checker.features_checked.is_supported(FEATURE, dict)
    assert observed["support"] == "unknown"
    assert "delete-calendar" in observed["behaviour"]
    checker.principal.make_calendar.assert_not_called()


class TestTheMkcolPath:
    """A server that needs MKCOL used to be reported as "namespace not freed".

    The fixed cal_id is only ever tried with MKCALENDAR before this branch is
    reached, so on a server that requires MKCOL it fails by construction - and
    the verdict was written from that failure, every run, with no evidence at
    all about what the DELETE does to the namespace.
    """

    def _drive(self, make_calendar, mkcol_works_on_the_fixed_id=True):
        check, checker = _check(make_calendar)
        attempted = []

        def _try_make_calendar(cal_id, **kwargs):
            attempted.append((cal_id, kwargs.get("method")))
            if kwargs.get("method") != "mkcol":
                return False
            if cal_id == CAL_ID:
                return mkcol_works_on_the_fixed_id
            return True

        check._try_make_calendar = _try_make_calendar
        check._probe_make_delete()
        return checker.features_checked, attempted

    def test_the_fixed_id_is_retried_with_mkcol_before_any_verdict(self) -> None:
        features, attempted = self._drive(make_calendar=lambda cal_id, **kw: Mock())
        assert (CAL_ID, "mkcol") in attempted, "the fixed cal_id was never tried with MKCOL"
        assert features.is_supported(FEATURE, str) == "full"

    def test_an_uncreatable_fixed_id_is_unknown_not_unsupported(self) -> None:
        features, _ = self._drive(
            make_calendar=lambda cal_id, **kw: Mock(),
            mkcol_works_on_the_fixed_id=False,
        )
        observed = features.is_supported(FEATURE, dict)
        assert observed["support"] == "unknown"
        assert "MKCOL" in observed["behaviour"]

    def test_the_fresh_id_control_uses_the_same_method(self) -> None:
        """Comparing an MKCOL failure against an MKCALENDAR success proves nothing."""
        calls = []

        def make_calendar(cal_id, **kwargs):
            calls.append((cal_id, kwargs.get("method")))
            if cal_id == CAL_ID:
                raise AuthorizationError("calendar already exists")
            return Mock()

        features, _ = self._drive(make_calendar)
        assert features.is_supported(FEATURE, str) == "unsupported"
        assert calls, "no calendar creation was attempted"
        assert {method for _cal_id, method in calls} == {"mkcol"}


class TestTheFixedIdIsNotAPreviousRunsLeftover:
    """The verdict must come from a calendar this run created and deleted.

    When the fixed cal_id cannot be created but a fresh one can, the fixed id
    was never created - so it was never deleted either, and re-creating it
    afterwards asks "is this name free?", not "does DELETE free a name?".  The
    first question is answered by whatever a *previous* run left behind, which
    is the inference this probe exists to stop making.
    """

    def _drive(self, make_calendar):
        check, checker = _check(make_calendar)
        probed = []
        real = check._probe_free_namespace

        def _probe_free_namespace(cal_id, **kwargs):
            probed.append(cal_id)
            return real(cal_id, **kwargs)

        def _try_make_calendar(cal_id, **kwargs):
            return cal_id != CAL_ID

        check._probe_free_namespace = _probe_free_namespace
        check._try_make_calendar = _try_make_calendar
        check._probe_make_delete()
        return checker.features_checked, probed

    def test_the_namespace_is_probed_on_the_id_this_run_actually_deleted(self) -> None:
        _features, probed = self._drive(make_calendar=lambda cal_id, **kw: Mock())
        assert probed, "the namespace was never probed"
        assert CAL_ID not in probed, "probed an id this run never created, so never deleted"
        assert all(c.startswith("testcalendar-") for c in probed)
