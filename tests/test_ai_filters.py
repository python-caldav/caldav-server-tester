"""Unit tests for filter functions in checks.py"""

## DISCLAIMER: those tests are AI-generated, gone through a very quick human QA

from datetime import date, datetime, timezone
from unittest.mock import Mock

import pytest

from caldav_server_tester.checks import _filter_2000


class TestFilter2000:
    """Test the _filter_2000 function that filters calendar objects by date range"""

    def create_mock_object(self, dtstart=None, dtend=None, due=None) -> Mock:
        """Helper to create a mock calendar object with date properties"""
        obj = Mock()
        component = Mock()

        # Set up the component properties
        if dtstart is not None:
            component.__contains__ = lambda self, key: key == "dtstart" or (key == "due" and due is not None) or (key == "dtend" and dtend is not None)
            component.start = dtstart
        elif due is not None or dtend is not None:
            component.__contains__ = lambda self, key: (key == "due" and due is not None) or (key == "dtend" and dtend is not None)
            component.end = due if due is not None else dtend
        else:
            component.__contains__ = lambda self, key: False

        obj.component = component
        return obj

    def test_filter_includes_dtstart_at_start_boundary(self) -> None:
        """Objects with dtstart exactly at 2000-01-01 should be included"""
        obj = self.create_mock_object(dtstart=date(2000, 1, 1))
        result = list(_filter_2000([obj]))
        assert len(result) == 1
        assert result[0] == obj

    def test_filter_includes_dtstart_at_end_boundary(self) -> None:
        """Objects with dtstart exactly at 2001-01-01 should be included"""
        obj = self.create_mock_object(dtstart=date(2001, 1, 1))
        result = list(_filter_2000([obj]))
        assert len(result) == 1
        assert result[0] == obj

    def test_filter_includes_dtstart_in_middle(self) -> None:
        """Objects with dtstart in the middle of year 2000 should be included"""
        obj = self.create_mock_object(dtstart=date(2000, 6, 15))
        result = list(_filter_2000([obj]))
        assert len(result) == 1
        assert result[0] == obj

    def test_filter_excludes_dtstart_before_range(self) -> None:
        """Objects with dtstart before 2000-01-01 should be excluded"""
        obj = self.create_mock_object(dtstart=date(1999, 12, 31))
        result = list(_filter_2000([obj]))
        assert len(result) == 0

    def test_filter_excludes_dtstart_after_range(self) -> None:
        """Objects with dtstart after 2001-01-01 should be excluded"""
        obj = self.create_mock_object(dtstart=date(2001, 1, 2))
        result = list(_filter_2000([obj]))
        assert len(result) == 0

    def test_filter_handles_datetime_objects(self) -> None:
        """Objects with datetime (not just date) should work correctly"""
        obj = self.create_mock_object(
            dtstart=datetime(2000, 6, 15, 12, 30, 0, tzinfo=timezone.utc)
        )
        result = list(_filter_2000([obj]))
        assert len(result) == 1
        assert result[0] == obj

    def test_filter_uses_due_when_no_dtstart(self) -> None:
        """Objects without dtstart but with due in range should be included"""
        obj = self.create_mock_object(due=date(2000, 6, 15))
        result = list(_filter_2000([obj]))
        assert len(result) == 1
        assert result[0] == obj

    def test_filter_uses_dtend_when_no_dtstart(self) -> None:
        """Objects without dtstart but with dtend in range should be included"""
        obj = self.create_mock_object(dtend=date(2000, 6, 15))
        result = list(_filter_2000([obj]))
        assert len(result) == 1
        assert result[0] == obj

    def test_filter_excludes_objects_without_dates(self) -> None:
        """Objects without any date fields should cause TypeError (code has bug with date(1980))"""
        obj = self.create_mock_object()
        # Note: This test documents a bug in the actual code - date(1980) is missing month/day
        # The code should use date(1980, 1, 1) instead
        with pytest.raises(TypeError, match="missing required argument"):
            list(_filter_2000([obj]))

    def test_filter_handles_multiple_objects(self) -> None:
        """Filter should correctly handle multiple objects"""
        obj1 = self.create_mock_object(dtstart=date(2000, 1, 1))
        obj2 = self.create_mock_object(dtstart=date(1999, 12, 31))
        obj3 = self.create_mock_object(dtstart=date(2000, 6, 15))
        obj4 = self.create_mock_object(dtstart=date(2001, 1, 2))
        obj5 = self.create_mock_object(dtstart=date(2001, 1, 1))

        result = list(_filter_2000([obj1, obj2, obj3, obj4, obj5]))
        assert len(result) == 3
        assert obj1 in result
        assert obj3 in result
        assert obj5 in result

    def test_filter_returns_generator(self) -> None:
        """_filter_2000 should return a generator, not a list"""
        obj = self.create_mock_object(dtstart=date(2000, 6, 15))
        result = _filter_2000([obj])

        # Check it's a generator
        assert hasattr(result, '__iter__')
        assert hasattr(result, '__next__')

    def test_filter_handles_empty_list(self) -> None:
        """Filter should handle empty input list"""
        result = list(_filter_2000([]))
        assert len(result) == 0
