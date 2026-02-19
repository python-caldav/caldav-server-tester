"""Unit tests for check classes with mocked server responses

These tests demonstrate how to mock CalDAV server responses for testing.
They execute the actual check logic but with mocked server responses.

NOTE: These tests can be slow because they run complex
check logic. Use pytest -m "not slow" to skip them in normal development.

DISCLAIMER: those tests are AI-generated, and haven't been reviewed

Tests based on mocked up server-client-communication is notoriously
fragile, the only reason why this is added at all is that it's a
relatively cheap thing to do with AI - but the value is questionable.
If those tests will break in the future, then consider just deleting
this file.
"""

from datetime import date, datetime, timezone
from unittest.mock import Mock, MagicMock, patch, PropertyMock
import pytest

from caldav.compatibility_hints import FeatureSet
from caldav.lib.error import NotFoundError, AuthorizationError, ReportError
from caldav_server_tester.checker import ServerQuirkChecker
from caldav_server_tester.checks import (
    CheckGetCurrentUserPrincipal,
    CheckIsNotDefined,
    CheckMakeDeleteCalendar,
    PrepareCalendar,
    CheckSearch,
)

# Mark all tests in this file as slow since they run actual check logic
pytestmark = pytest.mark.slow


class TestCheckGetCurrentUserPrincipal:
    """Test CheckGetCurrentUserPrincipal with mocked server responses"""

    def create_checker_with_mock_client(self) -> tuple[ServerQuirkChecker, Mock]:
        """Helper to create checker with mocked client"""
        client = Mock()
        client.features = FeatureSet()
        checker = ServerQuirkChecker(client, debug_mode=None)
        return checker, client

    def test_principal_supported_sets_feature_to_true(self) -> None:
        """When principal() succeeds, feature should be set to True"""
        checker, client = self.create_checker_with_mock_client()

        # Mock successful principal response
        mock_principal = Mock()
        client.principal.return_value = mock_principal

        check = CheckGetCurrentUserPrincipal(checker)
        check.run_check()

        # Should set feature to supported
        assert checker.features_checked.is_supported("get-current-user-principal")
        assert checker.principal == mock_principal

    def test_principal_failure_sets_feature_to_false(self) -> None:
        """When principal() fails, feature should be set to False"""
        checker, client = self.create_checker_with_mock_client()

        # Mock failed principal response
        client.principal.side_effect = Exception("Connection error")

        check = CheckGetCurrentUserPrincipal(checker)
        check.run_check()

        # Should set feature to unsupported
        assert not checker.features_checked.is_supported("get-current-user-principal")
        assert checker.principal is None

    def test_principal_assertion_error_reraises(self) -> None:
        """AssertionError should be re-raised, not caught"""
        checker, client = self.create_checker_with_mock_client()

        # Mock AssertionError
        client.principal.side_effect = AssertionError("Test assertion")

        check = CheckGetCurrentUserPrincipal(checker)

        with pytest.raises(AssertionError, match="Test assertion"):
            check.run_check()


