"""How long the library is told to wait after the colour/order PROPPATCHes.

The delay CheckProppatchDelay measured is one sample of a delay that varies,
and the configured one can be stale as well; waiting too little makes a
property look read-only.  So the wait is the larger of twice the measured
delay and the configured one.
"""

from unittest.mock import Mock

import pytest
from caldav.compatibility_hints import FeatureSet

from caldav_server_tester.checks import CheckCalendarProperties


def waits_seen(measured: int | None, configured: int | None) -> set:
    """Run the colour/order probes; return the delays the library was given."""
    seen = set()
    stored: dict = {}
    cal = Mock()
    cal.client.features = FeatureSet()

    def set_properties(elements):
        node = cal.client.features.is_supported("synchronous-write.proppatch", dict)
        seen.add(node.get("delay"))
        for el in elements:
            stored[el.tag] = el.value

    cal.set_properties.side_effect = set_properties
    cal.get_properties.side_effect = lambda elements: {el.tag: stored.get(el.tag) for el in elements}

    checker = Mock()
    checker._features_checked = FeatureSet()
    checker.features_checked = checker._features_checked
    checker.debug_mode = None
    checker.calendar = cal
    checker.expected_features = FeatureSet()
    if measured is not None:
        checker._features_checked.copyFeatureSet(
            {"synchronous-write.proppatch": {"support": "unsupported", "delay": measured}}, collapse=False
        )
    if configured is not None:
        checker.expected_features.copyFeatureSet(
            {"synchronous-write.proppatch": {"support": "unsupported", "delay": configured}}, collapse=False
        )
    CheckCalendarProperties(checker)._run_check()
    return seen


@pytest.mark.parametrize(
    ("measured", "configured", "expected"),
    [
        (2, 15, 15),  ## a fast sample must not undercut the configured delay
        (10, 15, 20),  ## twice the measured delay when that is longer
        (2, None, 4),
        (None, 15, 15),  ## configured, nothing measured
    ],
)
def test_wait_is_the_larger_of_twice_measured_and_configured(measured, configured, expected) -> None:
    assert waits_seen(measured, configured) == {expected}


def test_no_wait_when_neither_is_known() -> None:
    assert waits_seen(None, None) == {None}
