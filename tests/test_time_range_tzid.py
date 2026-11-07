"""Whether a time-range search places an event with a TZID, and the
VTIMEZONE for it, at its time in UTC.

Infomaniak finds it only by a range also reaching its local time read as
UTC.
"""

import re
from unittest.mock import Mock

import pytest
from caldav.compatibility_hints import FeatureSet
from caldav.lib.error import DAVError

from caldav_server_tester.checks import CheckTimeRangeTzid

from .fake_time_range import fake_search
from .test_floating_event import RewritingServer

BASE = "http://example.com/cal/"
FEATURE = CheckTimeRangeTzid.FEATURE


def _run(server, zoned=9, mode="overlap", search=None, time_range="full"):
    checker = Mock()
    checker._features_checked = FeatureSet({"search.time-range.event": {"support": time_range}})
    checker.features_checked = checker._features_checked
    checker.debug_mode = None
    checker._client_obj = server
    checker.fixture_base_year = 2027
    checker.calendar.url.join.side_effect = lambda name: BASE + name
    checker.calendar.search.side_effect = search or fake_search(server, zoned=zoned, mode=mode)
    CheckTimeRangeTzid(checker)._run_check()
    return checker._features_checked.is_supported(FEATURE, return_type=dict)


def test_the_event_carries_its_vtimezone():
    sent = []
    _run(RewritingServer(rewrite=lambda body: sent.append(body) or body))
    assert "\r\nTZID:Asia/Tokyo\r\n" in sent[0]
    assert "DTSTART;TZID=Asia/Tokyo:" in sent[0]


def test_placed_right_is_full():
    server = RewritingServer()
    assert _run(server)["support"] == "full"
    assert not server.objects


def test_placed_right_by_its_start_only_is_full():
    """Robur matches the start only, which the windows contain"""
    assert _run(RewritingServer(), mode="dtstart")["support"] == "full"


def test_read_as_utc_is_unsupported():
    verdict = _run(RewritingServer(), zoned=0)
    assert verdict["support"] == "unsupported"
    assert "UTC" in verdict["behaviour"]


def test_placed_elsewhere_is_unsupported():
    verdict = _run(RewritingServer(), zoned=-5)
    assert verdict["support"] == "unsupported"
    assert "neither" in verdict["behaviour"]


def test_found_only_reaching_the_wall_time_is_broken():
    """Infomaniak"""
    server = RewritingServer()
    verdict = _run(server, mode="index")
    assert verdict["support"] == "broken"
    assert "read as UTC" in verdict["behaviour"]
    assert not server.objects


def test_not_found_at_all_is_unknown():
    assert _run(RewritingServer(), search=lambda **kwargs: [])["support"] == "unknown"


def test_a_search_ignoring_the_range_is_unknown():
    found = [Mock(component={"UID": CheckTimeRangeTzid.UID})]
    assert _run(RewritingServer(), search=lambda **kwargs: found)["support"] == "unknown"


def test_a_failing_search_is_unknown():
    server = RewritingServer()
    assert _run(server, search=DAVError("500 Internal Server Error"))["support"] == "unknown"
    assert not server.objects


def test_refused_is_unknown():
    assert _run(RewritingServer(put_answer=403))["support"] == "unknown"


def test_without_time_range_search_it_is_unknown():
    server = RewritingServer()
    assert _run(server, time_range="unsupported")["support"] == "unknown"
    assert not server.objects


def test_a_reference_in_utc_goes_along_and_is_removed():
    sent = []
    server = RewritingServer(rewrite=lambda body: sent.append(body) or body)
    assert _run(server)["support"] == "full"
    assert any(re.search(r"\r\nDTSTART:\d{8}T120000Z\r\n", body) for body in sent)
    assert not server.objects


def test_a_search_misplacing_utc_times_is_unknown():
    """Bedework 3: a search answering with other objects than the ones in
    the range says nothing about the TZID"""
    server = RewritingServer()
    search = fake_search(server)

    def misplacing(start, end, **kwargs):
        found = search(start=start, end=end, **kwargs)
        return [obj for obj in found if obj.component["UID"] != CheckTimeRangeTzid.REFERENCE_UID]

    verdict = _run(server, search=misplacing)
    assert verdict["support"] == "unknown"
    assert "12:00 UTC" in verdict["behaviour"]
    assert not server.objects


@pytest.mark.parametrize("zoned", [1, 5, 8, 10, -5])
def test_placed_in_another_zone_is_unsupported(zoned):
    """Read in another zone, as floating in the server's own, say: not 'full',
    nor Infomaniak's 'broken', however close to Tokyo or UTC"""
    verdict = _run(RewritingServer(), zoned=zoned)
    assert verdict["support"] == "unsupported"
    assert "neither" in verdict["behaviour"]


@pytest.mark.parametrize("zoned", [1, 5, 10])
def test_placed_in_another_zone_by_its_start_only_is_unsupported(zoned):
    verdict = _run(RewritingServer(), zoned=zoned, mode="dtstart")
    assert verdict["support"] == "unsupported"
