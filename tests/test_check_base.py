"""Unit tests for the Check base class"""

## DISCLAIMER: those tests are AI-generated, gone through a very quick human QA

import logging
from unittest.mock import Mock

import pytest
from caldav.compatibility_hints import FeatureSet
from caldav.lib.error import NotFoundError

from caldav_server_tester.checks_base import Check


class TestCheckSetFeature:
    """Test the Check.set_feature method"""

    def create_mock_checker(self, debug_mode="logging") -> Mock:
        """Helper to create a mock checker object"""
        checker = Mock()
        checker._features_checked = FeatureSet()
        checker.debug_mode = debug_mode
        checker._client_obj = Mock()
        checker._client_obj.features = FeatureSet()
        return checker

    def create_check_instance(self, debug_mode="logging") -> Check:
        """Helper to create a Check instance with mocked dependencies"""
        checker = self.create_mock_checker(debug_mode=debug_mode)
        check = Check(checker)
        check.expected_features = FeatureSet()
        return check

    def test_set_feature_with_true_converts_to_full_support(self) -> None:
        """set_feature(feature, True) should set support to 'full'"""
        check = self.create_check_instance(debug_mode=None)
        check.set_feature("create-calendar", True)

        result = check.checker._features_checked.is_supported("create-calendar", dict)
        assert result == {"support": "full"}

    def test_set_feature_with_false_converts_to_unsupported(self) -> None:
        """set_feature(feature, False) should set support to 'unsupported'"""
        check = self.create_check_instance(debug_mode=None)
        check.set_feature("create-calendar", False)

        result = check.checker._features_checked.is_supported("create-calendar", dict)
        assert result == {"support": "unsupported"}

    def test_set_feature_with_none_converts_to_unknown(self) -> None:
        """set_feature(feature, None) should set support to 'unknown'"""
        check = self.create_check_instance(debug_mode=None)
        check.set_feature("create-calendar", None)

        result = check.checker._features_checked.is_supported("create-calendar", dict)
        assert result == {"support": "unknown"}

    def test_set_feature_with_string_converts_to_dict(self) -> None:
        """set_feature(feature, 'value') should set support to 'value'"""
        check = self.create_check_instance(debug_mode=None)
        check.set_feature("create-calendar", "fragile")

        result = check.checker._features_checked.is_supported("create-calendar", dict)
        assert result == {"support": "fragile"}

    def test_set_feature_with_dict_passes_through(self) -> None:
        """set_feature(feature, dict) should pass the dict through"""
        check = self.create_check_instance(debug_mode=None)
        feature_dict = {"support": "quirk", "behaviour": "test behaviour"}
        check.set_feature("create-calendar", feature_dict)

        result = check.checker._features_checked.is_supported("create-calendar", dict)
        assert result == feature_dict

    def test_set_feature_with_invalid_type_raises_assertion(self) -> None:
        """set_feature with unsupported type should raise AssertionError"""
        check = self.create_check_instance(debug_mode=None)

        with pytest.raises(AssertionError):
            check.set_feature("create-calendar", 123)

        with pytest.raises(AssertionError):
            check.set_feature("create-calendar", [])

    def test_set_feature_debug_mode_none_skips_validation(self) -> None:
        """With debug_mode=None, validation should be skipped"""
        check = self.create_check_instance(debug_mode=None)
        # Should not raise even if expectations don't match
        check.set_feature("create-calendar", True)
        # If we got here without exception, test passed

    def test_set_feature_with_nested_feature_names(self) -> None:
        """Features with dotted names should work correctly"""
        check = self.create_check_instance(debug_mode=None)
        check.set_feature("create-calendar.auto", True)

        result = check.checker._features_checked.is_supported("create-calendar.auto", dict)
        assert result == {"support": "full"}


class TestCheckFeatureChecked:
    """Test the Check.feature_checked method"""

    def create_check_instance(self) -> Check:
        """Helper to create a Check instance"""
        checker = Mock()
        checker._features_checked = FeatureSet()
        checker._client_obj = Mock()
        return Check(checker)

    def test_feature_checked_delegates_to_featureset(self) -> None:
        """feature_checked should delegate to the FeatureSet.is_supported method"""
        check = self.create_check_instance()
        check.checker._features_checked.copyFeatureSet({"create-calendar": {"support": "full"}}, collapse=False)

        result = check.feature_checked("create-calendar", bool)
        assert result is True

    def test_feature_checked_returns_bool_by_default(self) -> None:
        """feature_checked without return_type should return bool"""
        check = self.create_check_instance()
        check.checker._features_checked.copyFeatureSet({"create-calendar": {"support": "full"}}, collapse=False)

        result = check.feature_checked("create-calendar")
        assert isinstance(result, bool)
        assert result is True

    def test_feature_checked_can_return_dict(self) -> None:
        """feature_checked with return_type=dict should return dict"""
        check = self.create_check_instance()
        check.checker._features_checked.copyFeatureSet(
            {"create-calendar": {"support": "full", "behaviour": "test"}}, collapse=False
        )

        result = check.feature_checked("create-calendar", dict)
        assert isinstance(result, dict)
        assert result == {"support": "full", "behaviour": "test"}


