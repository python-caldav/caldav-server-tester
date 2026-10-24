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
    ``get_etag`` the one a GET answers with (defaults to ``put_etag``),
    ``accepts`` the If-Match etags a conditional PUT succeeds with,
    ``if_match_star``/``if_none_match_star`` a key of ``STAR``.  ``answers``
    overrides the reply to a ``(header, object exists)`` pair with a status, or
    raises it where it is an exception.
    """

    def __init__(
        self,
        put_etag='"tag-1"',
        get_etag=None,
        accepts=None,
        if_match_star="rfc",
        if_none_match_star="rfc",
        create_status=201,
        create_raises=None,
        answers=None,
        rewrite=None,
        rewrite_updates=None,
        raw_body=True,
    ):
        self.rewrite = rewrite
        self.rewrite_updates = rewrite_updates
        self.raw_body = raw_body
        self.bodies = {}
        self.put_etag = put_etag
        self.get_etag = put_etag if get_etag is None else get_etag
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
        rewrite = self.rewrite_updates if exists and self.rewrite_updates else self.rewrite
        self.bodies[url] = rewrite(body) if rewrite else body
        return self._response(204 if exists else 201)

    def _response(self, status):
        response = Mock()
        response.status = status
        response.headers = {"Etag": self.put_etag} if self.put_etag is not None and status < 300 else {}
        return response

    def request(self, url, method="GET"):
        response = Mock()
        response.status = 200 if url in self.objects else 404
        response.headers = {"Etag": self.get_etag} if response.status == 200 and self.get_etag else {}
        ## DAVResponse keeps the bytes as received, and turns CRLF into LF in .raw
        body = self.bodies.get(url, "")
        if self.raw_body:
            response._raw_body = body.encode()
        response.raw = body.replace("\r\n", "\n")
        return response

    def delete(self, url):
        self.deleted.append(url)
        self.objects.discard(url)
        return Mock(status=204)


def _run_all(server):
    checker = Mock()
    checker._features_checked = FeatureSet()
    checker.debug_mode = None
    checker._client_obj = server
    checker.fixture_base_year = 2027
    checker.calendar.url.join.side_effect = lambda name: BASE + name
    CheckPutEtag(checker)._run_check()
    return checker._features_checked


def _run(server):
    fs = _run_all(server)
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


def test_weak_etag_on_put_strong_on_get_is_a_quirk():
    """GMX: the PUT answers W/"..." while a GET of the same object gives a
    strong etag that If-Match accepts.  A client keeping the PUT etag has no
    lost-update protection until it fetches the object again."""
    etag, _ = _run(EtagServer(put_etag='W/"tag-1"', get_etag='"tag-1"', accepts={'"tag-1"'}))
    assert etag == {"support": "quirk", "behaviour": "weak on PUT, strong on GET"}


def test_weak_etag_on_put_strong_on_get_refused_too_stays_full():
    etag, _ = _run(EtagServer(put_etag='W/"tag-1"', get_etag='"tag-1"', accepts=set()))
    assert etag["support"] == "full"
    assert "weak" in etag["behaviour"]


def test_weak_etag_accepted_in_if_match_is_still_a_quirk():
    """A server comparing weakly takes W/"..." back, but the library never sends a
    weak etag, so a client is in the same spot as on GMX."""
    etag, _ = _run(EtagServer(put_etag='W/"tag-1"', get_etag='"tag-1"', accepts={'W/"tag-1"', '"tag-1"'}))
    assert etag == {"support": "quirk", "behaviour": "weak on PUT, strong on GET"}


def test_etag_on_get_differing_from_put_is_noted():
    """RFC 4791 section 5.3.4: a PUT etag means the stored object is the one sent."""
    etag, _ = _run(EtagServer(put_etag='"tag-1"', get_etag='"tag-2"'))
    assert etag["support"] == "full"
    assert "GET" in etag["behaviour"]


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


def _if_none_match(server):
    return _run_all(server).is_supported("save.if-none-match", return_type=dict)


def test_if_none_match_refusing_an_existing_object_is_full():
    assert _if_none_match(EtagServer()) == {"support": "full"}


@pytest.mark.parametrize("mode", ["literal", "ignored"], ids=["zimbra", "sogo"])
def test_if_none_match_overwriting_an_existing_object_is_unsupported(mode):
    """Zimbra compares "*" as an etag, SOGo ignores the header: either way
    If-None-Match: * never protects an existing object."""
    inm = _if_none_match(EtagServer(if_none_match_star=mode))
    assert inm["support"] == "unsupported"
    assert "If-None-Match: * overwrote an existing object" in inm["behaviour"]


def test_if_none_match_refusing_to_create_is_broken():
    """Sent on every no-overwrite save, it would then refuse every new object."""
    inm = _if_none_match(EtagServer(answers={("If-None-Match", False): 412}))
    assert inm["support"] == "broken"
    assert "If-None-Match: * refused to create a missing object" in inm["behaviour"]


def test_if_none_match_holding_backwards_is_broken():
    inm = _if_none_match(EtagServer(if_none_match_star="inverted"))
    assert inm["support"] == "broken"
    assert "overwrote" in inm["behaviour"]


@pytest.mark.parametrize("status", [403, 409, 500])
def test_if_none_match_refusing_an_existing_object_otherwise_than_412_is_ungraceful(status):
    inm = _if_none_match(EtagServer(answers={("If-None-Match", True): status}))
    assert inm["support"] == "ungraceful"
    assert str(status) in inm["behaviour"]


def test_if_none_match_failing_on_a_missing_object_is_ungraceful():
    inm = _if_none_match(EtagServer(answers={("If-None-Match", False): 500}))
    assert inm["support"] == "ungraceful"
    assert "500" in inm["behaviour"]


@pytest.mark.parametrize(
    "answer",
    [ConnectionError("redirected nowhere"), 503],
    ids=["no-answer", "overloaded"],
)
def test_if_none_match_without_an_answer_on_the_existing_object_is_not_graded(answer):
    inm = _if_none_match(EtagServer(answers={("If-None-Match", True): answer}))
    assert inm["support"] == "unknown"


def test_if_none_match_without_an_answer_on_the_missing_object_is_not_graded():
    """Graded full, the library would send it on every new object - which
    fails everywhere if that unprobed half refuses to create."""
    inm = _if_none_match(EtagServer(answers={("If-None-Match", False): ConnectionError("nowhere")}))
    assert inm["support"] == "unknown"
    assert "missing object" in inm["behaviour"]


def test_if_none_match_overwriting_without_an_answer_on_the_missing_object_is_unsupported():
    inm = _if_none_match(
        EtagServer(if_none_match_star="ignored", answers={("If-None-Match", False): ConnectionError("nowhere")})
    )
    assert inm["support"] == "unsupported"


def test_if_none_match_and_wildcard_use_separate_missing_objects():
    """An ignored If-Match: * creates its missing object; If-None-Match: *
    must not then find it there."""
    assert _if_none_match(EtagServer(if_match_star="ignored")) == {"support": "full"}


def test_missing_objects_deleted_when_the_existing_one_got_no_answer():
    server = EtagServer(answers={("If-None-Match", True): ConnectionError("nowhere")})
    _run_all(server)
    assert {BASE + "csc_put_etag_missing_1.ics", BASE + "csc_put_etag_missing_2.ics"} <= set(server.deleted)


def test_if_none_match_is_left_out_of_the_wildcard_verdict():
    """It has a feature of its own now."""
    _, wildcard = _run(EtagServer(if_match_star="literal", if_none_match_star="literal"))
    assert wildcard == {"support": "unsupported"}


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
    fs = _run_all(server)
    for feature in ("save.etag", "save-load.mutable.if-match-wildcard", "save.if-none-match"):
        assert fs.is_supported(feature, return_type=dict)["support"] == "unknown"
    assert server.requests == [None]


STORED = "save.etag.matches-stored"


def _add_dtstamp_last(body):
    """Radicale and Baikal add or move DTSTAMP; here it goes to the end."""
    lines = body.split("\r\n")
    dtstamp = next(line for line in lines if line.startswith("DTSTAMP"))
    lines.remove(dtstamp)
    lines.insert(lines.index("END:VEVENT"), dtstamp)
    return "\r\n".join(lines)


def test_stored_as_sent_is_full():
    assert _run_all(EtagServer()).is_supported(STORED, return_type=dict) == {"support": "full"}


def test_stored_differently_with_a_strong_etag_is_broken():
    """Radicale, Xandikos"""
    verdict = _run_all(EtagServer(rewrite=_add_dtstamp_last)).is_supported(STORED, return_type=dict)
    assert verdict == {
        "support": "broken",
        "behaviour": "stored with its properties reordered, yet the PUT answered with a strong ETag",
    }


def test_line_endings_rewritten_with_a_strong_etag_is_broken():
    verdict = _run_all(EtagServer(rewrite=lambda body: body.replace("\r\n", "\n"))).is_supported(
        STORED, return_type=dict
    )
    assert verdict["support"] == "broken"
    assert verdict["behaviour"].startswith("stored with other line endings")


def test_stored_with_lines_added_names_them():
    verdict = _run_all(
        EtagServer(rewrite=lambda body: body.replace("END:VEVENT", "CLASS:PUBLIC\r\nEND:VEVENT"))
    ).is_supported(STORED, return_type=dict)
    assert verdict["behaviour"].startswith("stored with CLASS added")


def test_stored_differently_without_an_etag_is_full():
    """Baikal leaves the ETag out when it rewrites."""
    verdict = _run_all(EtagServer(put_etag=None, get_etag='"g"', rewrite=_add_dtstamp_last)).is_supported(
        STORED, return_type=dict
    )
    assert verdict["support"] == "full"


def test_stored_differently_with_a_weak_etag_is_full():
    verdict = _run_all(EtagServer(put_etag='W/"w"', rewrite=_add_dtstamp_last)).is_supported(STORED, return_type=dict)
    assert verdict["support"] == "full"


def test_line_endings_are_compared_on_the_bytes_received():
    """caldav's .raw has LF where the server sent CRLF; that is not a rewrite."""
    server = EtagServer(rewrite=lambda body: body.replace("\r\n", "\n"))
    assert _run_all(server).is_supported(STORED, return_type=dict)["support"] == "broken"


