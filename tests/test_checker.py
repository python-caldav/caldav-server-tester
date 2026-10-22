"""Unit tests for the ServerQuirkChecker class"""

## DISCLAIMER: those tests are AI-generated, gone through a very quick human QA

import json
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, Mock, patch

import pytest
from caldav.compatibility_hints import FeatureSet

from caldav_server_tester.checker import ServerQuirkChecker
from caldav_server_tester.checks_base import Check


class TestServerQuirkCheckerInit:
    """Test ServerQuirkChecker initialization"""

    def test_init_sets_client_object(self) -> None:
        """Initialization should store the client object"""
        client = Mock()
        client.features = FeatureSet()
        checker = ServerQuirkChecker(client)

        assert checker._client_obj == client

    def test_init_creates_empty_features_checked(self) -> None:
        """Initialization should create an empty FeatureSet"""
        client = Mock()
        client.features = FeatureSet()
        checker = ServerQuirkChecker(client)

        assert isinstance(checker._features_checked, FeatureSet)
        assert len(checker._features_checked.dotted_feature_set_list()) == 0

    def test_init_sets_default_calendar_to_none(self) -> None:
        """Initialization should set _default_calendar to None"""
        client = Mock()
        client.features = FeatureSet()
        checker = ServerQuirkChecker(client)

        assert checker._default_calendar is None

    def test_init_creates_empty_checks_run_set(self) -> None:
        """Initialization should create an empty _checks_run set"""
        client = Mock()
        client.features = FeatureSet()
        checker = ServerQuirkChecker(client)

        assert isinstance(checker._checks_run, set)
        assert len(checker._checks_run) == 0

    def test_init_stores_expected_features(self) -> None:
        """Initialization should store client's features as expected_features"""
        client = Mock()
        client.features = FeatureSet()
        checker = ServerQuirkChecker(client)

        assert checker.expected_features == client.features

    def test_init_sets_debug_mode_default(self) -> None:
        """Initialization should set debug_mode to 'logging' by default"""
        client = Mock()
        client.features = FeatureSet()
        checker = ServerQuirkChecker(client)

        assert checker.debug_mode == "logging"

    def test_init_sets_custom_debug_mode(self) -> None:
        """Initialization should accept custom debug_mode"""
        client = Mock()
        client.features = FeatureSet()
        checker = ServerQuirkChecker(client, debug_mode="assert")

        assert checker.debug_mode == "assert"


class TestServerQuirkCheckerProperties:
    """Test ServerQuirkChecker properties"""

    def test_features_checked_property_returns_features(self) -> None:
        """features_checked property should return _features_checked"""
        client = Mock()
        client.features = FeatureSet()
        checker = ServerQuirkChecker(client)

        assert checker.features_checked == checker._features_checked

    def test_features_checked_is_featureset(self) -> None:
        """features_checked should return a FeatureSet instance"""
        client = Mock()
        client.features = FeatureSet()
        checker = ServerQuirkChecker(client)

        assert isinstance(checker.features_checked, FeatureSet)


class TestServerQuirkCheckerCheckOne:
    """Test ServerQuirkChecker.check_one method"""

    @patch("caldav_server_tester.checker.checks")
    def test_check_one_retrieves_check_by_name(self, mock_checks) -> None:
        """check_one should retrieve check class by name from checks module"""
        client = Mock()
        client.features = FeatureSet()
        checker = ServerQuirkChecker(client)

        # Create a mock check class
        MockCheck = MagicMock(spec=Check)
        mock_check_instance = MagicMock(spec=Check)
        MockCheck.return_value = mock_check_instance
        mock_checks.TestCheck = MockCheck

        checker.check_one("TestCheck")

        # Verify the check was retrieved and instantiated
        mock_checks.TestCheck.assert_called_once_with(checker)
        mock_check_instance.run_check.assert_called_once()


