"""Whether a TZID from the tz database is understood without a VTIMEZONE.

RFC 5545 section 3.6.5 requires a VTIMEZONE for every TZID, but most servers
place a bare TZID=Asia/Tokyo right; Synology's web UI was reported to read
it as UTC (https://github.com/python-caldav/caldav/issues/849).  The probe
judges the server's reading by time-range searches.
"""

import re
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from caldav.compatibility_hints import FeatureSet
from caldav.lib.error import AuthorizationError, DAVError

from caldav_server_tester.checks import CheckOlsonTzid

from .test_floating_event import RewritingServer

BASE = "http://example.com/cal/"
FEATURE = CheckOlsonTzid.FEATURE
VTIMEZONE = "BEGIN:VTIMEZONE\r\nTZID:Asia/Tokyo\r\nEND:VTIMEZONE\r\n"


def _search(server, offset, inclusive=False, zoned=9):
    """A time-range search reading each stored DTSTART as ``offset`` hours
    ahead of UTC, or ``zoned`` hours where the object carries a VTIMEZONE, the
    event lasting an hour; ``inclusive`` finds it in a range starting at its
    end"""

    def search(start, end, **kwargs):
        found = []
        for body in server.objects.values():
            event = body[body.index("BEGIN:VEVENT") :]
            stamp = re.search(r"\r\nDTSTART[^:]*:(\d{8}T\d{6})", event)[1]
            hours = zoned if "BEGIN:VTIMEZONE" in body else offset
            begin = datetime.strptime(stamp, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc) - timedelta(hours=hours)
            stop = begin + timedelta(hours=1)
            if (start <= stop if inclusive else start < stop) and end > begin:
                found.append(Mock(component={"UID": re.search(r"\r\nUID:(.*)\r\n", body)[1]}))
        return found

    return search


def _run(server, offset=9, time_range="full", search=None):
    checker = Mock()
    checker._features_checked = FeatureSet({"search.time-range.event": {"support": time_range}})
    checker.features_checked = checker._features_checked
    checker.debug_mode = None
    checker._client_obj = server
    checker.fixture_base_year = 2027
    checker.calendar.url.join.side_effect = lambda name: BASE + name
    checker.calendar.search.side_effect = search or _search(server, offset)
    CheckOlsonTzid(checker)._run_check()
    return checker._features_checked.is_supported(FEATURE, return_type=dict)


def test_the_probe_sends_a_bare_tzid():
    sent = []
    _run(RewritingServer(rewrite=lambda body: sent.append(body) or body))
    assert re.search(r"\r\nDTSTART;TZID=Asia/Tokyo:\d{8}T\d{6}\r\n", sent[0])
    assert "VTIMEZONE" not in sent[0]


def test_placed_in_tokyo_is_full():
    server = RewritingServer()
    assert _run(server)["support"] == "full"
    assert not server.objects


def test_a_vtimezone_added_by_the_server_is_noted():
    verdict = _run(RewritingServer(rewrite=lambda body: body.replace("BEGIN:VEVENT", VTIMEZONE + "BEGIN:VEVENT")))
    assert verdict["support"] == "full"
    assert "adds a VTIMEZONE" in verdict["behaviour"]


def test_a_range_starting_at_the_end_does_not_confuse_it():
    """DAViCal and SOGo find an event in a range starting at its end"""
    server = RewritingServer()
    assert _run(server, search=_search(server, 9, inclusive=True))["support"] == "full"


def test_read_in_the_servers_own_zone_is_unsupported():
    """A TZID read as floating, on a server running in Berlin time"""
    verdict = _run(RewritingServer(), offset=1)
    assert verdict["support"] == "unsupported"
    assert "neither" in verdict["behaviour"]


@pytest.mark.parametrize("offset", [13, 14, -12])
def test_read_in_a_zone_far_from_utc_is_unsupported(offset):
    """Read as floating in Auckland in January, say: the day before in UTC"""
    verdict = _run(RewritingServer(), offset=offset)
    assert verdict["support"] == "unsupported"
    assert "neither" in verdict["behaviour"]