class TestCheckRunCheck:
    """Test the Check.run_check method and dependency resolution"""

    def test_run_check_executes_dependencies_first(self) -> None:
        """run_check should execute all dependencies before running main check"""

        # Create a dependency check
        class DependencyCheck(Check):
            executed = False
            features_to_be_checked = set()  # Must define this attribute

            def _run_check(self) -> None:
                DependencyCheck.executed = True

        # Create main check that depends on DependencyCheck
        class MainCheck(Check):
            depends_on = {DependencyCheck}
            features_to_be_checked = set()

            def _run_check(self) -> None:
                # Verify dependency was executed first
                assert DependencyCheck.executed

        checker = Mock()
        checker._features_checked = FeatureSet()
        checker._checks_run = set()
        checker._client_obj = Mock()
        checker._client_obj.features = FeatureSet()

        main_check = MainCheck(checker)
        main_check.run_check()

        assert DependencyCheck.executed

    def test_run_check_only_once_prevents_duplicate_execution(self) -> None:
        """run_check with only_once=True should not re-execute same check"""

        class TestCheck(Check):
            execution_count = 0
            features_to_be_checked = set()

            def _run_check(self) -> None:
                TestCheck.execution_count += 1

        checker = Mock()
        checker._features_checked = FeatureSet()
        checker._checks_run = set()
        checker._client_obj = Mock()
        checker._client_obj.features = FeatureSet()

        check1 = TestCheck(checker)
        check1.run_check(only_once=True)
        check1.run_check(only_once=True)

        assert TestCheck.execution_count == 1

    def test_run_check_only_once_false_allows_multiple_executions(self) -> None:
        """run_check with only_once=False should allow re-execution"""

        class TestCheck(Check):
            execution_count = 0
            features_to_be_checked = set()

            def _run_check(self) -> None:
                TestCheck.execution_count += 1

        checker = Mock()
        checker._features_checked = FeatureSet()
        checker._checks_run = set()
        checker._client_obj = Mock()
        checker._client_obj.features = FeatureSet()

        check1 = TestCheck(checker)
        check1.run_check(only_once=False)
        check2 = TestCheck(checker)
        check2.run_check(only_once=False)

        assert TestCheck.execution_count == 2

    def test_run_check_tracks_executed_checks(self) -> None:
        """run_check should add check class to _checks_run set"""

        class TestCheck(Check):
            features_to_be_checked = set()

            def _run_check(self) -> None:
                pass

        checker = Mock()
        checker._features_checked = FeatureSet()
        checker._checks_run = set()
        checker._client_obj = Mock()
        checker._client_obj.features = FeatureSet()

        check = TestCheck(checker)
        check.run_check()

        assert TestCheck in checker._checks_run

    def test_run_check_restores_client_features(self) -> None:
        """run_check should restore original client features after execution"""
        original_features = FeatureSet()
        original_features.copyFeatureSet({"create-calendar": {"support": "full"}}, collapse=False)

        class TestCheck(Check):
            features_to_be_checked = set()

            def _run_check(self) -> None:
                # Modify features during check
                self.checker._client_obj.features = FeatureSet()

        checker = Mock()
        checker._features_checked = FeatureSet()
        checker._checks_run = set()
        checker._client_obj = Mock()
        checker._client_obj.features = original_features

        check = TestCheck(checker)
        check.run_check()

        # Features should be restored to original
        assert checker._client_obj.features == original_features

    @pytest.mark.filterwarnings("ignore:Unknown feature:UserWarning:caldav")
    def test_run_check_verifies_declared_features_checked(self) -> None:
        """run_check should verify all declared features were checked"""
        # Uses intentionally fake feature names (feature1, feature2) to test
        # AssertionError logic; the UserWarning from caldav about unknown features is expected.

        class TestCheck(Check):
            features_to_be_checked = {"feature1", "feature2"}

            def _run_check(self) -> None:
                # Only check feature1, not feature2
                self.set_feature("feature1", True)

        checker = Mock()
        checker._features_checked = FeatureSet()
        checker._checks_run = set()
        checker._client_obj = Mock()
        checker._client_obj.features = FeatureSet()
        checker.debug_mode = None

        check = TestCheck(checker)

        # Should raise AssertionError for missing feature2
        with pytest.raises(AssertionError):
            check.run_check()

    def test_run_check_unset_subfeature_derives_from_parent(self) -> None:
        """When a declared sub-feature is left unprobed but its parent IS set, the
        assert must not fire and is_supported() derives the sub-feature from the
        parent (code review #4 — derivation handles this, no explicit value needed).
        """

        class TestCheck(Check):
            features_to_be_checked = {"search.time-range.todo.no-dtstart"}

            def _run_check(self) -> None:
                ## parent says VTODO time-range search is unsupported, so the
                ## no-dtstart sub-feature can't be probed and is deliberately left
                ## unset; it must derive to "unsupported" via the caldav library.
                self.set_feature("search.time-range.todo", {"support": "unsupported"})

        checker = Mock()
        checker._features_checked = FeatureSet()
        checker._checks_run = set()
        checker._client_obj = Mock()
        checker._client_obj.features = FeatureSet()
        checker.debug_mode = None

        check = TestCheck(checker)
        check.run_check()  # must NOT raise — parent-collapse covers the sub-feature

        assert checker._features_checked.is_supported("search.time-range.todo.no-dtstart", str) == "unsupported"

    def test_run_check_base_class_raises_not_implemented(self) -> None:
        """Calling run_check on base Check class should raise NotImplementedError"""
        checker = Mock()
        checker._features_checked = FeatureSet()
        checker._checks_run = set()
        checker._client_obj = Mock()
        checker._client_obj.features = FeatureSet()

        check = Check(checker)

        with pytest.raises(NotImplementedError):
            check.run_check()