class TestServerQuirkCheckerReport:
    """Test ServerQuirkChecker.report method"""

    def test_report_returns_dict_when_requested(self) -> None:
        """report(return_what=dict) should return a dictionary"""
        client = Mock()
        client.features = FeatureSet()
        client.server_name = "Test Server"
        client.url = "https://example.com/caldav"
        checker = ServerQuirkChecker(client)

        result = checker.report(return_what=dict)

        assert isinstance(result, dict)

    def test_report_dict_contains_required_fields(self) -> None:
        """report dictionary should contain all required fields"""
        client = Mock()
        client.features = FeatureSet()
        client.server_name = "Test Server"
        client.url = "https://example.com/caldav"
        checker = ServerQuirkChecker(client)

        result = checker.report(return_what=dict)

        assert "caldav_version" in result
        assert "ts" in result
        assert "name" in result
        assert "url" in result
        assert "features" in result

    def test_report_dict_includes_client_data(self) -> None:
        """report dictionary should include data from client object"""
        client = Mock()
        client.features = FeatureSet()
        client.server_name = "Test Server"
        client.url = "https://example.com/caldav"
        checker = ServerQuirkChecker(client)

        result = checker.report(return_what=dict)

        assert result["name"] == "Test Server"
        assert result["url"] == "https://example.com/caldav"

    def test_report_dict_includes_timestamp(self) -> None:
        """report dictionary should include a timestamp"""
        client = Mock()
        client.features = FeatureSet()
        client.server_name = "Test Server"
        client.url = "https://example.com/caldav"
        checker = ServerQuirkChecker(client)

        before = time.time()
        result = checker.report(return_what=dict)
        after = time.time()

        assert "ts" in result
        assert isinstance(result["ts"], int | float)
        assert before <= result["ts"] <= after

    def test_report_dict_includes_features(self) -> None:
        """report dictionary should include checked features"""
        client = Mock()
        client.features = FeatureSet()
        client.server_name = "Test Server"
        client.url = "https://example.com/caldav"
        checker = ServerQuirkChecker(client)

        # Add some features (use registered feature names)
        checker._features_checked.copy_feature_set({"create-calendar": {"support": "full"}}, collapse=False)

        result = checker.report(return_what=dict)

        assert "features" in result
        assert isinstance(result["features"], dict)

    def test_report_json_returns_valid_json_string(self) -> None:
        """report(return_what='json') should return a valid JSON string"""
        client = Mock()
        client.features = FeatureSet()
        client.server_name = "Test Server"
        client.url = "https://example.com/caldav"
        checker = ServerQuirkChecker(client)

        result = checker.report(return_what="json")

        assert isinstance(result, str)
        # Should be parseable as JSON
        parsed = json.loads(result)
        assert isinstance(parsed, dict)

    def test_report_json_is_formatted(self) -> None:
        """report JSON should be formatted with indentation"""
        client = Mock()
        client.features = FeatureSet()
        client.server_name = "Test Server"
        client.url = "https://example.com/caldav"
        checker = ServerQuirkChecker(client)

        result = checker.report(return_what="json")

        # Formatted JSON should contain newlines
        assert "\n" in result
        # Formatted JSON should contain indentation
        assert "    " in result

    def test_report_str_returns_text(self) -> None:
        """report(return_what=str) should return a human-readable string"""
        client = Mock()
        client.features = FeatureSet()
        client.server_name = "Test Server"
        client.url = "https://example.com/caldav"
        checker = ServerQuirkChecker(client)

        result = checker.report(return_what=str)
        assert isinstance(result, str)
        assert "https://example.com/caldav" in result

    def test_report_str_verbose_shows_feature_description(self) -> None:
        """report text in verbose mode should include the feature description from FeatureSet.FEATURES"""
        client = Mock()
        client.features = FeatureSet()
        client.server_name = "Test Server"
        client.url = "https://example.com/caldav"
        checker = ServerQuirkChecker(client)
        checker._features_checked.copy_feature_set({"create-calendar": {"support": "full"}}, collapse=False)

        result = checker.report(return_what=str, verbose=True)

        expected_description = FeatureSet.FEATURES["create-calendar"]["description"]
        assert expected_description in result

    def test_report_str_nonverbose_shows_description_for_non_full_features(self) -> None:
        """report text in non-verbose mode should show descriptions for non-full features"""
        client = Mock()
        client.features = FeatureSet()
        client.server_name = "Test Server"
        client.url = "https://example.com/caldav"
        checker = ServerQuirkChecker(client)
        checker._features_checked.copy_feature_set({"create-calendar": {"support": "unsupported"}}, collapse=False)

        result = checker.report(return_what=str, verbose=False)

        expected_description = FeatureSet.FEATURES["create-calendar"]["description"]
        assert expected_description in result

    def test_report_invalid_return_type_raises_not_implemented(self) -> None:
        """report with invalid return_what should raise NotImplementedError"""
        client = Mock()
        client.features = FeatureSet()
        client.server_name = "Test Server"
        client.url = "https://example.com/caldav"
        checker = ServerQuirkChecker(client)

        with pytest.raises(NotImplementedError):
            checker.report(return_what=list)

    def test_report_verbose_parameter_accepted(self) -> None:
        """report should accept verbose parameter without error"""
        client = Mock()
        client.features = FeatureSet()
        client.server_name = "Test Server"
        client.url = "https://example.com/caldav"
        checker = ServerQuirkChecker(client)

        # Should not raise error
        result = checker.report(verbose=True, return_what=dict)
        assert isinstance(result, dict)

    def test_report_yaml_returns_valid_yaml_string(self) -> None:
        """report(return_what='yaml') should return a valid YAML string"""
        import yaml

        client = Mock()
        client.features = FeatureSet()
        client.server_name = "Test Server"
        client.url = "https://example.com/caldav"
        checker = ServerQuirkChecker(client)

        result = checker.report(return_what="yaml")

        assert isinstance(result, str)
        parsed = yaml.safe_load(result)
        assert isinstance(parsed, dict)
        assert "name" in parsed
        assert "features" in parsed

    def test_report_hints_returns_python_dict_format(self) -> None:
        """report(return_what='hints') should return Python dict literal suitable for compatibility_hints.py"""
        client = Mock()
        client.features = FeatureSet()
        client.server_name = "Test Server"
        client.url = "https://example.com/caldav"
        checker = ServerQuirkChecker(client)
        checker._features_checked.copy_feature_set(
            {"create-calendar": {"support": "full"}, "delete-calendar": {"support": "unsupported"}},
            collapse=False,
        )

        result = checker.report(return_what="hints")

        assert isinstance(result, str)
        # Should be valid Python that evaluates to a dict
        parsed = eval(result)  # noqa: S307
        assert isinstance(parsed, dict)
        assert "create-calendar" in parsed
        assert parsed["create-calendar"] == {"support": "full"}

    def test_report_dict_no_error_placeholder(self) -> None:
        """report dict should not contain a TODO placeholder in 'error' field"""
        client = Mock()
        client.features = FeatureSet()
        client.server_name = "Test Server"
        client.url = "https://example.com/caldav"
        checker = ServerQuirkChecker(client)

        result = checker.report(return_what=dict)

        assert "TODO" not in str(result.get("error", ""))

    def test_report_str_nonverbose_hides_feature_matching_spec_default(self) -> None:
        """Non-verbose report should NOT show create-calendar.auto when observed as unsupported (spec default)"""
        client = Mock()
        client.features = FeatureSet()
        client.server_name = "Test Server"
        client.url = "https://example.com/caldav"
        checker = ServerQuirkChecker(client)
        # create-calendar.auto has default=unsupported in FEATURES
        checker._features_checked.copy_feature_set({"create-calendar.auto": {"support": "unsupported"}}, collapse=False)

        result = checker.report(return_what=str, verbose=False)

        # Should NOT appear since unsupported == spec default for create-calendar.auto
        assert "create-calendar.auto" not in result

    def test_report_str_nonverbose_shows_feature_deviating_from_spec_default(self) -> None:
        """Non-verbose report should show create-calendar when observed as unsupported (spec default is full)"""
        client = Mock()
        client.features = FeatureSet()
        client.server_name = "Test Server"
        client.url = "https://example.com/caldav"
        checker = ServerQuirkChecker(client)
        # create-calendar has default=full in FEATURES
        checker._features_checked.copy_feature_set({"create-calendar": {"support": "unsupported"}}, collapse=False)

        result = checker.report(return_what=str, verbose=False)

        assert "create-calendar" in result

    def test_report_str_nonverbose_shows_extra_feature_when_supported(self) -> None:
        """Non-verbose report should show create-calendar.auto when observed as full (spec default is unsupported)"""
        client = Mock()
        client.features = FeatureSet()
        client.server_name = "Test Server"
        client.url = "https://example.com/caldav"
        checker = ServerQuirkChecker(client)
        # create-calendar.auto has default=unsupported; if server supports it, that's noteworthy
        checker._features_checked.copy_feature_set({"create-calendar.auto": {"support": "full"}}, collapse=False)

        result = checker.report(return_what=str, verbose=False)

        assert "create-calendar.auto" in result

    def test_report_diff_shows_deviations(self) -> None:
        """report should be able to show diff between expected and observed features"""
        client = Mock()
        expected = FeatureSet()
        expected.copy_feature_set({"create-calendar": {"support": "full"}}, collapse=False)
        client.features = expected
        client.server_name = "Test Server"
        client.url = "https://example.com/caldav"
        checker = ServerQuirkChecker(client)
        # Observe something different from expected
        checker._features_checked.copy_feature_set({"create-calendar": {"support": "unsupported"}}, collapse=False)

        result = checker.report(return_what=str, show_diff=True)

        assert "create-calendar" in result
        assert "full" in result or "unsupported" in result

    def _checker_with(self, expected: dict, observed: dict) -> ServerQuirkChecker:
        client = Mock()
        expected_fs = FeatureSet()
        expected_fs.copy_feature_set(expected, collapse=False)
        client.features = expected_fs
        client.server_name = "Test Server"
        client.url = "https://example.com/caldav"
        checker = ServerQuirkChecker(client)
        checker._features_checked.copy_feature_set(observed, collapse=False)
        return checker

    def test_diff_ignores_matching_observation(self) -> None:
        """An observation carries a value, not a support level - a match is no diff"""
        checker = self._checker_with(
            {"get-current-user-principal.has-calendar": {"value": False}},
            {"get-current-user-principal.has-calendar": {"value": False}},
        )
        assert "get-current-user-principal.has-calendar" not in checker._compute_diff()

    def test_diff_skips_types_caldav_compare_skips(self) -> None:
        """--diff must agree with FeatureSet.compare(): no client-features or observations"""
        checker = self._checker_with(
            {"rate-limit": {"enable": True, "interval": 1}},
            {
                "rate-limit": {"enable": True, "interval": 1},
                "get-current-user-principal.has-calendar": {"value": False},
            },
        )
        diff = checker._compute_diff()
        assert "rate-limit" not in diff
        assert "get-current-user-principal.has-calendar" not in diff

    def test_diff_still_reports_server_feature(self) -> None:
        checker = self._checker_with(
            {"create-calendar": {"support": "full"}},
            {"create-calendar": {"support": "unsupported"}},
        )
        assert checker._compute_diff()["create-calendar"] == {"expected": "full", "observed": "unsupported"}

    def test_deviating_ignores_observation_at_default(self) -> None:
        """has-calendar observed True is the default - not a deviation"""
        checker = self._checker_with({}, {"get-current-user-principal.has-calendar": {"value": True}})
        assert "get-current-user-principal.has-calendar" not in checker._get_deviating_features()

    def test_deviating_shows_observation_off_default(self) -> None:
        checker = self._checker_with({}, {"get-current-user-principal.has-calendar": {"value": False}})
        assert "get-current-user-principal.has-calendar" in checker._get_deviating_features()


