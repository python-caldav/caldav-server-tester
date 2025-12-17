"""Unit tests for CheckSyncToken to catch API usage errors"""

from unittest.mock import Mock

from caldav.compatibility_hints import FeatureSet

from caldav_server_tester.checks import CheckSyncToken


class TestCheckSyncTokenAPI:
    """Test that CheckSyncToken uses the correct caldav API"""

    def create_mock_calendar(self) -> Mock:
        """Helper to create a mock calendar object"""
        cal = Mock()

        # Mock objects() to return a sync token
        mock_objects = Mock()
        mock_objects.sync_token = "test-token-1"
        cal.objects.return_value = mock_objects

        # Mock save_object to return an event
        mock_event = Mock()
        mock_event.icalendar_instance = Mock()
        mock_event.icalendar_instance.subcomponents = [{"SUMMARY": "Test"}]
        cal.save_object.return_value = mock_event

        # Mock objects_by_sync_token
        mock_changed = Mock()
        mock_changed.sync_token = "test-token-2"
        mock_changed.__iter__ = Mock(return_value=iter([]))
        mock_changed.__len__ = Mock(return_value=0)
        cal.objects_by_sync_token.return_value = mock_changed

        return cal

    def create_mock_checker(self) -> Mock:
        """Helper to create a mock checker object"""
        checker = Mock()
        checker._features_checked = FeatureSet()
        checker.features_checked = checker._features_checked
        checker.debug_mode = None
        checker._client_obj = Mock()
        checker._client_obj.features = FeatureSet()
        checker.expected_features = FeatureSet()
        checker.calendar = self.create_mock_calendar()
        return checker

    def test_uses_save_object_not_save_event(self) -> None:
        """CheckSyncToken should use cal.save_object() not cal.save_event()"""
        checker = self.create_mock_checker()
        check = CheckSyncToken(checker)

        # Run the check
        check._run_check()

        # Verify save_object was called (not save_event)
        assert checker.calendar.save_object.called

        # Verify it was called with Event as first parameter
        call_args = checker.calendar.save_object.call_args
        from caldav.calendarobjectresource import Event
        assert call_args[0][0] == Event

    def test_sync_token_unsupported_exits_early(self) -> None:
        """If sync tokens aren't supported, check should exit early"""
        checker = self.create_mock_checker()

        # Mock no sync token support
        mock_objects = Mock()
        mock_objects.sync_token = ""
        checker.calendar.objects.return_value = mock_objects

        check = CheckSyncToken(checker)
        check._run_check()

        # Should set sync-token to unsupported
        result = checker._features_checked.is_supported("sync-token", return_type=bool)
        assert result is False

        # Should not try to create test events
        assert not checker.calendar.save_object.called

    def test_handles_sync_token_exception(self) -> None:
        """If objects() raises exception, should mark as unsupported"""
        checker = self.create_mock_checker()

        # Mock exception on objects()
        from caldav.lib.error import ReportError
        checker.calendar.objects.side_effect = ReportError()

        check = CheckSyncToken(checker)
        check._run_check()

        # Should set sync-token to unsupported
        result = checker._features_checked.is_supported("sync-token", return_type=bool)
        assert result is False

    def test_detects_time_based_tokens(self) -> None:
        """Should detect time-based tokens when changes aren't seen immediately"""
        checker = self.create_mock_checker()

        # First sync: no changes immediately
        empty_result = Mock()
        empty_result.__iter__ = Mock(return_value=iter([]))
        empty_result.__len__ = Mock(return_value=0)
        empty_result.sync_token = "test-token-2"

        # After sleep: changes appear
        changed_result = Mock()
        changed_result.__iter__ = Mock(return_value=iter([Mock()]))
        changed_result.__len__ = Mock(return_value=1)
        changed_result.sync_token = "test-token-3"

        # Mock delete test
        delete_result = Mock()
        delete_result.__iter__ = Mock(return_value=iter([]))
        delete_result.__len__ = Mock(return_value=0)
        delete_result.sync_token = "test-token-4"

        checker.calendar.objects_by_sync_token.side_effect = [
            empty_result,  # Immediate check after creating
            empty_result,  # First modification (no sleep)
            changed_result,  # After sleep
            empty_result,  # After second modification
            delete_result,  # After deletion
        ]

        check = CheckSyncToken(checker)
        check._run_check()

        # Should detect time-based behaviour
        result = checker._features_checked.is_supported("sync-token", return_type=dict)
        assert result is not None
        assert result.get("behaviour") == "time-based"

    def test_detects_fragile_tokens(self) -> None:
        """Should detect fragile tokens when extra content appears"""
        checker = self.create_mock_checker()

        # Immediately after getting token, return content (shouldn't happen)
        fragile_result = Mock()
        fragile_result.__iter__ = Mock(return_value=iter([Mock()]))
        fragile_result.__len__ = Mock(return_value=1)
        fragile_result.sync_token = "test-token-2"

        # After modification, return content (correct behaviour when modified)
        modified_result = Mock()
        modified_result.__iter__ = Mock(return_value=iter([Mock()]))
        modified_result.__len__ = Mock(return_value=1)
        modified_result.sync_token = "test-token-3"

        # After deletion test
        delete_result = Mock()
        delete_result.__iter__ = Mock(return_value=iter([]))
        delete_result.__len__ = Mock(return_value=0)
        delete_result.sync_token = "test-token-4"

        checker.calendar.objects_by_sync_token.side_effect = [
            fragile_result,  # Immediate check shows content (fragile!)
            modified_result,  # Modification check (shows the change)
            delete_result,  # After deletion
        ]

        check = CheckSyncToken(checker)
        check._run_check()

        # Should detect fragile support
        result = checker._features_checked.is_supported("sync-token", return_type=dict)
        assert result is not None
        assert result.get("support") == "fragile"
