"""CheckCalendarProperties must leave the calendar's properties as it found them.

The probe works by *writing* calendar-color and calendar-order.  With
--caldav-calendar pointing at a real calendar (the documented mode for servers
without MKCALENDAR) an unrestored probe silently repaints the user's production
calendar, which is exactly what the rest of this tool works to avoid.
"""

from unittest.mock import Mock

from caldav.compatibility_hints import FeatureSet
from caldav.elements import ical

from caldav_server_tester.checks import CheckCalendarProperties


def _make_checker(stored: dict) -> tuple[Mock, dict, list]:
    """A calendar whose property store starts at `stored`; returns the write log."""
    writes: list[tuple[str, str]] = []

    def set_properties(elements):
        for el in elements:
            stored[el.tag] = el.value
            writes.append((el.tag, el.value))

    def get_properties(elements):
        return {el.tag: stored.get(el.tag) for el in elements}

    cal = Mock()
    cal.set_properties.side_effect = set_properties
    cal.get_properties.side_effect = get_properties

    checker = Mock()
    checker._features_checked = FeatureSet()
    checker.debug_mode = None
    checker._client_obj = Mock()
    checker.expected_features = FeatureSet()
    checker.calendar = cal
    return checker, stored, writes


class TestPropertiesAreRestored:
    def test_original_values_are_put_back(self) -> None:
        original = {ical.CalendarColor.tag: "#123456FF", ical.CalendarOrder.tag: "7"}
        checker, stored, _ = _make_checker(dict(original))
        CheckCalendarProperties(checker)._run_check()
        assert stored == original

    def test_probe_still_reports_full_on_a_conformant_server(self) -> None:
        checker, _, _ = _make_checker({ical.CalendarColor.tag: "#123456FF", ical.CalendarOrder.tag: "7"})
        CheckCalendarProperties(checker)._run_check()
        assert checker._features_checked.is_supported("calendar-order") is True

    def test_a_calendar_with_no_such_properties_keeps_no_probe_value(self) -> None:
        """Nothing was set before, so no probe value may be left behind.

        Clearing a property means PROPPATCHing an empty value, so "absent" and
        "empty" are both acceptable end states; a probe colour is not.
        """
        checker, stored, _ = _make_checker({})
        CheckCalendarProperties(checker)._run_check()
        assert all(v in (None, "") for v in stored.values()), stored
        assert "green" not in stored.values() and "34" not in stored.values()

    def test_restore_happens_even_when_the_server_errors_mid_probe(self) -> None:
        from caldav.lib.error import DAVError

        original = {ical.CalendarColor.tag: "#123456FF", ical.CalendarOrder.tag: "7"}
        stored = dict(original)
        calls = {"n": 0}

        def set_properties(elements):
            calls["n"] += 1
            if calls["n"] == 2:
                raise DAVError("403 Forbidden")
            for el in elements:
                stored[el.tag] = el.value

        cal = Mock()
        cal.set_properties.side_effect = set_properties
        cal.get_properties.side_effect = lambda els: {el.tag: stored.get(el.tag) for el in els}

        checker = Mock()
        checker._features_checked = FeatureSet()
        checker.debug_mode = None
        checker._client_obj = Mock()
        checker.expected_features = FeatureSet()
        checker.calendar = cal

        CheckCalendarProperties(checker)._run_check()
        assert stored[ical.CalendarColor.tag] == original[ical.CalendarColor.tag]