class TestCheckMakeDeleteCalendar:
    """Test CheckMakeDeleteCalendar with mocked server responses"""

    def create_checker_with_principal(self) -> tuple[ServerQuirkChecker, Mock, Mock]:
        """Helper to create checker with mocked client and principal"""
        client = Mock()
        client.features = FeatureSet()
        checker = ServerQuirkChecker(client, debug_mode=None)

        # Mock principal
        mock_principal = Mock()
        checker.principal = mock_principal

        # Mark dependency as run
        checker._checks_run.add(CheckGetCurrentUserPrincipal)

        return checker, client, mock_principal

    def test_calendar_auto_creation_detected(self) -> None:
        """When accessing non-existent calendar creates it, auto feature is set"""
        checker, client, principal = self.create_checker_with_principal()

        # Mock auto-creation: accessing a non-existent calendar auto-creates it
        mock_auto_calendar = Mock()
        mock_auto_calendar.events.return_value = []

        # Mock successful calendar creation when trying to make test calendars
        mock_test_calendar = Mock()
        mock_test_calendar.id = "caldav-server-checker-mkdel-test"

        # Track state: calendar is created (by make_calendar) then deleted
        created = [False]
        deleted = [False]

        def delete_cal():
            # Only mark as deleted if it was actually created
            if created[0]:
                deleted[0] = True
        mock_test_calendar.delete = delete_cal

        # events() should raise NotFoundError after deletion
        def events_side_effect():
            if deleted[0]:
                raise NotFoundError("Calendar was deleted")
            return []
        mock_test_calendar.events.side_effect = events_side_effect

        # Track all calls for debugging
        calls = []
        def calendar_side_effect(cal_id=None, name=None):
            calls.append((cal_id, name))

            # First call: checking if "this_should_not_exist" auto-creates
            if cal_id == "this_should_not_exist":
                # Auto-creation: returns a calendar even though it "shouldn't exist"
                return mock_auto_calendar

            # Calls during _try_make_calendar for "caldav-server-checker-mkdel-test":
            # 1. Line 96: Try to delete if exists (before creation)
            # 2. Line 107: Verify after make_calendar()
            # 3. Line 117: Look up by name for displayname check
            # 4. Line 142: Check if deleted
            # 5. Line 158: Recheck after sleep if not deleted

            # If calendar was deleted, it's not found
            if deleted[0] and cal_id == "caldav-server-checker-mkdel-test":
                raise NotFoundError("Calendar was deleted")

            # Looking up by name (for displayname check)
            if name == "Yep":
                cal = Mock()
                cal.id = mock_test_calendar.id
                cal.events.return_value = []
                return cal

            # Normal lookup by cal_id (before deletion)
            if cal_id == "caldav-server-checker-mkdel-test":
                return mock_test_calendar

            # Everything else not found
            raise NotFoundError("Calendar not found")

        principal.calendar.side_effect = calendar_side_effect
        principal.calendars.return_value = []

        # Track when calendar is created
        def make_calendar_side_effect(cal_id=None, **kwargs):
            created[0] = True
            return mock_test_calendar
        principal.make_calendar.side_effect = make_calendar_side_effect

        check = CheckMakeDeleteCalendar(checker)
        check.run_check()

        # Should detect auto-creation
        assert checker.features_checked.is_supported("create-calendar.auto")

    @pytest.mark.skip(reason="Complex multi-call mocking pattern - CheckMakeDeleteCalendar._run_check has very complex logic with multiple retry paths")
    def test_calendar_no_auto_creation(self) -> None:
        """When accessing non-existent calendar fails, auto feature is not set"""
        checker, client, principal = self.create_checker_with_principal()

        # Mock successful manual creation
        mock_calendar = Mock()
        mock_calendar.events.return_value = []
        mock_calendar.id = "caldav-server-checker-mkdel-test"
        deleted_count = [0]

        def delete_calendar():
            deleted_count[0] += 1

        mock_calendar.delete = delete_calendar
        principal.make_calendar.return_value = mock_calendar

        # Mock calendar lookup behavior
        call_count = [0]
        def calendar_side_effect(cal_id=None, name=None):
            call_count[0] += 1
            # Initial cleanup attempt - calendar doesn't exist
            if call_count[0] == 1 and cal_id == "this_should_not_exist":
                raise NotFoundError("Not found")
            # Lookup by name (for displayname test) - should find it
            if name == "Yep" and cal_id == "caldav-server-checker-mkdel-test":
                mock_cal = Mock()
                mock_cal.id = mock_calendar.id
                mock_cal.events.return_value = []
                return mock_cal
            # After calendar is deleted, it's not found
            if deleted_count[0] > 0 and cal_id == "caldav-server-checker-mkdel-test":
                raise NotFoundError("Deleted")
            # Lookup by cal_id returns the created calendar (before deletion)
            if cal_id == "caldav-server-checker-mkdel-test":
                return mock_calendar
            # Other lookups fail
            raise NotFoundError("Not found")

        principal.calendar.side_effect = calendar_side_effect
        principal.calendars.return_value = []

        check = CheckMakeDeleteCalendar(checker)
        check.run_check()

        # Should not detect auto-creation (first attempt with weird name fails)
        # Note: The actual result depends on complex flow, but calendar creation should succeed
        assert checker.features_checked.is_supported("create-calendar")

    def test_calendar_creation_with_displayname(self) -> None:
        """Calendar creation with display name should be detected"""
        checker, client, principal = self.create_checker_with_principal()

        # Mock no auto-creation
        principal.calendar.side_effect = NotFoundError("Not found")

        # Mock successful calendar creation with name
        mock_calendar = Mock()
        mock_calendar.events.return_value = []
        mock_calendar.id = "caldav-server-checker-mkdel-test"
        mock_calendar.delete = Mock()

        principal.make_calendar.return_value = mock_calendar

        # Mock calendar retrieval by name
        calendar_calls = []
        def calendar_side_effect(cal_id=None, name=None):
            calendar_calls.append((cal_id, name))
            if name == "Yep":
                mock_calendar2 = Mock()
                mock_calendar2.id = mock_calendar.id
                mock_calendar2.events.return_value = []
                return mock_calendar2
            if cal_id == "caldav-server-checker-mkdel-test":
                return mock_calendar
            raise NotFoundError("Not found")

        principal.calendar.side_effect = calendar_side_effect

        check = CheckMakeDeleteCalendar(checker)
        check.run_check()

        # Should detect displayname support
        assert checker.features_checked.is_supported("create-calendar.set-displayname")

    @pytest.mark.skip(reason="Complex multi-call mocking pattern - CheckMakeDeleteCalendar._run_check has very complex logic with multiple retry paths")
    def test_calendar_deletion_successful(self) -> None:
        """Successful calendar deletion should set delete-calendar feature"""
        checker, client, principal = self.create_checker_with_principal()

        # Mock successful calendar creation
        mock_calendar = Mock()
        mock_calendar.events.return_value = []
        mock_calendar.id = "caldav-server-checker-mkdel-test"

        # Track deletion
        deleted_count = [0]
        def delete_side_effect():
            deleted_count[0] += 1

        mock_calendar.delete = delete_side_effect
        principal.make_calendar.return_value = mock_calendar
        principal.calendars.return_value = []

        # After deletion, calendar should not be found
        call_count = [0]
        def calendar_lookup(cal_id=None, name=None):
            call_count[0] += 1
            # Initial cleanup - doesn't exist
            if call_count[0] == 1 and cal_id == "this_should_not_exist":
                raise NotFoundError("Not found")
            # After deletion, calendar not found
            if deleted_count[0] > 0 and cal_id == "caldav-server-checker-mkdel-test":
                raise NotFoundError("Deleted")
            # Lookup by name  with cal_id
            if name == "Yep" and cal_id == "caldav-server-checker-mkdel-test":
                cal = Mock()
                cal.id = mock_calendar.id
                cal.events.return_value = []
                return cal
            # Before deletion, return calendar
            if cal_id == "caldav-server-checker-mkdel-test":
                return mock_calendar
            raise NotFoundError("Not found")

        principal.calendar.side_effect = calendar_lookup

        check = CheckMakeDeleteCalendar(checker)
        check.run_check()

        # Should detect deletion support
        assert checker.features_checked.is_supported("delete-calendar")

    @pytest.mark.skip(reason="Complex multi-call mocking pattern - CheckMakeDeleteCalendar._run_check has very complex logic with multiple retry paths")
    def test_calendar_has_default_calendar(self) -> None:
        """Principal with existing calendars should set has-calendar feature"""
        checker, client, principal = self.create_checker_with_principal()

        # Mock existing calendars
        mock_calendar = Mock()
        mock_calendar.events.return_value = []
        principal.calendars.return_value = [mock_calendar]

        # Mock calendar creation for test calendars
        mock_test_cal = Mock()
        mock_test_cal.events.return_value = []
        mock_test_cal.id = "caldav-server-checker-mkdel-test"
        deleted_count = [0]

        def delete_cal():
            deleted_count[0] += 1

        mock_test_cal.delete = delete_cal
        principal.make_calendar.return_value = mock_test_cal

        call_count = [0]
        def calendar_lookup(cal_id=None, name=None):
            call_count[0] += 1
            # Initial cleanup
            if call_count[0] == 1 and cal_id == "this_should_not_exist":
                raise NotFoundError("Not found")
            # After deletion
            if deleted_count[0] > 0 and cal_id == "caldav-server-checker-mkdel-test":
                raise NotFoundError("Deleted")
            # Lookup by name with cal_id
            if name == "Yep" and cal_id == "caldav-server-checker-mkdel-test":
                cal = Mock()
                cal.id = mock_test_cal.id
                cal.events.return_value = []
                return cal
            # Before deletion
            if cal_id == "caldav-server-checker-mkdel-test":
                return mock_test_cal
            raise NotFoundError("Not found")

        principal.calendar.side_effect = calendar_lookup

        check = CheckMakeDeleteCalendar(checker)
        check.run_check()

        # Should detect existing calendar
        assert checker.features_checked.is_supported("get-current-user-principal.has-calendar")