class TestServerQuirkCheckerCleanupSafety:
    """Test cleanup safety when PrepareCalendar hasn't run"""

    def test_cleanup_does_not_crash_without_calendar_attribute(self) -> None:
        """cleanup should not raise AttributeError if no calendar was set up"""
        client = Mock()
        client.features = FeatureSet()
        checker = ServerQuirkChecker(client)
        # Do NOT set checker.calendar / tasklist / journallist

        # Should not raise
        checker.cleanup(force=True)


class TestServerQuirkCheckerCleanup:
    """Test ServerQuirkChecker.cleanup method"""

    def test_cleanup_without_force_checks_expected_features(self) -> None:
        """cleanup(force=False) should check expected_features for cleanup setting"""
        client = Mock()
        features = FeatureSet()
        features.copy_feature_set({"test-calendar.compatibility-tests": {"cleanup": False}}, collapse=False)
        client.features = features
        checker = ServerQuirkChecker(client)
        checker.expected_features = features

        # Should return early without doing cleanup
        checker.cleanup(force=False)
        # If no exception, test passed

    def test_cleanup_with_force_attempts_deletion(self) -> None:
        """cleanup(force=True) should attempt to delete calendars if supported"""
        client = Mock()
        client.features = FeatureSet()
        checker = ServerQuirkChecker(client)

        # Mock the calendar, tasklist and journallist
        mock_calendar = Mock()
        mock_tasklist = Mock()
        checker.calendar = mock_calendar
        checker.tasklist = mock_tasklist
        checker.journallist = mock_calendar  # Same as calendar by default
        checker.calendar_was_created = True  # tool created the calendar -> safe to delete

        # Set features to indicate calendar creation/deletion is supported
        checker._features_checked.copy_feature_set(
            {
                "create-calendar": {"support": "full"},
                "delete-calendar": {"support": "full"},
            },
            collapse=False,
        )

        checker.cleanup(force=True)

        # Should have called delete on calendar
        mock_calendar.delete.assert_called_once()

    def test_cleanup_deletes_both_calendar_and_tasklist_when_different(self) -> None:
        """cleanup should delete both calendar and tasklist if they're different"""
        client = Mock()
        client.features = FeatureSet()
        checker = ServerQuirkChecker(client)

        mock_calendar = Mock()
        mock_tasklist = Mock()
        checker.calendar = mock_calendar
        checker.tasklist = mock_tasklist
        checker.journallist = mock_calendar  # Same as calendar
        checker.calendar_was_created = True  # tool created the calendar -> safe to delete

        checker._features_checked.copy_feature_set(
            {
                "create-calendar": {"support": "full"},
                "delete-calendar": {"support": "full"},
            },
            collapse=False,
        )

        checker.cleanup(force=True)

        mock_calendar.delete.assert_called_once()
        mock_tasklist.delete.assert_called_once()

    def test_cleanup_deletes_calendar_only_once_when_same_as_tasklist(self) -> None:
        """cleanup should only delete calendar once if it's the same as tasklist"""
        client = Mock()
        client.features = FeatureSet()
        checker = ServerQuirkChecker(client)

        mock_calendar = Mock()
        checker.calendar = mock_calendar
        checker.tasklist = mock_calendar  # Same object
        checker.journallist = mock_calendar  # Same object
        checker.calendar_was_created = True  # tool created the calendar -> safe to delete

        checker._features_checked.copy_feature_set(
            {
                "create-calendar": {"support": "full"},
                "delete-calendar": {"support": "full"},
            },
            collapse=False,
        )

        checker.cleanup(force=True)

        # Should only be called once since they're the same
        assert mock_calendar.delete.call_count == 1


