"""The ETag a PUT answers with, and whether If-Match takes it back.

RFC 9110 section 8.8.3 has an entity-tag be a quoted string, optionally
prefixed W/.  Bedework 5 percent-encodes the quotes in its PUT response
(``ETag: %22...%22``; a GET gives the quoted form) and refuses the encoded form
in If-Match with 412.  The CalDAV library decodes that shape, so ``save()``
hides it - the probe has to look at the raw PUT response.

Section 13.1.1 has ``If-Match: *`` hold where the object exists and
``If-None-Match: *`` hold where it does not.  Bedework (3 and 5) has If-Match
backwards - 412 on an existing object, 201 on a missing one - and Zimbra
compares ``*`` as a literal etag, so If-Match: * never holds and
If-None-Match: * always does, overwriting an existing object.
"""

from unittest.mock import Mock
from urllib.parse import unquote

import pytest
from caldav.compatibility_hints import FeatureSet
from caldav.lib.error import AuthorizationError, RateLimitError

from caldav_server_tester.checks import CheckPutEtag

BASE = "http://example.com/cal/"
URL = BASE + "csc_put_etag.ics"

## How a server evaluates a "*" condition: whether it holds, by header and by
## whether the object exists.  "literal" compares "*" as an etag, which never
## matches; "ignored" performs the request whatever the condition says.
STAR = {
    "rfc": {"If-Match": lambda exists: exists, "If-None-Match": lambda exists: not exists},
    "literal": {"If-Match": lambda exists: False, "If-None-Match": lambda exists: True},
    "inverted": {"If-Match": lambda exists: not exists, "If-None-Match": lambda exists: exists},
    "ignored": {"If-Match": lambda exists: True, "If-None-Match": lambda exists: True},
}


class EtagServer:
    """A collection answering PUT the ways the probe has to tell apart.

    ``put_etag`` is the ETag header a PUT answers with (None for no header),
    ``accepts`` the If-Match etags a conditional PUT succeeds with,
    ``if_match_star``/``if_none_match_star`` a key of ``STAR``.  ``answers``
    overrides the reply to a ``(header, object exists)`` pair with a status, or
    raises it where it is an exception.
    """

    def __init__(
        self,
        put_etag='"tag-1"',
        accepts=None,
        if_match_star="rfc",
        if_none_match_star="rfc",
        create_status=201,
        create_raises=None,
        answers=None,
    ):
        self.put_etag = put_etag
        self.accepts = {put_etag} if accepts is None else set(accepts)
        self.if_match_star = if_match_star
        self.if_none_match_star = if_none_match_star
        self.create_status = create_status
        self.create_raises = create_raises
        self.answers = answers or {}
        self.objects = set()
        self.requests = []
        self.deleted = []

    def put(self, url, body, headers=None):
        headers = headers or {}
        header = next((h for h in ("If-Match", "If-None-Match") if h in headers), None)
        value = headers.get(header)
        exists = url in self.objects
        self.requests.append((header, value) if header else None)
        answer = self.answers.get((header, exists))
        if isinstance(answer, Exception):
            raise answer
        if answer is not None:
            return self._response(answer)
        if header is None:
            if self.create_raises:
                raise self.create_raises
            if self.create_status >= 300:
                return self._response(self.create_status)
            holds = True
        elif value == "*":
            mode = self.if_match_star if header == "If-Match" else self.if_none_match_star
            holds = STAR[mode][header](exists)
        else:
            holds = exists and value in self.accepts
        if not holds:
            return self._response(412)
        self.objects.add(url)
        return self._response(204 if exists else 201)

    def _response(self, status):
        response = Mock()
        response.status = status
        response.headers = {"Etag": self.put_etag} if self.put_etag is not None and status < 300 else {}
        return response

    def delete(self, url):
        self.deleted.append(url)
        self.objects.discard(url)
        return Mock(status=204)


def _run(server):
    checker = Mock()
    checker._features_checked = FeatureSet()
    checker.debug_mode = None
    checker._client_obj = server
    checker.fixture_base_year = 2027
    checker.calendar.url.join.side_effect = lambda name: BASE + name
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
    assert wildcard == {"support": "full"}
    assert URL in server.deleted


def test_every_probe_object_is_deleted_again():
    """Where a wildcard wrongly creates an object, that one is removed too."""
    server = EtagServer(if_match_star="ignored", if_none_match_star="ignored")
    _run(server)
    assert server.objects == set()


def test_weak_etag_is_full():
    etag, _ = _run(EtagServer(put_etag='W/"tag-1"'))
    assert etag["support"] == "full"


