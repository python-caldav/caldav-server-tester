"""Unit tests for CheckMutable"""

from unittest.mock import MagicMock, Mock, patch

from caldav.compatibility_hints import FeatureSet
from caldav.lib.error import AuthorizationError, DAVError, NotFoundError

from caldav_server_tester.checks import CheckMutable


def _make_checker(event_summary: str = "original summary") -> tuple[Mock, Mock]:
    """Return (checker, mock_event) with a fully wired mock calendar."""
    mock_vevent = MagicMock()
    mock_vevent.get.return_value = event_summary

    mock_ical = Mock()
    mock_ical.walk.return_value = [mock_vevent]

    mock_event = Mock()
    mock_event.icalendar_instance = mock_ical

    mock_calendar = Mock()
    mock_calendar.event_by_uid.return_value = mock_event
    mock_calendar.url = Mock()
    mock_calendar.url.join.return_value = "http://example.com/cal/csc_simple_event1.ics"

    checker = Mock()
    checker._features_checked = FeatureSet()
    checker.features_checked = checker._features_checked
    checker.debug_mode = None
    checker._client_obj = Mock()
    checker._client_obj.features = FeatureSet()
    checker.expected_features = FeatureSet()
    checker.calendar = mock_calendar

    return checker, mock_event


class TestCheckMutable:
    def test_successful_modification_sets_feature_supported(self) -> None:
        """When save() works and reload shows the new summary, feature is set to full."""
        checker, mock_event = _make_checker("original")

        def reload_side_effect():
            # after first save(), simulate that subsequent walk() returns modified summary
            pass

        mock_event.load.return_value = None
        mock_event.save.return_value = None

        vevent = mock_event.icalendar_instance.walk("vevent")[0]
        # First get() returns original; after save() + load() simulate updated value
        call_count = [0]
        def get_side_effect(key, default=""):
            call_count[0] += 1
            if call_count[0] <= 1:
                return "original"
            return "original (mutable-check)"
        vevent.get.side_effect = get_side_effect

        check = CheckMutable(checker)
        check._run_check()

        assert checker._features_checked.is_supported("save-load.mutable")

    def test_dav_error_on_save_sets_unsupported(self) -> None:
        """When save() raises DAVError, feature is marked unsupported."""
        checker, mock_event = _make_checker()

        mock_event.load.return_value = None
        mock_event.save.side_effect = DAVError("403 Forbidden")
        mock_event.icalendar_instance.walk("vevent")[0].get.return_value = "original"

        check = CheckMutable(checker)
        check._run_check()

        result = checker._features_checked.is_supported("save-load.mutable", return_type=dict)
        assert result.get("support") == "unsupported"

    def test_auth_error_on_save_sets_unsupported(self) -> None:
        """When save() raises AuthorizationError, feature is marked unsupported."""
        checker, mock_event = _make_checker()

        mock_event.load.return_value = None
        mock_event.save.side_effect = AuthorizationError()
        mock_event.icalendar_instance.walk("vevent")[0].get.return_value = "original"

        check = CheckMutable(checker)
        check._run_check()

        result = checker._features_checked.is_supported("save-load.mutable", return_type=dict)
        assert result.get("support") == "unsupported"

    def test_modification_not_reflected_sets_broken(self) -> None:
        """When reload shows the old summary despite a successful save(), feature is broken."""
        checker, mock_event = _make_checker()

        mock_event.load.return_value = None
        mock_event.save.return_value = None
        # Always returns the original summary - modification is not stored
        mock_event.icalendar_instance.walk("vevent")[0].get.return_value = "original"

        check = CheckMutable(checker)
        check._run_check()

        result = checker._features_checked.is_supported("save-load.mutable", return_type=dict)
        assert result.get("support") == "broken"

    def test_falls_back_to_url_on_not_found(self) -> None:
        """When event_by_uid raises NotFoundError, load is retried via direct URL."""
        checker, mock_event = _make_checker()

        checker.calendar.event_by_uid.side_effect = NotFoundError()

        # The Event constructor path uses cal.client and cal.url.join
        mock_event.load.return_value = None
        mock_event.save.return_value = None

        call_count = [0]
        def get_side_effect(key, default=""):
            call_count[0] += 1
            if call_count[0] <= 1:
                return "original"
            return "original (mutable-check)"
        mock_event.icalendar_instance.walk("vevent")[0].get.side_effect = get_side_effect

        with patch("caldav_server_tester.checks.Event") as MockEvent:
            MockEvent.return_value = mock_event
            check = CheckMutable(checker)
            check._run_check()

        MockEvent.assert_called_once()
