"""A fatal configuration error must reach the user, not become a wall of 'unknown'.

Check.run() catches broad exceptions so one misbehaving probe cannot abort the
run.  PrepareCalendar raises RuntimeError for the one case the user must act on
-- no test calendar exists and the server will not create one -- and that
message is actionable ("specify a calendar with --caldav-calendar").  Swallowed,
it leaves checker.calendar unset, so every dependent check then fails too and
the user gets a full report of 'unknown' with the real cause buried in a warning.
"""

from unittest.mock import Mock

import pytest
from caldav.compatibility_hints import FeatureSet

from caldav_server_tester.checks_base import Check


class _Boom(Check):
    features_to_be_checked = {"save-load.event"}
    exc: Exception = RuntimeError("boom")

    def _run_check(self):
        raise self.exc


def _checker() -> Mock:
    checker = Mock()
    checker._features_checked = FeatureSet()
    checker.features_checked = checker._features_checked
    checker.debug_mode = None
    checker._client_obj = Mock()
    checker._client_obj.features = FeatureSet()
    checker.expected_features = FeatureSet()
    checker._checks_run = set()
    return checker


class TestFatalErrorsPropagate:
    def test_runtime_error_is_not_swallowed(self) -> None:
        check = _Boom(_checker())
        check.exc = RuntimeError("Server does not support calendar creation and no existing test calendar was found.")
        with pytest.raises(RuntimeError, match="does not support calendar creation"):
            check.run_check()

    def test_assertion_error_still_propagates(self) -> None:
        check = _Boom(_checker())
        check.exc = AssertionError("still fatal")
        with pytest.raises(AssertionError):
            check.run_check()


class TestOrdinaryProbeFailuresAreStillContained:
    def test_a_server_error_is_still_reported_as_unknown(self) -> None:
        """The containment this except clause exists for must keep working."""
        from caldav.lib.error import DAVError

        checker = _checker()
        check = _Boom(checker)
        check.exc = DAVError("500 Internal Server Error")
        check.run_check()
        assert checker._features_checked.is_supported("save-load.event", str) == "unknown"
