"""Unit tests for CheckSyncToken to catch API usage errors"""

from unittest.mock import Mock

import pytest
from caldav.compatibility_hints import FeatureSet
from caldav.davclient import DAVClient
from caldav.elements import dav
from caldav.lib.error import AuthorizationError, ReportError

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
        checker._client_obj.report.side_effect = _refuse_token
        checker._client_obj._build_sync_collection_body = DAVClient._build_sync_collection_body
        checker._client_obj.features = FeatureSet()
        checker.expected_features = FeatureSet()
        checker.calendar = self.create_mock_calendar()
        checker.fixture_base_year = 2027
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


def _refuse_token(*args, **kwargs):
    """A REPORT for a never-issued token, refused as RFC 6578 section 3.2 says."""
    raise AuthorizationError(reason="Forbidden", preconditions=[dav.ValidSyncToken.tag])


class _SyncResult:
    """What ``objects_by_sync_token`` hands back: a token and some changes."""

    def __init__(self, token, changes=0):
        self.sync_token = token
        self._changes = [Mock() for _ in range(changes)]

    def __iter__(self):
        return iter(self._changes)


class TestSyncTokenDeleteGrading:
    """`sync-token.delete` asks whether a deletion is *reported* by the sync.

    Three outcomes, three gradings.  The one that matters is the middle one: a
    server that answers the post-delete REPORT with no changes at all has
    silently dropped the deletion, and a client syncing incrementally never
    learns the object is gone.  That is what 'unsupported' means in this
    vocabulary, and it used to be reported as full support because the probe
    only looked at whether the call raised.
    """

    def _checker(self, final):
        """A checker whose post-delete sync behaves as `final` says.

        The two earlier sync calls are scripted so the probe walks straight past
        the fragile- and time-based-token tests to the deletion one.
        """
        cal = Mock()
        cal.objects.return_value = _SyncResult("token-1")
        cal.save_object.return_value = Mock(
            icalendar_instance=Mock(subcomponents=[{"SUMMARY": "Test"}]),
        )
        cal.objects_by_sync_token.side_effect = [
            _SyncResult("token-1", changes=0),  ## nothing changed yet: not fragile
            _SyncResult("token-2", changes=1),  ## the edit shows up: not time-based
            final,  ## and this is the one under test
        ]

        checker = Mock()
        checker._features_checked = FeatureSet()
        checker.features_checked = checker._features_checked
        checker.debug_mode = None
        checker._client_obj = Mock()
        checker._client_obj.report.side_effect = _refuse_token
        checker._client_obj._build_sync_collection_body = DAVClient._build_sync_collection_body
        checker._client_obj.features = FeatureSet()
        checker.expected_features = FeatureSet()
        checker.calendar = cal
        checker.fixture_base_year = 2027
        return checker

    def _run(self, final):
        checker = self._checker(final)
        CheckSyncToken(checker)._run_check()
        return checker._features_checked.is_supported("sync-token.delete", str)

    def test_a_reported_deletion_is_full(self) -> None:
        assert self._run(_SyncResult("token-3", changes=1)) == "full"

    def test_a_deletion_the_sync_never_mentions_is_unsupported(self) -> None:
        """No error, no change reported - the deletion vanished silently."""
        assert self._run(_SyncResult("token-3", changes=0)) == "unsupported"

    def test_a_sync_that_raises_after_a_delete_is_ungraceful(self) -> None:
        """Infomaniak answers 418 for the removed member; the client can catch it."""
        assert self._run(ReportError("418 I'm a teapot")) == "ungraceful"


class TestSyncTokenInvalidTokenGrading:
    """`sync-token.invalid-token`: how a token the server never issued is refused.

    RFC 6578 section 3.2 wants DAV:valid-sync-token, and RFC 4918 section 16
    a 403 with it.  The library only reads the precondition from a 401/403,
    so another status carrying it is a quirk.  An error without it is
    ungraceful (Xandikos and CCS answer 500), and no error at all is broken:
    Stalwart returns a full listing, which a client can't tell from a report.
    """

    def _run(self, *answers):
        """Grade the server whose REPORTs for the two tokens get `answers`.

        An answer is an exception to raise, or a (status, body) pair.
        """
        checker = TestSyncTokenDeleteGrading()._checker(_SyncResult("token-3", changes=1))
        side_effects = []
        for answer in answers:
            if isinstance(answer, Exception):
                side_effects.append(answer)
            else:
                status, body = answer
                side_effects.append(Mock(status=status, raw=body))
        checker._client_obj.report.side_effect = side_effects
        CheckSyncToken(checker)._run_check()
        assert checker._client_obj.report.call_count == len(answers)
        return checker._features_checked.is_supported("sync-token.invalid-token", dict)

    _refused = AuthorizationError(reason="Forbidden", preconditions=[dav.ValidSyncToken.tag])
    _precondition_body = '<D:error xmlns:D="DAV:"><D:valid-sync-token/></D:error>'

    def test_a_403_with_valid_sync_token_is_full(self) -> None:
        assert self._run(self._refused, self._refused)["support"] == "full"

    def test_another_status_with_valid_sync_token_is_a_quirk(self) -> None:
        result = self._run(self._refused, (412, self._precondition_body))
        assert result["support"] == "quirk"
        assert "412 with valid-sync-token" in result["behaviour"]

    def test_an_error_without_valid_sync_token_is_ungraceful(self) -> None:
        """Xandikos: 500 for a malformed token, 412 with the precondition for a hex one."""
        result = self._run((500, "Server got itself in trouble"), (412, self._precondition_body))
        assert result["support"] == "ungraceful"
        assert "500 without valid-sync-token" in result["behaviour"]

    def test_a_403_without_valid_sync_token_is_ungraceful(self) -> None:
        refused = AuthorizationError(reason="Forbidden")
        assert self._run(refused, self._refused)["support"] == "ungraceful"

    def test_a_report_that_succeeds_is_broken(self) -> None:
        """Stalwart answers a never-issued token with a full listing."""
        result = self._run((207, "<D:multistatus xmlns:D='DAV:'/>"), (500, ""))
        assert result["support"] == "broken"

    def test_the_tokens_were_never_issued(self) -> None:
        checker = TestSyncTokenDeleteGrading()._checker(_SyncResult("token-3", changes=1))
        checker._client_obj.report.side_effect = _refuse_token
        checker._client_obj._build_sync_collection_body = DAVClient._build_sync_collection_body
        CheckSyncToken(checker)._run_check()
        for call in checker._client_obj.report.call_args_list:
            assert b"token-" not in call.args[1]

    def test_an_error_other_than_401_403_is_ungraceful(self) -> None:
        assert self._run(ReportError("500"), self._refused)["support"] == "ungraceful"

    def test_a_crashing_probe_keeps_the_other_results(self) -> None:
        """The probe runs last, so a crash in it can't cost the main results."""
        ## a deletion the sync never mentions: an outcome only test 4 records,
        ## where an unset feature would read as full
        checker = TestSyncTokenDeleteGrading()._checker(_SyncResult("token-3", changes=0))
        checker._client_obj.report.side_effect = RuntimeError("probe crashed")
        with pytest.raises(RuntimeError):
            CheckSyncToken(checker)._run_check()
        assert checker._features_checked.is_supported("sync-token.delete", str) == "unsupported"