def test_without_the_bytes_received_line_endings_are_not_compared():
    """An older caldav keeps only .raw, with CRLF turned into LF"""
    server = EtagServer(raw_body=False, rewrite=lambda body: body.replace("\r\n", "\n"))
    assert _run_all(server).is_supported(STORED, return_type=dict) == {"support": "full"}


def test_without_the_bytes_received_a_rewrite_is_still_seen():
    verdict = _run_all(EtagServer(raw_body=False, rewrite=_add_dtstamp_last)).is_supported(STORED, return_type=dict)
    assert verdict["support"] == "broken"


def test_a_rewrite_on_update_only_is_not_blamed_on_the_create():
    """The probe PUTs the object again for the If-Match checks; what an update
    adds (LAST-MODIFIED, SEQUENCE) says nothing about the create's ETag"""
    server = EtagServer(rewrite_updates=_add_dtstamp_last)
    assert _run_all(server).is_supported(STORED, return_type=dict) == {"support": "full"}


def test_an_unusable_etag_is_not_asked_whether_it_matches():
    """Robur: the ETag is malformed, so whether it matches the stored object is moot."""
    fs = _run_all(EtagServer(put_etag="a22ccfb2985aed13f50b4991a110d32253fd99aa"))
    assert fs.is_supported(STORED, return_type=dict) == fs.is_supported("save.etag", return_type=dict)
    assert fs.is_supported(STORED, return_type=dict)["support"] == "broken"