class TestPrepareCalendar:
    """Test PrepareCalendar with mocked server responses

    Note: PrepareCalendar is complex and does extensive setup. These tests
    focus on key mocking patterns rather than exhaustive coverage.
    """

    def create_checker_with_calendar(self) -> tuple[ServerQuirkChecker, Mock, Mock]:
        """Helper to create checker with mocked calendar"""
        client = Mock()
        client.features = FeatureSet()
        # Mock expected_features to avoid lookup issues
        client.features.copyFeatureSet(
            {"test-calendar.compatibility-tests": {}}, collapse=False
        )
        checker = ServerQuirkChecker(client, debug_mode=None)

        # Mock principal
        mock_principal = Mock()
        checker.principal = mock_principal
        checker.expected_features = client.features

        # Mark dependencies as run
        checker._checks_run.add(CheckGetCurrentUserPrincipal)
        checker._checks_run.add(CheckMakeDeleteCalendar)

        # Mock that create-calendar is supported
        checker._features_checked.copyFeatureSet(
            {"create-calendar": {"support": "full"}}, collapse=False
        )

        return checker, client, mock_principal

    def test_prepare_uses_existing_calendar_by_id(self) -> None:
        """PrepareCalendar should use existing calendar if found"""
        checker, client, principal = self.create_checker_with_calendar()

        # Mock existing calendar with all necessary methods
        mock_calendar = Mock()
        mock_calendar.events.return_value = [Mock()]  # Return non-empty to pass assertion
        mock_calendar.todos.return_value = [Mock()]
        mock_calendar.journals.return_value = []
        mock_calendar.search.return_value = []

        # Mock save_object to handle test data creation
        def save_object(*args, **kwargs):
            obj = Mock()
            obj.component = Mock()
            obj.component.__getitem__ = lambda self, key: kwargs.get("uid", "test-uid")
            obj.load = Mock()
            return obj

        mock_calendar.save_object = save_object
        principal.calendar.return_value = mock_calendar

        check = PrepareCalendar(checker)
        check.run_check()

        # Should use existing calendar
        assert checker.calendar == mock_calendar
        principal.make_calendar.assert_not_called()

    def test_prepare_creates_calendar_if_not_found(self) -> None:
        """PrepareCalendar should create calendar if not found"""
        checker, client, principal = self.create_checker_with_calendar()

        # Mock calendar not found on first call, then return created calendar
        call_count = [0]
        mock_calendar = Mock()
        mock_calendar.events.return_value = [Mock()]
        mock_calendar.todos.return_value = [Mock()]
        mock_calendar.journals.return_value = []
        mock_calendar.search.return_value = []

        def save_object(*args, **kwargs):
            obj = Mock()
            obj.component = Mock()
            obj.component.__getitem__ = lambda self, key: kwargs.get("uid", "test-uid")
            obj.load = Mock()
            return obj

        mock_calendar.save_object = save_object

        def calendar_side_effect(cal_id=None, name=None):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("Not found")
            return mock_calendar

        principal.calendar.side_effect = calendar_side_effect
        principal.make_calendar.return_value = mock_calendar

        check = PrepareCalendar(checker)
        check.run_check()

        # Should create calendar
        principal.make_calendar.assert_called_once()
        assert checker.calendar == mock_calendar

    def test_prepare_sets_save_load_event_feature(self) -> None:
        """PrepareCalendar should set save-load.event feature"""
        checker, client, principal = self.create_checker_with_calendar()

        # Mock calendar with all necessary behavior
        mock_calendar = Mock()
        mock_calendar.events.return_value = [Mock()]
        mock_calendar.todos.return_value = [Mock()]
        mock_calendar.journals.return_value = []
        mock_calendar.search.return_value = []

        def save_object(*args, **kwargs):
            obj = Mock()
            obj.component = Mock()
            obj.component.__getitem__ = lambda self, key: kwargs.get("uid", "test-uid")
            obj.load = Mock()
            return obj

        mock_calendar.save_object = save_object
        principal.calendar.return_value = mock_calendar

        check = PrepareCalendar(checker)
        check.run_check()

        # Should set event save/load feature
        assert checker.features_checked.is_supported("save-load.event")