def test_an_unparsable_search_result_is_passed_over():
    class Broken:
        @property
        def component(self):
            raise ValueError("no data")

    server = RewritingServer()
    search = _search(server, 9)
    assert _run(server, search=lambda **kwargs: [Broken(), *search(**kwargs)])["support"] == "full"


def test_read_as_utc_is_unsupported():
    """Synology, reportedly"""
    verdict = _run(RewritingServer(), offset=0)
    assert verdict["support"] == "unsupported"
    assert "UTC" in verdict["behaviour"]


def test_placed_elsewhere_is_unsupported():
    verdict = _run(RewritingServer(), offset=-5)
    assert verdict["support"] == "unsupported"
    assert "neither" in verdict["behaviour"]


@pytest.mark.parametrize("answer", [400, 403, 500, AuthorizationError("403 Forbidden")])
def test_refused_is_ungraceful(answer):
    assert _run(RewritingServer(put_answer=answer))["support"] == "ungraceful"


def test_without_time_range_search_it_is_unknown():
    server = RewritingServer()
    assert _run(server, time_range="unsupported")["support"] == "unknown"
    assert not server.objects


def test_a_search_ignoring_the_range_is_unknown():
    """Found in both windows, there is nothing to tell from"""
    found = [Mock(component={"UID": CheckOlsonTzid.UID})]
    assert _run(RewritingServer(), search=lambda **kwargs: found)["support"] == "unknown"


def test_a_failing_search_is_unknown():
    server = RewritingServer()
    assert _run(server, search=AuthorizationError("403 Forbidden"))["support"] == "unknown"
    assert not server.objects


def test_not_found_at_all_is_unknown():
    """A search index lagging behind the PUT"""
    assert _run(RewritingServer(), search=lambda **kwargs: [])["support"] == "unknown"


def test_a_search_misplacing_zoned_times_too_is_unknown():
    """Xandikos: once its index answers, a time lands in the server's zone,
    VTIMEZONE or not, so the search cannot tell what the TZID is read as"""
    server = RewritingServer()
    verdict = _run(server, search=_search(server, 0, zoned=0))
    assert verdict["support"] == "unknown"
    assert "VTIMEZONE" in verdict["behaviour"]
    assert not server.objects


def test_the_control_event_carries_a_vtimezone_and_is_removed():
    sent = []
    server = RewritingServer(rewrite=lambda body: sent.append(body) or body)
    assert _run(server, offset=0)["support"] == "unsupported"
    control = [body for body in sent if "BEGIN:VTIMEZONE" in body]
    assert len(control) == 1
    assert "\r\nTZID:Asia/Tokyo\r\n" in control[0]
    assert re.search(r"\r\nDTSTART;TZID=Asia/Tokyo:\d{8}T120000\r\n", control[0])
    assert not server.objects


def test_no_control_event_where_the_bare_tzid_is_placed_right():
    sent = []
    _run(RewritingServer(rewrite=lambda body: sent.append(body) or body))
    assert len(sent) == 1


def test_a_control_that_cannot_be_stored_is_unknown():
    """Without the control, a misplaced bare TZID may still be the search's doing"""

    class RefusingControl(RewritingServer):
        def put(self, url, body, headers=None):
            if CheckOlsonTzid.CONTROL_UID in url:
                return Mock(status=403, headers={})
            return super().put(url, body, headers)

    server = RefusingControl()
    verdict = _run(server, offset=0)
    assert verdict["support"] == "unknown"
    assert "could not be stored" in verdict["behaviour"]
    assert not server.objects


def test_the_control_is_removed_when_its_search_fails():
    server = RewritingServer()
    search = _search(server, 0)

    def failing(**kwargs):
        if any(CheckOlsonTzid.CONTROL_UID in url for url in server.objects):
            raise DAVError("500 Internal Server Error")
        return search(**kwargs)

    assert _run(server, search=failing)["support"] == "unknown"
    assert not server.objects
