"""calendar-multiget for a missing href, and limits on big listings.

RFC 4791 section 7.9 gives every requested href an entry in the multistatus,
a 404 for a missing one; Baikal leaves it out.  Stalwart answers a multiget
or a Depth: 1 PROPFIND with at most WebDav.maxResults members and a 507 for
the collection, and a calendar-query matching more the same way.  The limits
are only probed with
CALDAV_SERVER_TESTER_LIMIT_OBJECTS set, since it takes that many objects.
"""

import re
from unittest.mock import Mock

import pytest
from caldav.compatibility_hints import FeatureSet
from caldav.lib.error import ResponseError
from caldav.lib.url import URL
from lxml import etree

from caldav_server_tester.checks import CheckMultiget

BASE = "http://example.com/cal/"
MISSING = "multiget.missing-href"
LIMIT = "multiget.limit"
PROPFIND = "propfind.limit"
SEARCH = "search.limit"
ENV = "CALDAV_SERVER_TESTER_LIMIT_OBJECTS"


class MultigetServer:
    """``omit_missing`` leaves a missing href out (Baikal); ``cap`` answers at
    most that many members, with a 507 for the collection unless
    ``cap_marker`` is False.  ``missing_as`` answers a missing href with that
    entry instead of a response-level 404: "propstat 404", "propstat 200" or a
    response-level status code.  ``query_status`` is the status of every
    calendar-query answer."""

    def __init__(
        self,
        omit_missing=False,
        cap=None,
        cap_marker=True,
        report_status=207,
        propfind_raises=None,
        missing_as=None,
        propfind_status=None,
        query_status=207,
    ):
        self.query_status = query_status
        self.propfind_status = propfind_status
        self.missing_as = missing_as
        self.propfind_raises = propfind_raises
        self.omit_missing = omit_missing
        self.cap = cap
        self.cap_marker = cap_marker
        self.report_status = report_status
        self.objects = set()
        self.puts = 0

    @staticmethod
    def _name(url):
        return url.rstrip("/").rsplit("/", 1)[-1]

    def put(self, url, body, headers=None):
        self.puts += 1
        self.objects.add(self._name(url))
        return Mock(status=201, headers={})

    def delete(self, url, headers=None):
        self.objects.discard(self._name(url))
        return Mock(status=204)

    def report(self, url, body, depth=0):
        if "calendar-query" in body:
            if self.query_status != 207:
                return Mock(status=self.query_status, tree=None, raw="")
            return self._answer([(n, None) for n in sorted(self.objects)])
        if self.report_status != 207:
            return Mock(status=self.report_status, tree=None, raw="")
        names = [self._name(h) for h in re.findall(r"<D:href>(.*?)</D:href>", body)]
        entries = [(n, None if n in self.objects else (self.missing_as or 404)) for n in names]
        if self.omit_missing:
            entries = [e for e in entries if e[1] is None]
        return self._answer(entries)

    def propfind(self, url, props=None, depth=0):
        """caldav parses the answer at once, and raises on the collection's 507"""
        answer = self.request(url, "PROPFIND")
        if b"507" in etree.tostring(answer.tree):
            raise ResponseError("HTTP/1.1 507 Insufficient Storage")
        return answer

    def request(self, url, method="GET", body="", headers=None):
        """The raw answer, left unparsed"""
        assert method == "PROPFIND"
        if self.propfind_raises:
            raise self.propfind_raises
        if self.propfind_status:
            return Mock(status=self.propfind_status, tree=None, raw="")
        ## Last, so that a cap drops it, as on Stalwart: its members, then a 507
        return self._answer([(n, None) for n in sorted(self.objects)] + [("", None)])

    def _answer(self, entries):
        truncated = self.cap is not None and len(entries) > self.cap
        if truncated:
            entries = entries[: self.cap]
        parts = []
        for name, status in entries:
            href = f"<D:href>/cal/{name}</D:href>"
            if isinstance(status, str):
                code = status.split()[1]
                parts.append(
                    f"<D:response>{href}<D:propstat><D:prop><D:getetag/></D:prop>"
                    f"<D:status>HTTP/1.1 {code} X</D:status></D:propstat></D:response>"
                )
            elif status:
                parts.append(f"<D:response>{href}<D:status>HTTP/1.1 {status} Not Found</D:status></D:response>")
            else:
                parts.append(
                    f'<D:response>{href}<D:propstat><D:prop><D:getetag>"x"</D:getetag></D:prop>'
                    "<D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response>"
                )
        if truncated and self.cap_marker:
            parts.append(
                "<D:response><D:href>/cal/</D:href><D:status>HTTP/1.1 507 Insufficient Storage</D:status></D:response>"
            )
        xml = '<?xml version="1.0"?><D:multistatus xmlns:D="DAV:">' + "".join(parts) + "</D:multistatus>"
        return Mock(status=207, tree=etree.fromstring(xml.encode()), raw=xml)


def _run(server, monkeypatch, objects=None):
    if objects is None:
        monkeypatch.delenv(ENV, raising=False)
    else:
        monkeypatch.setenv(ENV, str(objects))
    checker = Mock()
    checker._features_checked = FeatureSet()
    checker.debug_mode = None
    checker._client_obj = server
    checker.fixture_base_year = 2027
    checker.calendar.url = URL.objectify(BASE)
    CheckMultiget(checker)._run_check()
    fs = checker._features_checked
    return {f: fs.is_supported(f, return_type=dict) for f in (MISSING, LIMIT, PROPFIND, SEARCH)}