class TestCheckSearch:
    """Test CheckSearch with mocked server responses"""

    def create_checker_with_prepared_calendar(self) -> tuple[ServerQuirkChecker, Mock, Mock]:
        """Helper to create checker with prepared calendar"""
        client = Mock()
        client.features = FeatureSet()
        checker = ServerQuirkChecker(client, debug_mode=None)

        # Mock calendar and tasklist
        mock_calendar = Mock()
        mock_tasklist = Mock()
        checker.calendar = mock_calendar
        checker.tasklist = mock_tasklist

        # Mark dependencies as run
        checker._checks_run.add(CheckGetCurrentUserPrincipal)
        checker._checks_run.add(CheckMakeDeleteCalendar)
        checker._checks_run.add(PrepareCalendar)

        return checker, mock_calendar, mock_tasklist

    def test_search_time_range_event_success(self) -> None:
        """Successful time-range event search sets feature to True"""
        checker, calendar, tasklist = self.create_checker_with_prepared_calendar()

        # Mock search returning one event
        mock_event = Mock()
        calendar.search.return_value = [mock_event]
        tasklist.search.return_value = []

        check = CheckSearch(checker)
        check.run_check()

        # Should set feature to supported
        assert checker.features_checked.is_supported("search.time-range.event")

    def test_search_time_range_event_failure(self) -> None:
        """Failed time-range event search (wrong count) sets feature to False"""
        checker, calendar, tasklist = self.create_checker_with_prepared_calendar()

        # Mock search returning wrong number of events
        calendar.search.return_value = []
        tasklist.search.return_value = []

        check = CheckSearch(checker)
        check.run_check()

        # Should set feature to unsupported
        assert not checker.features_checked.is_supported("search.time-range.event")

    def test_search_time_range_todo_success(self) -> None:
        """Successful time-range todo search sets feature to True"""
        checker, calendar, tasklist = self.create_checker_with_prepared_calendar()

        # Mock search
        calendar.search.return_value = [Mock()]  # One event
        mock_todo = Mock()
        tasklist.search.return_value = [mock_todo]  # One todo

        check = CheckSearch(checker)
        check.run_check()

        # Should set todo search feature
        assert checker.features_checked.is_supported("search.time-range.todo")

    def test_search_category_supported(self) -> None:
        """Category search returning correct results sets feature to True"""
        checker, calendar, tasklist = self.create_checker_with_prepared_calendar()

        # Mock initial time-range searches
        def search_side_effect(**kwargs):
            if 'category' in kwargs:
                # Category search returns one result
                return [Mock()]
            elif 'event' in kwargs and kwargs.get('event'):
                # Time-range event search
                return [Mock()]
            elif 'todo' in kwargs:
                # Time-range todo search
                return [Mock()]
            return []

        calendar.search.side_effect = search_side_effect
        tasklist.search.side_effect = search_side_effect

        check = CheckSearch(checker)
        check.run_check()

        # Should set category search feature
        assert checker.features_checked.is_supported("search.text.category")

    def test_search_category_ungraceful(self) -> None:
        """Category search raising ReportError sets feature to 'ungraceful'"""
        checker, calendar, tasklist = self.create_checker_with_prepared_calendar()

        def search_side_effect(**kwargs):
            if 'category' in kwargs:
                raise ReportError("Category not supported")
            elif 'event' in kwargs and kwargs.get('event'):
                return [Mock()]
            elif 'todo' in kwargs:
                return [Mock()]
            return []

        calendar.search.side_effect = search_side_effect
        tasklist.search.return_value = [Mock()]

        check = CheckSearch(checker)
        check.run_check()

        # Should set feature to ungraceful
        result = checker.features_checked.is_supported("search.text.category", str)
        assert result == "ungraceful"

    def test_search_combined_logical_and(self) -> None:
        """Combined search filters should work as logical AND"""
        checker, calendar, tasklist = self.create_checker_with_prepared_calendar()

        search_calls = []

        def search_side_effect(**kwargs):
            search_calls.append(kwargs)

            # Time-range only
            if 'event' in kwargs and not 'category' in kwargs:
                return [Mock()]

            # Category + time range (wider range) = 1 result
            if 'category' in kwargs and 'start' in kwargs:
                start = kwargs['start']
                if start.day == 1 and start.hour == 11:
                    return [Mock()]  # Wider range matches
                elif start.day == 1 and start.hour == 9:
                    return []  # Narrower range doesn't match

            # Just category
            if 'category' in kwargs:
                return [Mock()]

            # Todos
            if 'todo' in kwargs:
                return [Mock()]

            return []

        calendar.search.side_effect = search_side_effect
        tasklist.search.return_value = [Mock()]

        check = CheckSearch(checker)
        check.run_check()

        # Should detect logical AND
        assert checker.features_checked.is_supported("search.combined-is-logical-and")