class TestPollCalendar:
    """Test the shared calendar polling helper on the Check base class.

    Three near-identical poll loops used to live in checks.py (wait for a
    calendar to materialise, wait for it to disappear, and a hand-rolled copy in
    PrepareCalendar).  They are one helper here; these tests pin both polarities
    and the round-trip count, since every extra probe is a network round-trip
    against a server we already know is slow.
    """

    def _check(self, monkeypatch) -> Check:
        import caldav_server_tester.checks_base as base

        monkeypatch.setattr(base.time, "sleep", lambda _s: None)
        checker = Mock()
        checker._features_checked = FeatureSet()
        checker._client_obj = Mock()
        checker._client_obj.features = FeatureSet()
        checker.debug_mode = None
        return Check(checker)

    @staticmethod
    def _calendar(fail_times: int) -> Mock:
        """A calendar whose events() raises NotFoundError the first N calls."""
        state = {"n": 0}

        def events():
            state["n"] += 1
            if state["n"] <= fail_times:
                raise NotFoundError("Node could not be found")
            return []

        cal = Mock()
        cal.events.side_effect = events
        cal.probe_count = state
        return cal

    def test_polls_until_accessible(self, monkeypatch) -> None:
        check = self._check(monkeypatch)
        cal = self._calendar(fail_times=3)
        check.checker.principal.calendar.return_value = cal

        found, waited = check._poll_calendar(cal_id="x")

        assert found is cal
        assert waited == 3

    def test_polls_until_gone(self, monkeypatch) -> None:
        """The delete probe wants the opposite polarity: wait for the 404."""
        check = self._check(monkeypatch)
        state = {"n": 0}

        def events():
            state["n"] += 1
            if state["n"] > 2:
                raise NotFoundError("gone at last")
            return []

        cal = Mock()
        cal.events.side_effect = events
        check.checker.principal.calendar.return_value = cal

        found, waited = check._poll_calendar(cal_id="x", until_accessible=False)

        assert found is None
        assert waited == 2

    def test_timeout_returns_none(self, monkeypatch) -> None:
        check = self._check(monkeypatch)
        cal = self._calendar(fail_times=99)
        check.checker.principal.calendar.return_value = cal

        found, waited = check._poll_calendar(cal_id="x", timeout=4)

        assert found is None
        assert waited == 4

    def test_probes_once_per_second(self, monkeypatch) -> None:
        """One accessibility probe per iteration - no redundant re-probe.

        The old helper called _calendar_is_accessible() once more after the loop
        to build its return value, paying an extra round-trip on every call.
        """
        check = self._check(monkeypatch)
        cal = self._calendar(fail_times=99)
        check.checker.principal.calendar.return_value = cal

        check._poll_calendar(cal_id="x", timeout=3)

        ## 4 probes: the immediate one plus one per second waited
        assert cal.probe_count["n"] == 4

    def test_immediate_success_does_not_sleep(self, monkeypatch) -> None:
        import caldav_server_tester.checks_base as base

        slept = []
        monkeypatch.setattr(base.time, "sleep", slept.append)
        checker = Mock()
        checker._features_checked = FeatureSet()
        checker._client_obj = Mock()
        checker._client_obj.features = FeatureSet()
        checker.debug_mode = None
        check = Check(checker)
        cal = self._calendar(fail_times=0)
        checker.principal.calendar.return_value = cal

        found, waited = check._poll_calendar(cal_id="x")

        assert (found, waited) == (cal, 0)
        assert slept == []

    def test_polls_a_supplied_calendar_object_without_refetching(self, monkeypatch) -> None:
        """PrepareCalendar polls the object make_calendar returned.

        Re-fetching by cal_id would break on servers where a created calendar
        does not live at the requested cal_id (create-calendar.stable-url
        unsupported - Zimbra hands back an opaque cal://0/NNN URL).
        """
        check = self._check(monkeypatch)
        cal = self._calendar(fail_times=2)

        found, waited = check._poll_calendar(cal=cal)

        assert (found, waited) == (cal, 2)
        check.checker.principal.calendar.assert_not_called()


