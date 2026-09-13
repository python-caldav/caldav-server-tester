"""The ETag a PUT answers with, and whether If-Match takes it back.

RFC 9110 section 8.8.3 has an entity-tag be a quoted string, optionally
prefixed W/.  Bedework 5 percent-encodes the quotes in its PUT response
(``ETag: %22...%22``; a GET gives the quoted form) and refuses the encoded form
in If-Match with 412.  The CalDAV library decodes that shape, so ``save()``
hides it - the probe has to look at the raw PUT response.  Bedework also
answers ``If-Match: *`` with 412, where section 13.1.1 has it match any
existing representation.
"""

from unittest.mock import Mock
from urllib.parse import unquote

import pytest
from caldav.compatibility_hints import FeatureSet
from caldav.lib.error import AuthorizationError

from caldav_server_tester.checks import CheckPutEtag

URL = "http://example.com/cal/csc_put_etag.ics"


class EtagServer:
    """Answers PUT the ways the probe has to tell apart.

    ``put_etag`` is the ETag header a PUT answers with (None for no header),
    ``accepts`` the If-Match values a conditional PUT succeeds with, and
    ``wildcard`` whether ``If-Match: *`` is honoured.
    """

    def __init__(self, put_etag='"tag-1"', accepts=None, wildcard=True, create_status=201, create_raises=None):
        self.put_etag = put_etag
        self.accepts = {put_etag} if accepts is None else set(accepts)
        self.wildcard = wildcard
        self.create_status = create_status
        self.create_raises = create_raises
        self.requests = []
        self.deleted = []

    def put(self, url, body, headers=None):
        if_match = (headers or {}).get("If-Match")
        self.requests.append(if_match)
        if if_match is None:
            if self.create_raises:
                raise self.create_raises
            status = self.create_status
        elif if_match == "*":
            status = 204 if self.wildcard else 412
        else:
            status = 204 if if_match in self.accepts else 412
        response = Mock()
        response.status = status
        response.headers = {"Etag": self.put_etag} if self.put_etag is not None and status < 300 else {}
        return response

    def delete(self, url):
        self.deleted.append(url)
        return Mock(status=204)


def _run(server):
    checker = Mock()
    checker._features_checked = FeatureSet()
    checker.debug_mode = None
    checker._client_obj = server
    checker.fixture_base_year = 2027
    checker.calendar.url.join.return_value = URL
    CheckPutEtag(checker)._run_check()
    fs = checker._features_checked
    return (
        fs.is_supported("save.etag", return_type=dict),
        fs.is_supported("save-load.mutable.if-match-wildcard", return_type=dict),
    )


def test_quoted_etag_taken_back_is_full():
    server = EtagServer()
    etag, wildcard = _run(server)
    assert etag["support"] == "full"
    assert wildcard["support"] == "full"
    assert server.deleted == [URL]


def test_weak_etag_is_full():
    etag, _ = _run(EtagServer(put_etag='W/"tag-1"'))
    assert etag["support"] == "full"


def test_percent_encoded_etag_is_a_quirk():
    """The Bedework shape: encoded in the header, refused as sent, the decoded
    form accepted.  Not what RFC 9110 allows, but the CalDAV library decodes it,
    so a client gets a working etag - a quirk, not a breakage."""
    encoded = "%2220260912T213103Z-218e%22"
    server = EtagServer(put_etag=encoded, accepts={unquote(encoded)}, wildcard=False)
    etag, wildcard = _run(server)
    assert etag["support"] == "quirk"
    assert "percent-encoded" in etag["behaviour"]
    assert wildcard["support"] == "unsupported"
    assert server.deleted == [URL]


def test_percent_encoded_etag_refused_in_both_forms_says_so():
    encoded = "%22tag-1%22"
    etag, _ = _run(EtagServer(put_etag=encoded, accepts=set()))
    assert etag["support"] == "broken"
    assert "percent-encoded" in etag["behaviour"]
    assert "decoded" in etag["behaviour"]


def test_malformed_etag_is_broken():
    etag, _ = _run(EtagServer(put_etag="tag-1"))
    assert etag["support"] == "broken"
    assert "tag-1" in etag["behaviour"]


def test_valid_etag_refused_in_if_match_is_broken():
    etag, _ = _run(EtagServer(put_etag='"tag-1"', accepts=set()))
    assert etag["support"] == "broken"
    assert "412" in etag["behaviour"]


def test_weak_etag_refused_in_if_match_is_not_broken():
    """RFC 9110 section 13.1.1 compares If-Match strongly, so a weak etag can
    never match: the 412 is the server following the RFC, not refusing its own
    etag."""
    etag, _ = _run(EtagServer(put_etag='W/"tag-1"', accepts=set()))
    assert etag["support"] == "full"
    assert "weak" in etag["behaviour"]


class WildcardStatusServer(EtagServer):
    """Answers ``If-Match: *`` with ``status``, everything else as EtagServer."""

    def __init__(self, status):
        super().__init__()
        self.wildcard_status = status

    def put(self, url, body, headers=None):
        if (headers or {}).get("If-Match") == "*":
            self.requests.append("*")
            return Mock(status=self.wildcard_status, headers={})
        return super().put(url, body, headers)


def test_server_error_on_wildcard_is_not_graded():
    """A 5xx says the server was unwell, not that it refuses If-Match: *."""
    _, wildcard = _run(WildcardStatusServer(500))
    assert wildcard["support"] == "unknown"


def test_no_etag_header_is_not_graded():
    """RFC 9110 lets a PUT response leave the ETag out; nothing to grade."""
    etag, wildcard = _run(EtagServer(put_etag=None))
    assert etag["support"] == "unknown"
    assert wildcard["support"] == "full"


@pytest.mark.parametrize(
    "server",
    [EtagServer(create_status=500), EtagServer(create_raises=AuthorizationError())],
    ids=["refused", "raised"],
)
def test_object_that_cannot_be_created_grades_nothing(server):
    etag, wildcard = _run(server)
    assert etag["support"] == "unknown"
    assert wildcard["support"] == "unknown"
    assert server.requests == [None]