class TestPurgeErrorReporting:
    """Finding #7: cleanup must not report success when listing/deletion failed."""

    def _checker(self) -> ServerQuirkChecker:
        client = Mock()
        client.features = FeatureSet()
        return ServerQuirkChecker(client, debug_mode=None)

    def test_listing_failure_is_counted(self) -> None:
        checker = self._checker()
        cal = Mock()
        cal.objects.side_effect = Exception("403 Forbidden")
        with patch("caldav.Event") as mock_event:
            mock_event.return_value.load.side_effect = Exception("probe absent")
            removed, errors = checker._purge_csc_objects([cal])
        assert removed == 0
        assert errors >= 1

    def test_delete_failure_is_counted(self) -> None:
        checker = self._checker()
        cal = Mock()
        obj = Mock()
        obj.icalendar_component.get.return_value = "csc_simple_event1"
        obj.delete.side_effect = Exception("500 Server Error")
        cal.objects.return_value = [obj]
        with patch("caldav.Event") as mock_event:
            mock_event.return_value.load.side_effect = Exception("probe absent")
            removed, errors = checker._purge_csc_objects([cal])
        assert removed == 0
        assert errors >= 1

    def test_clean_purge_reports_no_errors(self) -> None:
        checker = self._checker()
        cal = Mock()
        obj = Mock()
        obj.icalendar_component.get.return_value = "csc_simple_event1"
        cal.objects.return_value = [obj]
        with patch("caldav.Event") as mock_event:
            mock_event.return_value.load.side_effect = Exception("probe absent")
            removed, errors = checker._purge_csc_objects([cal])
        assert removed == 1
        assert errors == 0