class TestObservedDelayWarning:
    """An observed delay is checked against the configured one.

    A write-delay in a server profile is a number somebody wrote by hand, and
    the only way to find out it is too small is to measure the server.  Once a
    probe records how long the server actually took, set_feature compares it
    with what the profile asks a client to sleep and complains through the same
    debug_mode machinery as any other unmet expectation.
    """

    def _check(self, configured=None, debug_mode="logging") -> Check:
        checker = Mock()
        checker._features_checked = FeatureSet()
        checker.debug_mode = debug_mode
        checker._client_obj = Mock()
        checker._client_obj.features = FeatureSet()
        check = Check(checker)
        expected = FeatureSet()
        if configured is not None:
            expected.copyFeatureSet({"write-delay": {"behaviour": "delay", "delay": configured}}, collapse=False)
        check.expected_features = expected
        return check

    @staticmethod
    def _delayed(delay, **extra):
        return {"support": "quirk", "behaviour": "delayed creation", "delay": delay, **extra}

    def test_warns_when_the_observed_delay_eats_the_margin(self, caplog) -> None:
        check = self._check(configured=10)
        with caplog.at_level(logging.ERROR):
            check.set_feature("create-calendar", self._delayed(9))
        assert "observed delay" in caplog.text
        assert "create-calendar" in caplog.text
        assert "9" in caplog.text and "10" in caplog.text

    def test_quiet_when_the_configured_delay_has_room(self, caplog) -> None:
        check = self._check(configured=10)
        with caplog.at_level(logging.ERROR):
            check.set_feature("create-calendar", self._delayed(4))
        assert "observed delay" not in caplog.text

    def test_warns_when_nothing_is_configured(self, caplog) -> None:
        """A delay nobody configured is the case worth hearing about."""
        check = self._check(configured=None)
        with caplog.at_level(logging.ERROR):
            check.set_feature("create-calendar", self._delayed(3))
        assert "observed delay" in caplog.text
        assert "no write-delay is configured" in caplog.text

    def test_a_lower_bound_always_warns(self, caplog) -> None:
        """The probe gave up waiting, so the real delay is longer than this."""
        check = self._check(configured=100)
        with caplog.at_level(logging.ERROR):
            check.set_feature("create-calendar", self._delayed(10, **{"delay-is-lower-bound": True}))
        assert "observed delay" in caplog.text

    def test_a_zero_delay_says_nothing(self, caplog) -> None:
        check = self._check(configured=10)
        with caplog.at_level(logging.ERROR):
            check.set_feature("write-delay", {"support": "full", "save-load-delay": 0, "delay": 0})
        assert "observed delay" not in caplog.text

    def test_silent_when_debug_mode_is_off(self, caplog) -> None:
        check = self._check(configured=None, debug_mode=None)
        with caplog.at_level(logging.ERROR):
            check.set_feature("create-calendar", self._delayed(9))
        assert caplog.text == ""

    def test_a_fragile_verdict_still_gets_the_delay_checked(self, caplog) -> None:
        """The fragile/unknown early return must not swallow the comparison."""
        check = self._check(configured=10)
        with caplog.at_level(logging.ERROR):
            check.set_feature("delete-calendar", {"support": "fragile", "behaviour": "slow", "delay": 9})
        assert "observed delay" in caplog.text