def test_percent_encoded_etag_is_a_quirk():
    """The Bedework shape: encoded in the header, refused as sent, the decoded
    form accepted.  Not what RFC 9110 allows, but the CalDAV library decodes it,
    so a client gets a working etag - a quirk, not a breakage."""
    encoded = "%2220260912T213103Z-218e%22"
    server = EtagServer(put_etag=encoded, accepts={unquote(encoded)}, if_match_star="inverted")
    etag, wildcard = _run(server)
    assert etag["support"] == "quirk"
    assert "percent-encoded" in etag["behaviour"]
    assert wildcard["support"] == "broken"
    assert server.objects == set()


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


def test_wildcard_holding_only_for_a_missing_object_is_broken():
    """Bedework: "update only if it exists" creates objects instead."""
    _, wildcard = _run(EtagServer(if_match_star="inverted"))
    assert wildcard["support"] == "broken"
    assert "missing object" in wildcard["behaviour"]


def test_wildcard_that_never_holds_is_unsupported():
    _, wildcard = _run(EtagServer(if_match_star="literal"))
    assert wildcard["support"] == "unsupported"


def test_wildcard_creating_a_missing_object_is_broken():
    _, wildcard = _run(EtagServer(if_match_star="ignored"))
    assert wildcard["support"] == "broken"
    assert "missing object" in wildcard["behaviour"]


def test_missing_object_refused_otherwise_than_412_is_noted():
    """Not performing the request is what counts; the status is only a note."""
    _, wildcard = _run(EtagServer(answers={("If-Match", False): 404}))
    assert wildcard["support"] == "full"
    assert "404" in wildcard["behaviour"]


def test_no_answer_for_the_missing_object_keeps_the_existing_verdict():
    _, wildcard = _run(EtagServer(answers={("If-Match", False): ConnectionError("redirected nowhere")}))
    assert wildcard["support"] == "full"


@pytest.mark.parametrize(
    "answer",
    [ConnectionError("redirected nowhere"), 503],
    ids=["no-answer", "overloaded"],
)
def test_never_holding_without_the_missing_object_says_it_could_not_tell(answer):
    """The missing object is what tells "never holds" from "holds backwards";
    without an answer there, unsupported may be understating a broken server."""
    _, wildcard = _run(EtagServer(if_match_star="literal", answers={("If-Match", False): answer}))
    assert wildcard["support"] == "unsupported"
    assert "missing object" in wildcard["behaviour"]


def test_if_none_match_overwriting_an_existing_object_is_noted():
    """Zimbra: "*" compared as an etag, so If-None-Match: * never protects."""
    _, wildcard = _run(EtagServer(if_match_star="literal", if_none_match_star="literal"))
    assert wildcard["support"] == "unsupported"
    assert "If-None-Match: * overwrote an existing object" in wildcard["behaviour"]


def test_if_none_match_refusing_to_create_is_noted():
    _, wildcard = _run(EtagServer(if_none_match_star="inverted"))
    assert wildcard["support"] == "full"
    assert "If-None-Match: * refused to create a missing object" in wildcard["behaviour"]


@pytest.mark.parametrize("status", [500, 501])
def test_server_failure_on_wildcard_is_ungraceful(status):
    """A 500 on a probe is a bug met, not a bad moment."""
    _, wildcard = _run(EtagServer(answers={("If-Match", True): status}))
    assert wildcard["support"] == "ungraceful"
    assert str(status) in wildcard["behaviour"]


def test_forbidden_on_wildcard_is_ungraceful():
    """The unconditional PUT went through a moment earlier, so a 403 refuses the condition."""
    _, wildcard = _run(EtagServer(answers={("If-Match", True): AuthorizationError("403 Forbidden")}))
    assert wildcard["support"] == "ungraceful"
    assert "403" in wildcard["behaviour"]


def test_unauthorized_on_wildcard_is_not_called_forbidden():
    """The client raises 401 and 403 alike, carrying the request URL rather
    than the status, so which of the two it was cannot be told."""
    _, wildcard = _run(EtagServer(answers={("If-Match", True): AuthorizationError(url=URL, reason="Unauthorized")}))
    assert wildcard["support"] == "ungraceful"
    assert "401/403" in wildcard["behaviour"]


@pytest.mark.parametrize(
    "answer",
    [502, 503, 504, RateLimitError(reason="429 Too Many Requests")],
    ids=["502", "503", "504", "rate-limited"],
)
def test_gateway_or_overload_on_wildcard_is_not_graded(answer):
    _, wildcard = _run(EtagServer(answers={("If-Match", True): answer}))
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