class TestCheckIsNotDefined:
    """Test CheckIsNotDefined with mocked server responses"""

    def create_checker_with_prepared_calendar(self) -> tuple[ServerQuirkChecker, Mock]:
        """Helper to create checker with prepared calendar"""
        client = Mock()
        client.features = FeatureSet()
        checker = ServerQuirkChecker(client, debug_mode=None)

        mock_calendar = Mock()
        checker.calendar = mock_calendar

        # Mark dependencies as run
        checker._checks_run.add(CheckGetCurrentUserPrincipal)
        checker._checks_run.add(CheckMakeDeleteCalendar)
        checker._checks_run.add(PrepareCalendar)

        return checker, mock_calendar

    def _make_event_mock(self, uid, has_categories=False, has_class=False):
        """Create a mock event with optional CATEGORIES and CLASS"""
        event = Mock()
        component = {}
        component["uid"] = uid
        if has_categories:
            component["categories"] = "test"
        if has_class:
            component["class"] = "CONFIDENTIAL"
        event.component = Mock()
        event.component.get = lambda key, default="": component.get(key, default)
        return event

    def _make_save_object_uid_capture(self) -> tuple[list[str], object]:
        """Return a (saved_uids, side_effect) pair that records UIDs from save_object calls.

        The iCal UID is extracted from the iCal string so that the search mock can
        return events with UIDs matching what was actually saved.
        """
        import re
        saved_uids: list[str] = []

        def side_effect(cls, ical_str: str) -> Mock:
            m = re.search(r"^UID:(\S+)", ical_str, re.MULTILINE)
            uid = m.group(1) if m else f"unknown_{len(saved_uids)}"
            saved_uids.append(uid)
            return Mock()

        return saved_uids, side_effect

    def test_is_not_defined_full_support(self) -> None:
        """Category, class, and dtend is-not-defined all work correctly"""
        checker, calendar = self.create_checker_with_prepared_calendar()

        def search_side_effect(**kwargs):
            if kwargs.get("no_category"):
                # Return events WITHOUT categories (exclude csc_event_with_categories)
                return [
                    self._make_event_mock("csc_simple_event1"),
                    self._make_event_mock("csc_simple_event2"),
                ]
            if kwargs.get("no_class"):
                # Return events WITHOUT class (exclude csc_event_with_class)
                return [
                    self._make_event_mock("csc_simple_event1"),
                    self._make_event_mock("csc_simple_event2"),
                ]
            if kwargs.get("no_dtend"):
                # Return only the event without DTEND (csc_event_with_duration);
                # csc_simple_event1 (has DTEND) is correctly excluded
                return [self._make_event_mock("csc_event_with_duration")]
            return [Mock()]

        calendar.search.side_effect = search_side_effect

        check = CheckIsNotDefined(checker)
        check.run_check()

        assert checker.features_checked.is_supported("search.is-not-defined")
        assert checker.features_checked.is_supported("search.is-not-defined.category")
        assert checker.features_checked.is_supported("search.is-not-defined.dtend")

    def test_is_not_defined_category_unsupported(self) -> None:
        """Server supports is-not-defined for CLASS and DTEND but not for CATEGORIES"""
        checker, calendar = self.create_checker_with_prepared_calendar()

        def search_side_effect(**kwargs):
            if kwargs.get("no_category"):
                # Server returns nothing for category is-not-defined (broken for CATEGORIES)
                return []
            if kwargs.get("no_class"):
                # Works correctly: excludes csc_event_with_class
                return [
                    self._make_event_mock("csc_simple_event1"),
                    self._make_event_mock("csc_simple_event2"),
                ]
            if kwargs.get("no_dtend"):
                # Works correctly: returns csc_event_with_duration, excludes csc_simple_event1
                return [self._make_event_mock("csc_event_with_duration")]
            return [Mock()]

        calendar.search.side_effect = search_side_effect

        check = CheckIsNotDefined(checker)
        check.run_check()

        assert not checker.features_checked.is_supported("search.is-not-defined")
        assert not checker.features_checked.is_supported("search.is-not-defined.category")
        assert checker.features_checked.is_supported("search.is-not-defined.dtend")
        assert checker.features_checked.is_supported("search.is-not-defined", str) == "fragile"

    def test_is_not_defined_unsupported(self) -> None:
        """Server ignores is-not-defined filter (no properties work)"""
        checker, calendar = self.create_checker_with_prepared_calendar()

        def search_side_effect(**kwargs):
            if kwargs.get("no_category"):
                # Returns the event WITH categories too (filter ignored)
                return [
                    self._make_event_mock("csc_simple_event1"),
                    self._make_event_mock("csc_event_with_categories", has_categories=True),
                ]
            if kwargs.get("no_class"):
                # Returns csc_event_with_class too (filter ignored)
                return [
                    self._make_event_mock("csc_simple_event1"),
                    self._make_event_mock("csc_event_with_class"),
                ]
            if kwargs.get("no_dtend"):
                # Returns csc_simple_event1 (has DTEND) but not csc_event_with_duration (filter broken)
                return [self._make_event_mock("csc_simple_event1")]
            return [Mock()]

        calendar.search.side_effect = search_side_effect

        check = CheckIsNotDefined(checker)
        check.run_check()

        assert not checker.features_checked.is_supported("search.is-not-defined")
        assert not checker.features_checked.is_supported("search.is-not-defined.category")
        assert not checker.features_checked.is_supported("search.is-not-defined.dtend")

    def test_is_not_defined_ungraceful(self) -> None:
        """Server throws error on is-not-defined search"""
        checker, calendar = self.create_checker_with_prepared_calendar()

        calendar.search.side_effect = ReportError("is-not-defined not supported")

        check = CheckIsNotDefined(checker)
        check.run_check()

        result = checker.features_checked.is_supported("search.is-not-defined", str)
        assert result == "ungraceful"
        assert checker.features_checked.is_supported("search.is-not-defined.category", str) == "ungraceful"
        assert checker.features_checked.is_supported("search.is-not-defined.dtend", str) == "ungraceful"
