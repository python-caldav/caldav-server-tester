"""How CheckCalendarProperties grades calendar-color and calendar-color.name.

The de facto value format of calendar-color is hex, so calendar-color is
graded on a hex value.  Colour names are graded separately as
calendar-color.name, and a server that does not take them - whether it
rejects them, drops them or ignores them - is simply not supporting them.
"""

import re
from collections.abc import Callable
from unittest.mock import Mock

import pytest
from caldav.compatibility_hints import FeatureSet
from caldav.elements import ical
from caldav.lib.error import DAVError

from caldav_server_tester.checks import CheckCalendarProperties

HEX = re.compile(r"^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$")


def _grade(store_color: Callable[[dict, str], None], original: str | None = "#CEE7FFFF") -> FeatureSet:
    """Run the probe against a server whose colour handling is `store_color`.

    The calendar starts with colour `original` (None: no colour at all), and
    has to end up with it again.
    """
    stored = {ical.CalendarOrder.tag: "7"}
    if original is not None:
        stored[ical.CalendarColor.tag] = original

    def set_properties(elements):
        for el in elements:
            if el.tag == ical.CalendarColor.tag and el.value:
                store_color(stored, el.value)
            else:
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
    assert stored.get(ical.CalendarColor.tag) in ((None, "") if original is None else (original,))
    return checker._features_checked


def _support(features: FeatureSet, feature: str) -> str:
    return features.is_supported(feature, dict)["support"]


def _verbatim(stored, value):
    stored[ical.CalendarColor.tag] = value


def _normalising(stored, value):
    names = {"blue": "#0000FFFF", "green": "#008000FF"}
    stored[ical.CalendarColor.tag] = names.get(value, value)


def _hex_only(stored, value):
    if not HEX.match(value):
        raise DAVError("403 Forbidden")
    stored[ical.CalendarColor.tag] = value


def _names_ignored(stored, value):
    ## OX App Suite: a colour name is accepted, but nothing changes
    if HEX.match(value):
        stored[ical.CalendarColor.tag] = value


def _read_only(stored, value):
    pass


ORIGINALS = pytest.mark.parametrize("original", ["#CEE7FFFF", None])


@ORIGINALS
def test_verbatim_server_supports_both(original: str | None) -> None:
    features = _grade(_verbatim, original)
    assert _support(features, "calendar-color") == "full"
    assert _support(features, "calendar-color.name") == "full"


@ORIGINALS
def test_normalising_server_supports_both(original: str | None) -> None:
    features = _grade(_normalising, original)
    assert _support(features, "calendar-color") == "full"
    assert _support(features, "calendar-color.name") == "full"
    assert "#0000FFFF" in features.is_supported("calendar-color.name", dict)["behaviour"]


@ORIGINALS
def test_server_rejecting_names_supports_calendar_color(original: str | None) -> None:
    features = _grade(_hex_only, original)
    assert _support(features, "calendar-color") == "full"
    assert _support(features, "calendar-color.name") == "unsupported"


@ORIGINALS
def test_server_ignoring_names_supports_calendar_color(original: str | None) -> None:
    ## with no original colour the ignored names read back as nothing
    ## ("dropped"), otherwise as the unchanged colour ("read-only")
    features = _grade(_names_ignored, original)
    assert _support(features, "calendar-color") == "full"
    assert _support(features, "calendar-color.name") == "unsupported"


def test_read_only_colour_is_broken() -> None:
    ## needs a colour to start with: a read-only empty property cannot be
    ## told apart from a dropped one
    features = _grade(_read_only)
    assert _support(features, "calendar-color") == "broken"
    assert _support(features, "calendar-color.name") == "unsupported"
