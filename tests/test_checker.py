"""Unit tests for the ServerQuirkChecker class"""

## DISCLAIMER: those tests are AI-generated, gone through a very quick human QA

import json
import time
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
        checker._features_checked.copyFeatureSet({"create-calendar": {"support": "full"}}, collapse=False)

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
        checker._features_checked.copyFeatureSet({"create-calendar": {"support": "full"}}, collapse=False)

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
        checker._features_checked.copyFeatureSet({"create-calendar": {"support": "unsupported"}}, collapse=False)

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
        checker._features_checked.copyFeatureSet(
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
        checker._features_checked.copyFeatureSet({"create-calendar.auto": {"support": "unsupported"}}, collapse=False)

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
        checker._features_checked.copyFeatureSet({"create-calendar": {"support": "unsupported"}}, collapse=False)

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
        checker._features_checked.copyFeatureSet({"create-calendar.auto": {"support": "full"}}, collapse=False)

        result = checker.report(return_what=str, verbose=False)

        assert "create-calendar.auto" in result

    def test_report_diff_shows_deviations(self) -> None:
        """report should be able to show diff between expected and observed features"""
        client = Mock()
        expected = FeatureSet()
        expected.copyFeatureSet({"create-calendar": {"support": "full"}}, collapse=False)
        client.features = expected
        client.server_name = "Test Server"
        client.url = "https://example.com/caldav"
        checker = ServerQuirkChecker(client)
        # Observe something different from expected
        checker._features_checked.copyFeatureSet({"create-calendar": {"support": "unsupported"}}, collapse=False)

        result = checker.report(return_what=str, show_diff=True)

        assert "create-calendar" in result
        assert "full" in result or "unsupported" in result


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
        features.copyFeatureSet({"test-calendar.compatibility-tests": {"cleanup": False}}, collapse=False)
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

        # Set features to indicate calendar creation/deletion is supported
        checker._features_checked.copyFeatureSet(
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

        checker._features_checked.copyFeatureSet(
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

        checker._features_checked.copyFeatureSet(
            {
                "create-calendar": {"support": "full"},
                "delete-calendar": {"support": "full"},
            },
            collapse=False,
        )

        checker.cleanup(force=True)

        # Should only be called once since they're the same
        assert mock_calendar.delete.call_count == 1
