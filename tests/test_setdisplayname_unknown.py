"""set-displayname must not be blamed when the display name is unreadable.

The probe decides whether a display name "stuck" by listing calendars and
comparing get_display_name().  If the server does not serve DAV:displayname at
all (propfind.displayname unsupported/broken) no match is possible, so the
probe concluded the name was dropped -- reporting create-calendar.set-displayname
as unsupported when the truth is simply that it could not be measured.
"""

from unittest.mock import Mock

from caldav.compatibility_hints import FeatureSet

from caldav_server_tester.checks import CheckMakeDeleteCalendar


def _checker() -> Mock:
    checker = Mock()
    checker._features_checked = FeatureSet()
    checker.features_checked = checker._features_checked
    checker.debug_mode = None
    checker._client_obj = Mock()
    checker._client_obj.features = FeatureSet()
    checker.expected_features = FeatureSet()
    checker._checks_run = set()
    return checker


class TestUnreadableDisplaynameIsNotBlamedOnSetDisplayname:
    def test_unsupported_propfind_displayname_yields_unknown(self) -> None:
        checker = _checker()
        checker._features_checked.set_feature("propfind.displayname", False)
        check = CheckMakeDeleteCalendar(checker)
        assert check._displayname_verdict_is_measurable() is False

    def test_broken_propfind_displayname_yields_unknown(self) -> None:
        checker = _checker()
        checker._features_checked.set_feature("propfind.displayname", {"support": "broken"})
        check = CheckMakeDeleteCalendar(checker)
        assert check._displayname_verdict_is_measurable() is False

    def test_working_propfind_displayname_is_measurable(self) -> None:
        checker = _checker()
        checker._features_checked.set_feature("propfind.displayname", True)
        check = CheckMakeDeleteCalendar(checker)
        assert check._displayname_verdict_is_measurable() is True