def test_a_404_entry_for_the_missing_href_is_full(monkeypatch):
    server = MultigetServer()
    assert _run(server, monkeypatch)[MISSING]["support"] == "full"
    assert not server.objects


def test_the_missing_href_left_out_is_unsupported(monkeypatch):
    """Baikal"""
    verdict = _run(MultigetServer(omit_missing=True), monkeypatch)[MISSING]
    assert verdict["support"] == "unsupported"
    assert verdict["behaviour"] == "a missing href is left out of the multistatus"


def test_a_404_inside_the_propstat_is_full(monkeypatch):
    assert _run(MultigetServer(missing_as="propstat 404"), monkeypatch)[MISSING] == {"support": "full"}


@pytest.mark.parametrize("answer", ["propstat 200", 200])
def test_a_missing_href_answered_as_if_it_existed_is_broken(monkeypatch, answer):
    verdict = _run(MultigetServer(missing_as=answer), monkeypatch)[MISSING]
    assert verdict["support"] == "broken"
    assert "as if it existed" in verdict["behaviour"]


def test_a_missing_href_answered_otherwise_names_the_status(monkeypatch):
    assert _run(MultigetServer(missing_as=410), monkeypatch)[MISSING] == {
        "support": "full",
        "behaviour": "a missing href is answered with 410",
    }


def test_a_failing_multiget_is_ungraceful(monkeypatch):
    assert _run(MultigetServer(report_status=400), monkeypatch)[MISSING]["support"] == "ungraceful"


def test_the_limits_are_not_probed_by_default(monkeypatch):
    server = MultigetServer()
    verdicts = _run(server, monkeypatch)
    assert server.puts == 1
    for feature in (LIMIT, PROPFIND, SEARCH):
        assert verdicts[feature]["support"] == "unknown"
        assert ENV in verdicts[feature]["behaviour"]


def test_no_limit_up_to_the_objects_stored(monkeypatch):
    server = MultigetServer()
    verdicts = _run(server, monkeypatch, objects=12)
    for feature in (LIMIT, PROPFIND, SEARCH):
        assert verdicts[feature] == {"support": "full", "behaviour": "no limit up to 12 objects"}
    assert not server.objects


def test_a_cap_with_a_507_is_unsupported(monkeypatch):
    """Stalwart"""
    verdicts = _run(MultigetServer(cap=5), monkeypatch, objects=12)
    assert verdicts[LIMIT] == {"support": "unsupported", "behaviour": "stops at 5 members, with a 507"}
    assert verdicts[PROPFIND] == {"support": "unsupported", "behaviour": "stops at 5 members, with a 507"}
    assert verdicts[SEARCH] == {"support": "unsupported", "behaviour": "stops at 5 members, with a 507"}


def test_a_propfind_cap_counts_every_member_listed(monkeypatch):
    """A server's cap counts the calendar's other members too"""
    server = MultigetServer(cap=5)
    server.objects.update({"aa_other_1.ics", "aa_other_2.ics"})  # listed first
    verdicts = _run(server, monkeypatch, objects=12)
    assert verdicts[PROPFIND] == {"support": "unsupported", "behaviour": "stops at 5 members, with a 507"}


def test_a_cap_without_a_507_is_broken(monkeypatch):
    verdicts = _run(MultigetServer(cap=5, cap_marker=False), monkeypatch, objects=12)
    assert verdicts[LIMIT]["support"] == "broken"
    assert verdicts[LIMIT]["behaviour"] == "lists 5 of 12 objects, without a 507"


@pytest.mark.parametrize("value", ["0", "", "many"])
def test_an_unusable_object_count_does_not_probe(monkeypatch, value):
    monkeypatch.setenv(ENV, value)
    server = MultigetServer()
    checker = Mock()
    checker._features_checked = FeatureSet()
    checker.debug_mode = None
    checker._client_obj = server
    checker.fixture_base_year = 2027
    checker.calendar.url = URL.objectify(BASE)
    CheckMultiget(checker)._run_check()
    assert server.puts == 1


def test_a_propfind_answered_507_as_a_whole_is_unsupported(monkeypatch):
    """No member listed at all, the HTTP status itself a 507"""
    verdict = _run(MultigetServer(propfind_status=507), monkeypatch, objects=12)[PROPFIND]
    assert verdict == {"support": "unsupported", "behaviour": "the whole answer was a 507, with 12 objects"}


def test_a_multiget_answered_507_as_a_whole_is_unsupported(monkeypatch):
    verdict = _run(MultigetServer(report_status=507), monkeypatch, objects=12)[LIMIT]
    assert verdict == {"support": "unsupported", "behaviour": "the whole answer was a 507, with 12 objects"}


def test_another_failure_is_ungraceful(monkeypatch):
    server = MultigetServer(propfind_raises=ResponseError("HTTP/1.1 500 Internal Server Error"))
    assert _run(server, monkeypatch, objects=12)[PROPFIND]["support"] == "ungraceful"


def test_a_calendar_query_answered_507_as_a_whole_is_unsupported(monkeypatch):
    verdict = _run(MultigetServer(query_status=507), monkeypatch, objects=12)[SEARCH]
    assert verdict == {"support": "unsupported", "behaviour": "the whole answer was a 507, with 12 objects"}


def test_a_failing_calendar_query_is_ungraceful(monkeypatch):
    assert _run(MultigetServer(query_status=500), monkeypatch, objects=12)[SEARCH]["support"] == "ungraceful"
