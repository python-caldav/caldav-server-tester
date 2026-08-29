"""Unit tests for filter functions in checks.py"""

## DISCLAIMER: those tests are AI-generated, gone through a very quick human QA

from datetime import date, datetime, timezone
from unittest.mock import Mock

from caldav_server_tester.checks import _base_year, _filter_fixture_window

## The fixture window is a single calendar year; tests use a representative base.
BASE = 2027


class TestBaseYear:
    """The fixture base year is always the next calendar year."""

    def test_base_year_is_next_year(self) -> None:
        assert _base_year(datetime(2026, 6, 8, tzinfo=timezone.utc)) == 2027

    def test_base_year_late_in_year(self) -> None:
        ## Stable within a calendar year - only flips on Jan 1
        assert _base_year(datetime(2026, 12, 31, 23, 59, tzinfo=timezone.utc)) == 2027

    def test_base_year_early_in_year(self) -> None:
        assert _base_year(datetime(2027, 1, 1, tzinfo=timezone.utc)) == 2028


class TestFilterFixtureWindow:
    """Test _filter_fixture_window: keep objects whose date is in the base year."""

    def create_mock_object(self, dtstart=None, dtend=None, due=None) -> Mock:
        """Helper to create a mock calendar object with date properties"""
        obj = Mock()
        component = Mock()

        # Set up the component properties
        if dtstart is not None:
            component.__contains__ = lambda self, key: (
                key == "dtstart" or (key == "due" and due is not None) or (key == "dtend" and dtend is not None)
            )
            component.start = dtstart
        elif due is not None or dtend is not None:
            component.__contains__ = lambda self, key: (
                (key == "due" and due is not None) or (key == "dtend" and dtend is not None)
            )
            component.end = due if due is not None else dtend
        else:
            component.__contains__ = lambda self, key: False

        obj.component = component
        return obj

    def test_filter_includes_dtstart_at_start_boundary(self) -> None:
        """Objects with dtstart exactly at <base>-01-01 should be included"""
        obj = self.create_mock_object(dtstart=date(BASE, 1, 1))
        result = list(_filter_fixture_window([obj], BASE))
        assert result == [obj]

    def test_filter_includes_dtstart_at_end_boundary(self) -> None:
        """Objects with dtstart exactly at <base+1>-01-01 should be included"""
        obj = self.create_mock_object(dtstart=date(BASE + 1, 1, 1))
        result = list(_filter_fixture_window([obj], BASE))
        assert result == [obj]

    def test_filter_includes_dtstart_in_middle(self) -> None:
        """Objects with dtstart in the middle of the base year should be included"""
        obj = self.create_mock_object(dtstart=date(BASE, 6, 15))
        result = list(_filter_fixture_window([obj], BASE))
        assert result == [obj]

    def test_filter_excludes_dtstart_before_range(self) -> None:
        """Objects with dtstart before <base>-01-01 should be excluded"""
        obj = self.create_mock_object(dtstart=date(BASE - 1, 12, 31))
        result = list(_filter_fixture_window([obj], BASE))
        assert result == []

    def test_filter_excludes_dtstart_after_range(self) -> None:
        """Objects with dtstart after <base+1>-01-01 should be excluded"""
        obj = self.create_mock_object(dtstart=date(BASE + 1, 1, 2))
        result = list(_filter_fixture_window([obj], BASE))
        assert result == []

    def test_filter_excludes_year_2000_probes(self) -> None:
        """The persistent year-2000 old-date probes must NOT be treated as fixtures"""
        obj = self.create_mock_object(dtstart=datetime(2000, 1, 1, 12, 0, tzinfo=timezone.utc))
        result = list(_filter_fixture_window([obj], BASE))
        assert result == []

    def test_filter_handles_datetime_objects(self) -> None:
        """Objects with datetime (not just date) should work correctly"""
        obj = self.create_mock_object(dtstart=datetime(BASE, 6, 15, 12, 30, 0, tzinfo=timezone.utc))
        result = list(_filter_fixture_window([obj], BASE))
        assert result == [obj]

    def test_filter_uses_due_when_no_dtstart(self) -> None:
        """Objects without dtstart but with due in range should be included"""
        obj = self.create_mock_object(due=date(BASE, 6, 15))
        result = list(_filter_fixture_window([obj], BASE))
        assert result == [obj]

    def test_filter_uses_dtend_when_no_dtstart(self) -> None:
        """Objects without dtstart but with dtend in range should be included"""
        obj = self.create_mock_object(dtend=date(BASE, 6, 15))
        result = list(_filter_fixture_window([obj], BASE))
        assert result == [obj]

    def test_filter_excludes_objects_without_dates(self) -> None:
        """Objects without any date fields fall back to date(1980, 1, 1) and are excluded"""
        obj = self.create_mock_object()
        result = list(_filter_fixture_window([obj], BASE))
        assert result == []

    def test_filter_handles_multiple_objects(self) -> None:
        """Filter should correctly handle multiple objects"""
        obj1 = self.create_mock_object(dtstart=date(BASE, 1, 1))
        obj2 = self.create_mock_object(dtstart=date(BASE - 1, 12, 31))
        obj3 = self.create_mock_object(dtstart=date(BASE, 6, 15))
        obj4 = self.create_mock_object(dtstart=date(BASE + 1, 1, 2))
        obj5 = self.create_mock_object(dtstart=date(BASE + 1, 1, 1))

        result = list(_filter_fixture_window([obj1, obj2, obj3, obj4, obj5], BASE))
        assert len(result) == 3
        assert obj1 in result
        assert obj3 in result
        assert obj5 in result

    def test_filter_returns_generator(self) -> None:
        """_filter_fixture_window should return a generator, not a list"""
        obj = self.create_mock_object(dtstart=date(BASE, 6, 15))
        result = _filter_fixture_window([obj], BASE)

        # Check it's a generator
        assert hasattr(result, "__iter__")
        assert hasattr(result, "__next__")

    def test_filter_handles_empty_list(self) -> None:
        """Filter should handle empty input list"""
        result = list(_filter_fixture_window([], BASE))
        assert result == []