class TestWriteDelay:
    """synchronous-write unsupported with a delay: sleep after every write request.

    Some servers (Infomaniak/SabreDAV) process writes asynchronously, so the
    checker must wait after each PUT/DELETE/MKCALENDAR/... before relying on the
    change being visible.  This is the general (write-side) counterpart of the
    search-cache delay.
    """

    def _client(self, delay=None):
        client = Mock()
        features = FeatureSet()
        if delay is not None:
            features.copy_feature_set({"synchronous-write": {"support": "unsupported", "delay": delay}}, collapse=False)
        client.features = features
        client.server_name = "Test Server"
        client.url = "https://example.com/caldav"
        ## A real request() captured before wrapping; the wrapper must call through.
        client.request = Mock(name="request", return_value="response")
        return client

    @patch("caldav_server_tester.checker.time.sleep")
    def test_sleeps_after_write_method(self, mock_sleep) -> None:
        client = self._client(delay=10)
        ServerQuirkChecker(client)
        result = client.request("https://example.com/caldav/cal/obj.ics", "PUT", "BEGIN:VCALENDAR...")
        assert result == "response"  ## the original return value is passed through
        mock_sleep.assert_called_once_with(10)

    @patch("caldav_server_tester.checker.time.sleep")
    def test_sleeps_after_every_write_verb(self, mock_sleep) -> None:
        client = self._client(delay=7)
        ServerQuirkChecker(client)
        for verb in ("PUT", "DELETE", "MKCALENDAR", "MKCOL", "PROPPATCH", "MOVE", "POST"):
            client.request("https://example.com/caldav/x", verb)
        assert mock_sleep.call_count == 7
        assert all(c.args == (7,) for c in mock_sleep.call_args_list)

    @patch("caldav_server_tester.checker.time.sleep")
    def test_does_not_sleep_after_read_method(self, mock_sleep) -> None:
        client = self._client(delay=10)
        ServerQuirkChecker(client)
        for verb in ("GET", "PROPFIND", "REPORT", "OPTIONS", "HEAD"):
            client.request("https://example.com/caldav/x", verb)
        mock_sleep.assert_not_called()

    @patch("caldav_server_tester.checker.time.sleep")
    def test_method_matching_is_case_insensitive(self, mock_sleep) -> None:
        client = self._client(delay=10)
        ServerQuirkChecker(client)
        client.request("https://example.com/caldav/x", "put")
        mock_sleep.assert_called_once_with(10)

    @patch("caldav_server_tester.checker.time.sleep")
    def test_no_delay_when_unconfigured(self, mock_sleep) -> None:
        client = self._client(delay=None)
        original = client.request
        ServerQuirkChecker(client)
        ## request must be left untouched when the server has no write delay
        assert client.request is original
        client.request("https://example.com/caldav/x", "PUT")
        mock_sleep.assert_not_called()

    @patch("caldav_server_tester.checker.time.sleep")
    def test_no_delay_when_writes_are_synchronous(self, mock_sleep) -> None:
        client = self._client()
        client.features.copy_feature_set({"synchronous-write": {"support": "full", "delay": 10}}, collapse=False)
        original = client.request
        ServerQuirkChecker(client)
        assert client.request is original
        client.request("https://example.com/caldav/x", "PUT")
        mock_sleep.assert_not_called()

    def test_construction_alone_claims_no_observation(self) -> None:
        """Reporting the delay is CheckSynchronousWrite's job, not the constructor's.

        The constructor installs the sleep, but recording "this server has a
        write delay" is an observation, and observations belong to the check
        that made one - CheckSynchronousWrite measures it, and falls back to
        reporting the configured value where it cannot (see
        tests/test_synchronous_write_probe.py).  Recording it here would also make
        the feature look already-checked to the machinery that verifies every
        declared feature was probed.
        """
        client = self._client(delay=10)
        checker = ServerQuirkChecker(client)
        assert "synchronous-write" not in checker.features_checked.dotted_feature_set_list()
        ## ... but the sleep is installed all the same
        assert client._write_delay == 10

    @patch("caldav_server_tester.checker.time.sleep")
    def test_also_wraps_extra_clients(self, mock_sleep) -> None:
        main = self._client(delay=10)
        extra = self._client(delay=10)
        ServerQuirkChecker(main, extra_clients=[extra])
        extra.request("https://example.com/caldav/x", "PUT")
        mock_sleep.assert_called_once_with(10)

    @patch("caldav_server_tester.checker.time.sleep")
    def test_does_not_double_wrap_shared_client(self, mock_sleep) -> None:
        ## A client reused across two checkers must sleep ONCE per write, not twice.
        client = self._client(delay=10)
        ServerQuirkChecker(client)
        ServerQuirkChecker(client)
        client.request("https://example.com/caldav/x", "PUT")
        mock_sleep.assert_called_once_with(10)


