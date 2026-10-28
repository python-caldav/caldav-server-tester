"""Whether the parameters of an ATTENDEE are stored as sent.

SOGo adds PARTSTAT=NEEDS-ACTION, which is RFC 5545's default and changes
nothing, and RSVP=TRUE, which is not: the default is FALSE.
"""

from unittest.mock import Mock

import pytest
from caldav.compatibility_hints import FeatureSet

from caldav_server_tester.checks import CheckAttendeeParams

BASE = "http://example.com/cal/"
FEATURE = "save-load.icalendar.attendee-params"


class RewritingServer:
    def __init__(self, rewrite=lambda line: line, put_status=201):
        self.rewrite = rewrite
        self.put_status = put_status
        self.objects = {}

    def put(self, url, body, headers=None):
        if self.put_status >= 300:
            return Mock(status=self.put_status, headers={})
        lines = [self.rewrite(line) if line.startswith("ATTENDEE") else line for line in body.split("\r\n")]
        self.objects[url] = "\r\n".join(line for line in lines if line is not None)
        return Mock(status=201, headers={})

    def request(self, url, method="GET"):
        if url not in self.objects:
            return Mock(status=404, headers={}, raw="")
        return Mock(status=200, headers={}, raw=self.objects[url])

    def delete(self, url, headers=None):
        self.objects.pop(url, None)
        return Mock(status=204)


def _sogo(line):
    params, address = line.split(":", 1)
    if "PARTSTAT" not in params:
        params += ";PARTSTAT=NEEDS-ACTION"
    return f"{params};RSVP=TRUE:{address}"


def _run(server):
    checker = Mock()
    checker._features_checked = FeatureSet()
    checker.debug_mode = None
    checker._client_obj = server
    checker.fixture_base_year = 2027
    checker.calendar.url.join.side_effect = lambda name: BASE + name
    CheckAttendeeParams(checker)._run_check()
    return checker._features_checked.is_supported(FEATURE, return_type=dict)


def test_stored_as_sent_is_full():
    server = RewritingServer()
    assert _run(server) == {"support": "full"}
    assert not server.objects


def test_the_probe_sends_no_organizer():
    """Attendees without an ORGANIZER: not a scheduling object resource."""
    sent = []
    server = RewritingServer()
    original = server.put
    server.put = lambda url, body, headers=None: sent.append(body) or original(url, body, headers)
    _run(server)
    assert "ATTENDEE" in sent[0]
    assert "ORGANIZER" not in sent[0]


def test_sogo_is_unsupported():
    verdict = _run(RewritingServer(rewrite=_sogo))
    assert verdict["support"] == "unsupported"
    assert verdict["behaviour"] == "RSVP=TRUE is added; PARTSTAT=NEEDS-ACTION is added"


@pytest.mark.parametrize(
    "added", ["PARTSTAT=NEEDS-ACTION", "ROLE=REQ-PARTICIPANT", "CUTYPE=INDIVIDUAL", "RSVP=FALSE", "SCHEDULE-STATUS=1.2"]
)
def test_an_added_default_is_full(added):
    def add(line):
        params, address = line.split(":", 1)
        return line if added.split("=")[0] in params else f"{params};{added}:{address}"

    verdict = _run(RewritingServer(rewrite=add))
    assert verdict["support"] == "full"
    assert added in verdict["behaviour"]


def test_a_dropped_parameter_is_unsupported():
    verdict = _run(RewritingServer(rewrite=lambda line: line.replace(";PARTSTAT=ACCEPTED", "")))
    assert verdict["support"] == "unsupported"
    assert verdict["behaviour"] == "PARTSTAT=ACCEPTED is dropped"


def test_a_changed_parameter_is_unsupported():
    verdict = _run(RewritingServer(rewrite=lambda line: line.replace("PARTSTAT=ACCEPTED", "PARTSTAT=NEEDS-ACTION")))
    assert verdict["support"] == "unsupported"
    assert verdict["behaviour"] == "PARTSTAT=ACCEPTED is changed to NEEDS-ACTION"


def test_a_mangled_name_is_unsupported():
    verdict = _run(RewritingServer(rewrite=lambda line: line.replace("Åse", "?se")))
    assert verdict["support"] == "unsupported"
    assert "CN=Åse is changed" in verdict["behaviour"]


def test_a_dropped_attendee_is_unsupported():
    verdict = _run(RewritingServer(rewrite=lambda line: None if "PARTSTAT" in line else line))
    assert verdict["support"] == "unsupported"
    assert "is dropped" in verdict["behaviour"]


def test_refused_is_ungraceful():
    assert _run(RewritingServer(put_status=400))["support"] == "ungraceful"