class TestReportDoesNotCorruptFeatureData:
    """Finding #10: compact rendering must not strip per-child data from the
    lossless (hints / verbose) report branches."""

    def _checker(self) -> ServerQuirkChecker:
        client = Mock()
        client.features = FeatureSet()
        client.server_name = "X"
        client.url = "https://x/dav/"
        checker = ServerQuirkChecker(client)
        # ALL sibling children present and equal-support -> they collapse to the
        # parent, but carry differing behaviour notes that must not be lost.
        checker._features_checked.set_feature(
            "search.recurrences.expanded.event", {"support": "unsupported", "behaviour": "note-A"}
        )
        checker._features_checked.set_feature(
            "search.recurrences.expanded.todo", {"support": "unsupported", "behaviour": "note-B"}
        )
        checker._features_checked.set_feature(
            "search.recurrences.expanded.exception", {"support": "unsupported", "behaviour": "note-C"}
        )
        return checker

    def test_hints_keeps_per_child_behaviour_notes(self) -> None:
        checker = self._checker()
        hints = checker.report(return_what="hints")
        assert "note-A" in hints
        assert "note-B" in hints

    def test_verbose_text_keeps_per_child_behaviour_notes(self) -> None:
        checker = self._checker()
        text = checker.report(verbose=True, return_what=str)
        assert "note-A" in text
        assert "note-B" in text


class TestPurgeProbeCalendars:
    """The cleanup sweep must remove leftover throwaway probe calendars (so
    repeated runs cannot accumulate calendars on quota-limited servers) without
    touching the user's real calendars."""

    ## The free-namespace probe names its calendars testcalendar-<uuid4>
    ## (checks.py), and the sweep requires that shape rather than the bare
    ## prefix so a human's "testcalendar-2019" is not deleted.  Fixtures here
    ## therefore have to use real uuid-shaped ids.
    @staticmethod
    def _cal(url: str, name: str | None = None) -> Mock:
        cal = Mock()
        cal.url = url
        cal.get_display_name.return_value = name
        return cal

    def _checker(self) -> ServerQuirkChecker:
        return ServerQuirkChecker(Mock())

    def test_purges_probe_calendars_only(self) -> None:
        checker = self._checker()
        real = self._cal("https://h/cal/61f72804-uuid/", name="Tobias Brox")
        probes = [
            self._cal("https://h/cal/caldav-server-checker-calendar/", "Calendar for checking"),
            self._cal("https://h/cal/caldav-server-checker-mkdel-test/"),
            self._cal("https://h/cal/caldav-server-checker-displayname-test/"),
            self._cal("https://h/cal/testcalendar-2f1c8d3e-4b5a-4c6d-8e7f-0a1b2c3d4e5f/"),
            self._cal("https://h/cal/csc-inbox-delivery-probe/", "csc-inbox-delivery-probe"),
            # created with name only (no cal_id) -> server-assigned URL, matched by name
            self._cal("https://h/cal/server-assigned-uuid/", name="csc_duplicate_uid_cal2"),
        ]
        checker.principal = Mock()
        checker.principal.calendars.return_value = [real, *probes]
        checker.extra_principals = []

        removed = checker._purge_probe_calendars()

        assert removed == len(probes)
        real.delete.assert_not_called()
        for p in probes:
            p.delete.assert_called_once()

    def test_sweeps_extra_principals_too(self) -> None:
        checker = self._checker()
        checker.principal = Mock()
        checker.principal.calendars.return_value = []
        attendee_probe = self._cal("https://h/cal/csc-inbox-probe-attendee-cal/", "csc-inbox-probe-attendee-cal")
        extra = Mock()
        extra.calendars.return_value = [attendee_probe]
        checker.extra_principals = [extra]

        removed = checker._purge_probe_calendars()

        assert removed == 1
        attendee_probe.delete.assert_called_once()

    def test_delete_failure_does_not_abort_sweep(self) -> None:
        checker = self._checker()
        bad = self._cal("https://h/cal/testcalendar-11111111-2222-4333-8444-555555555555/")
        bad.delete.side_effect = Exception("500")
        good = self._cal("https://h/cal/testcalendar-66666666-7777-4888-8999-aaaaaaaaaaaa/")
        checker.principal = Mock()
        checker.principal.calendars.return_value = [bad, good]
        checker.extra_principals = []

        removed = checker._purge_probe_calendars()

        assert removed == 1
        good.delete.assert_called_once()


class TestWriteDelaySuspension:
    """A probe that measures the server's own delay must not be smoothed over.

    Every other check wants the configured write delay honoured, so a
    read-back sees settled data.  The probes that exist to *measure* the delay
    want the opposite: with the sleep in place the calendar is always there by
    the time the poll runs, the observed delay is 0, and the checker reports
    "full" for the two features the delay was configured because of.
    """

    def _client(self, delay=None):
        client = Mock()
        features = FeatureSet()
        if delay is not None:
            features.copy_feature_set({"synchronous-write": {"support": "unsupported", "delay": delay}}, collapse=False)
        client.features = features
        client.server_name = "Test Server"
        client.url = "https://example.com/caldav"
        client.request = Mock(name="request", return_value="response")
        return client

    @patch("caldav_server_tester.checker.time.sleep")
    def test_suspends_and_restores_the_delay(self, mock_sleep) -> None:
        client = self._client(delay=10)
        checker = ServerQuirkChecker(client)

        with checker.without_write_delay():
            client.request("https://example.com/caldav/x", "PUT")
            mock_sleep.assert_not_called()

        client.request("https://example.com/caldav/x", "PUT")
        mock_sleep.assert_called_once_with(10)

    @patch("caldav_server_tester.checker.time.sleep")
    def test_restores_the_delay_after_an_exception(self, mock_sleep) -> None:
        client = self._client(delay=10)
        checker = ServerQuirkChecker(client)

        with pytest.raises(ValueError):
            with checker.without_write_delay():
                raise ValueError("a probe blew up")

        client.request("https://example.com/caldav/x", "PUT")
        mock_sleep.assert_called_once_with(10)

    @patch("caldav_server_tester.checker.time.sleep")
    def test_suspends_extra_clients_too(self, mock_sleep) -> None:
        client = self._client(delay=10)
        extra = self._client(delay=10)
        checker = ServerQuirkChecker(client, extra_clients=[extra])

        with checker.without_write_delay():
            extra.request("https://example.com/caldav/x", "PUT")
            mock_sleep.assert_not_called()

        extra.request("https://example.com/caldav/x", "PUT")
        mock_sleep.assert_called_once_with(10)

    def test_is_a_no_op_without_a_configured_delay(self) -> None:
        client = self._client(delay=None)
        checker = ServerQuirkChecker(client)
        assert checker.configured_write_delay == 0
        with checker.without_write_delay():
            pass

    def test_configured_delay_is_readable(self) -> None:
        checker = ServerQuirkChecker(self._client(delay=16))
        assert checker.configured_write_delay == 16

    def test_poll_timeout_covers_the_configured_delay(self) -> None:
        """A 10s poll cannot observe a 16s delay.

        With the delay suspended the probe sees the server's raw behaviour, so
        the poll has to be willing to wait longer than the delay someone
        configured - otherwise suspending it turns Infomaniak from "delayed
        creation" into "creation does not work".
        """
        assert ServerQuirkChecker(self._client(delay=16)).delay_probe_timeout >= 16
        assert ServerQuirkChecker(self._client(delay=None)).delay_probe_timeout == 10


class TestSearchCacheDelay:
    """search-cache with a delay: sleep only before a search the server's
    index answers.

    Purelymail lags ~25 s behind a write in a time-range or text search, but
    lists a calendar (events(), todos(), children) at once (probed
    2026-10-14).  Sleeping before every search made a full run take hours,
    since the calendar-creation probes poll with events().
    """

    def _client(self, delay=None, filtered_only=True):
        client = Mock()
        features = FeatureSet()
        if delay is not None:
            config = {"behaviour": "delay", "delay": delay}
            if filtered_only:
                config["filtered-only"] = True
            features.copy_feature_set({"search-cache": config}, collapse=False)
        client.features = features
        client.server_name = "Test Server"
        client.url = "https://example.com/caldav"
        client.request = Mock(name="request", return_value="response")
        return client

    def _search(self, searcher, *args, **kwargs):
        from caldav.search import CalDAVSearcher

        with patch.object(CalDAVSearcher, "_csc_undelayed_search", Mock(return_value=[])) as undelayed:
            searcher.search(Mock(), *args, **kwargs)
        return undelayed

    @pytest.fixture(autouse=True)
    def _reset_delay(self):
        ## A frozen clock: no time passes unless a test says so
        with patch("caldav_server_tester.checker.time.monotonic", return_value=1000.0):
            yield
        ## The wrapper is class-level; leave no delay behind for other tests
        ServerQuirkChecker(self._client())

    @patch("caldav_server_tester.checker.time.sleep")
    def test_plain_listing_does_not_sleep(self, mock_sleep) -> None:
        from caldav.calendarobjectresource import Event
        from caldav.search import CalDAVSearcher

        ServerQuirkChecker(self._client(delay=180))
        self._search(CalDAVSearcher(comp_class=Event))
        self._search(CalDAVSearcher(todo=True))
        mock_sleep.assert_not_called()

    @patch("caldav_server_tester.checker.time.sleep")
    def test_pending_todos_sleep(self, mock_sleep) -> None:
        """include_completed=False filters on STATUS in nested searches whose
        filters go into the XML, unseen by the wrapper: it counts itself."""
        from caldav.search import CalDAVSearcher

        ServerQuirkChecker(self._client(delay=180))
        self._search(CalDAVSearcher(todo=True, include_completed=False))
        mock_sleep.assert_called_once_with(180)

    @patch("caldav_server_tester.checker.time.sleep")
    def test_cleanup_ends_the_delay(self, mock_sleep) -> None:
        """The wrap is class-level: once a checker is done, later searches in
        the same process (caldav's own tests) must not inherit its delay."""
        from caldav.search import CalDAVSearcher

        checker = ServerQuirkChecker(self._client(delay=180))
        with patch.object(ServerQuirkChecker, "_cleanup", return_value=None):
            checker.cleanup()
        self._search(CalDAVSearcher(event=True, start=datetime(2027, 3, 1, tzinfo=timezone.utc)))
        mock_sleep.assert_not_called()

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"start": datetime(2027, 3, 1, tzinfo=timezone.utc)},
            {"end": datetime(2027, 3, 2, tzinfo=timezone.utc)},
            {"alarm_start": datetime(2027, 3, 1, tzinfo=timezone.utc)},
        ],
    )
    @patch("caldav_server_tester.checker.time.sleep")
    def test_time_range_sleeps(self, mock_sleep, kwargs) -> None:
        from caldav.search import CalDAVSearcher

        ServerQuirkChecker(self._client(delay=180))
        self._search(CalDAVSearcher(event=True, **kwargs))
        mock_sleep.assert_called_once_with(180)

    @patch("caldav_server_tester.checker.time.sleep")
    def test_property_filter_sleeps(self, mock_sleep) -> None:
        from caldav.search import CalDAVSearcher

        ServerQuirkChecker(self._client(delay=180))
        searcher = CalDAVSearcher(event=True)
        searcher.add_property_filter("SUMMARY", "meeting")
        self._search(searcher)
        mock_sleep.assert_called_once_with(180)

    @patch("caldav_server_tester.checker.time.sleep")
    def test_is_not_defined_sleeps(self, mock_sleep) -> None:
        """no_category=True and friends keep only an operator, no value."""
        from caldav.search import CalDAVSearcher

        ServerQuirkChecker(self._client(delay=180))
        searcher = CalDAVSearcher(event=True)
        searcher.add_property_filter("categories", True, operator="undef")
        self._search(searcher)
        mock_sleep.assert_called_once_with(180)

    @patch("caldav_server_tester.checker.time.sleep")
    def test_filter_added_in_a_nested_search_sleeps(self, mock_sleep) -> None:
        """todos() recurses into STATUS-filtered searches, the unlimited-range
        workaround into a time-range one: the inner search is judged too."""
        from caldav.search import CalDAVSearcher

        ServerQuirkChecker(self._client(delay=180))

        def nested(searcher, *args, **kwargs):
            if not searcher.start:
                CalDAVSearcher.search(
                    CalDAVSearcher(event=True, start=datetime(2027, 3, 1, tzinfo=timezone.utc)), Mock()
                )
            return []

        with patch.object(CalDAVSearcher, "_csc_undelayed_search", side_effect=nested, autospec=True):
            CalDAVSearcher(event=True).search(Mock())
        mock_sleep.assert_called_once_with(180)

    @patch("caldav_server_tester.checker.time.sleep")
    def test_raw_xml_sleeps(self, mock_sleep) -> None:
        from caldav.search import CalDAVSearcher

        ServerQuirkChecker(self._client(delay=180))
        self._search(CalDAVSearcher(), xml="<C:calendar-query/>")
        mock_sleep.assert_called_once_with(180)

    @patch("caldav_server_tester.checker.time.sleep")
    def test_nested_search_sleeps_once(self, mock_sleep) -> None:
        """A search may recurse (e.g. once per component type): one sleep in all."""
        from caldav.search import CalDAVSearcher

        ServerQuirkChecker(self._client(delay=180))

        def nested(searcher, *args, **kwargs):
            if searcher.event:
                CalDAVSearcher.search(CalDAVSearcher(todo=True, start=searcher.start), Mock())
            return []

        outer = CalDAVSearcher(event=True, start=datetime(2027, 3, 1, tzinfo=timezone.utc))
        with patch.object(CalDAVSearcher, "_csc_undelayed_search", side_effect=nested, autospec=True) as undelayed:
            outer.search(Mock())
        assert undelayed.call_count == 2
        mock_sleep.assert_called_once_with(180)

    @patch("caldav_server_tester.checker.time.sleep")
    def test_plain_listing_sleeps_without_filtered_only(self, mock_sleep) -> None:
        """Bedework 3 lags in plain listings too: without the key every
        search waits, though still only for the rest of the delay."""
        from caldav.calendarobjectresource import Event
        from caldav.search import CalDAVSearcher

        ServerQuirkChecker(self._client(delay=180, filtered_only=False))
        self._search(CalDAVSearcher(comp_class=Event))
        mock_sleep.assert_called_once_with(180)

    @patch("caldav_server_tester.checker.time.sleep")
    def test_no_delay_configured(self, mock_sleep) -> None:
        from caldav.search import CalDAVSearcher

        ServerQuirkChecker(self._client(delay=180))
        ServerQuirkChecker(self._client())
        self._search(CalDAVSearcher(event=True, start=datetime(2027, 3, 1, tzinfo=timezone.utc)))
        mock_sleep.assert_not_called()

    @patch("caldav_server_tester.checker.time.sleep")
    def test_sleeps_only_the_rest_of_the_delay(self, mock_sleep) -> None:
        """The index lags behind the last write: a search 100 s after it
        waits the remaining 80 s, one 200 s after it not at all."""
        from caldav.search import CalDAVSearcher

        client = self._client(delay=180)
        ServerQuirkChecker(client)
        client.request("https://example.com/caldav/cal/x.ics", "PUT")
        searcher = CalDAVSearcher(event=True, start=datetime(2027, 3, 1, tzinfo=timezone.utc))
        with patch("caldav_server_tester.checker.time.monotonic", return_value=1100.0):
            self._search(searcher)
        mock_sleep.assert_called_once_with(80.0)
        mock_sleep.reset_mock()
        with patch("caldav_server_tester.checker.time.monotonic", return_value=1200.0):
            self._search(searcher)
        mock_sleep.assert_not_called()

    @patch("caldav_server_tester.checker.time.sleep")
    def test_a_read_does_not_restart_the_clock(self, mock_sleep) -> None:
        from caldav.search import CalDAVSearcher

        client = self._client(delay=180)
        ServerQuirkChecker(client)
        with patch("caldav_server_tester.checker.time.monotonic", return_value=1500.0):
            client.request("https://example.com/caldav/cal/x.ics", "GET")
            client.request("https://example.com/caldav/cal/", "REPORT")
            self._search(CalDAVSearcher(event=True, start=datetime(2027, 3, 1, tzinfo=timezone.utc)))
        mock_sleep.assert_not_called()
