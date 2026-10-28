"""Whether a second object resource with a UID already in the calendar is refused.

RFC 4791 section 5.3.2.1 has the server refuse it with the CALDAV:no-uid-conflict
precondition.  Radicale answers 409, Xandikos and Stalwart 412, Cyrus 403 (which
the client raises rather than returns).  Bedework answers a plain PUT with 403
and the precondition, one carrying If-None-Match: * with a bare 412.
"""

from unittest.mock import Mock

import pytest
from caldav.compatibility_hints import FeatureSet
from caldav.lib.error import AuthorizationError, RateLimitError

from caldav_server_tester.checks import CheckNoUidConflict

BASE = "http://example.com/cal/"
CONFLICT = (
    b'<?xml version="1.0" encoding="utf-8"?>\n'
    b'<D:error xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">'
    b"<C:no-uid-conflict><D:href>/cal/csc_no_uid_conflict_1.ics</D:href></C:no-uid-conflict>"
    b"</D:error>"
)


class UidServer:
    """A collection that stores objects by URL and knows their UIDs.

    ``conflict`` is what a PUT of a UID held under another URL gets: a status
    (answered with ``body``), an exception to raise, or None to store it.
    ``create_status`` is the answer to the first object.
    """

    def __init__(
        self,
        conflict=409,
        body=CONFLICT,
        create_status=201,
        inm_conflict=None,
        inm_body=b"",
        stored_anyway=False,
        get_status=None,
    ):
        self.conflict = conflict
        self.body = body
        self.create_status = create_status
        ## The answer to a PUT carrying If-None-Match: *, where it differs
        self.inm_conflict = inm_conflict
        self.inm_body = inm_body
        ## Refuses the duplicate, yet stores it
        self.stored_anyway = stored_anyway
        ## Answers every GET with this, where given
        self.get_status = get_status
        self.objects = {}
        self.deleted = []
        self.headers = []

    def put(self, url, body, headers=None):
        headers = headers or {}
        self.headers.append(headers)
        uid = next(line[4:] for line in body.split("\r\n") if line.startswith("UID:"))
        if any(u == uid and other != url for other, u in self.objects.items()):
            conflict, body = self.conflict, self.body
            if self.inm_conflict is not None and "If-None-Match" in headers:
                conflict, body = self.inm_conflict, self.inm_body
            if isinstance(conflict, Exception):
                raise conflict
            if conflict is not None:
                if self.stored_anyway:
                    self.objects[url] = uid
                return self._response(conflict, body)
        elif self.create_status >= 300:
            return self._response(self.create_status, b"")
        self.objects[url] = uid
        return self._response(201, b"")

    def request(self, url, method="GET", *args, **kwargs):
        assert method == "GET"
        if self.get_status is not None:
            return self._response(self.get_status, b"")
        return self._response(200 if url in self.objects else 404, b"")

    @staticmethod
    def _response(status, raw):
        response = Mock()
        response.status = status
        response.headers = {}
        response.raw = raw
        return response

    def delete(self, url):
        self.deleted.append(url)
        self.objects.pop(url, None)
        return Mock(status=204)


def _run(server, features=None, feature="save.no-uid-conflict"):
    checker = Mock()
    checker._features_checked = FeatureSet()
    if features:
        checker._features_checked.copy_feature_set(features, collapse=False)
    checker.debug_mode = None
    checker._client_obj = server
    checker.fixture_base_year = 2027
    checker.calendar.url.join.side_effect = lambda name: BASE + name
    CheckNoUidConflict(checker)._run_check()
    return checker._features_checked.is_supported(feature, return_type=dict)


@pytest.mark.parametrize("status", [403, 409])
def test_refused_with_the_precondition_is_full(status):
    server = UidServer(conflict=status)
    assert _run(server) == {"support": "full"}
    assert server.objects == {}


def test_412_with_the_precondition_is_a_quirk():
    """Xandikos, Stalwart: recognisable by the body, but the RFC status is 403 or 409."""
    verdict = _run(UidServer(conflict=412))
    assert (verdict["support"], verdict["status"], verdict["precondition"]) == ("quirk", 412, True)


def test_forbidden_raised_by_the_client_is_full():
    """Cyrus: the client raises 403 before a response exists."""
    exc = AuthorizationError(
        url="403 Forbidden", reason="Forbidden", preconditions=["{urn:ietf:params:xml:ns:caldav}no-uid-conflict"]
    )
    assert _run(UidServer(conflict=exc)) == {"support": "full"}


def test_cyrus_scheduling_precondition_is_full():
    """Cyrus names RFC 6638's unique-scheduling-object-resource instead."""
    exc = AuthorizationError(
        url="403 Forbidden",
        reason="Forbidden",
        preconditions=["{urn:ietf:params:xml:ns:caldav}unique-scheduling-object-resource"],
    )
    assert _run(UidServer(conflict=exc)) == {"support": "full"}


def test_bare_refusal_is_a_quirk():
    """SOGo: nothing stored at the URL, and the same PUT with another UID goes through."""
    server = UidServer(conflict=409, body=b"")
    verdict = _run(server)
    assert (verdict["support"], verdict["status"], verdict["precondition"]) == ("quirk", 409, False)
    assert server.objects == {}


def test_bare_refusal_storing_the_object_anyway_is_ungraceful():
    verdict = _run(UidServer(conflict=409, body=b"", stored_anyway=True))
    assert verdict["support"] == "ungraceful"
    assert "GET" in verdict["behaviour"]


def test_bare_refusal_of_any_uid_is_ungraceful():
    """The same PUT with another UID is refused too: not about the UID."""
    server = UidServer(conflict=409, body=b"", create_status=201)
    put = server.put

    def put_refusing_other_uids(url, body, headers=None):
        if "-other" in body:
            server.headers.append(headers or {})
            return server._response(409, b"")
        return put(url, body, headers)

    server.put = put_refusing_other_uids
    verdict = _run(server)
    assert verdict["support"] == "ungraceful"
    assert "another UID" in verdict["behaviour"]


def test_bare_403_is_ungraceful():
    """A read-only calendar answers 403 as well: it may be anything."""
    exc = AuthorizationError(url="403 Forbidden", reason="Forbidden")
    server = UidServer(conflict=exc)
    assert _run(server)["support"] == "ungraceful"


def test_precondition_in_the_wrong_namespace_is_ungraceful():
    """Purelymail puts no-uid-conflict in the CardDAV namespace."""
    exc = AuthorizationError(
        url="403 Forbidden", reason="Forbidden", preconditions=["{urn:ietf:params:xml:ns:carddav}no-uid-conflict"]
    )
    assert _run(UidServer(conflict=exc))["support"] == "ungraceful"


def test_plain_put_grades_the_parent():
    server = UidServer()
    _run(server)
    assert "If-None-Match" not in server.headers[1]
    assert any(h.get("If-None-Match") == "*" for h in server.headers[2:])


def test_if_none_match_graded_apart():
    """Bedework: 403 with the precondition, but a bare 412 with If-None-Match: *."""
    exc = AuthorizationError(
        url="403 Forbidden",
        reason="Forbidden",
        preconditions=["{urn:ietf:params:xml:ns:caldav}no-uid-conflict"],
    )
    server = UidServer(conflict=exc, inm_conflict=412)
    assert _run(server) == {"support": "full"}
    verdict = _run(server, feature="save.no-uid-conflict.if-none-match")
    assert (verdict["support"], verdict["status"], verdict["precondition"]) == ("quirk", 412, False)


def test_if_none_match_same_answer():
    verdict = _run(UidServer(), feature="save.no-uid-conflict.if-none-match")
    assert verdict == {"support": "full"}


@pytest.mark.parametrize("support", ["broken", "unsupported", "unknown"])
def test_no_if_none_match_where_the_server_mishandles_it(support):
    """A 412 there could be about the header, not the UID."""
    server = UidServer()
    verdict = _run(server, {"save.if-none-match": {"support": support}}, feature="save.no-uid-conflict.if-none-match")
    assert all("If-None-Match" not in h for h in server.headers)
    ## Left unset, so it is what the parent says
    assert verdict == {"support": "full"}


def test_href_given():
    assert _run(UidServer(), feature="save.no-uid-conflict.href-given") == {"support": "full"}


def test_href_not_given():
    body = CONFLICT.replace(b"<D:href>/cal/csc_no_uid_conflict_1.ics</D:href>", b"")
    verdict = _run(UidServer(body=body), feature="save.no-uid-conflict.href-given")
    assert verdict["support"] == "unsupported"


def test_href_not_graded_without_the_precondition():
    verdict = _run(UidServer(body=b""), feature="save.no-uid-conflict.href-given")
    assert verdict["support"] == "unknown"


def test_leftover_second_object_deleted_before_the_first_put():
    server = UidServer()
    _run(server)
    assert server.deleted[0] == BASE + "csc_no_uid_conflict_2.ics"


def test_both_objects_deleted_when_the_first_put_fails():
    server = UidServer(create_status=500)
    _run(server)
    assert {BASE + "csc_no_uid_conflict_1.ics", BASE + "csc_no_uid_conflict_2.ics"} <= set(server.deleted)


def test_probe_objects_say_which_probe_made_them():
    bodies = []
    server = UidServer()
    put = server.put
    server.put = lambda url, body, headers=None: bodies.append(body) or put(url, body, headers)
    _run(server)
    assert all("no-uid-conflict probe" in body for body in bodies)


def test_second_object_with_the_same_uid_stored_is_unsupported():
    server = UidServer(conflict=None)
    verdict = _run(server)
    assert verdict["support"] == "unsupported"
    assert "same UID" in verdict["behaviour"]
    assert server.objects == {}


def test_server_failure_is_ungraceful():
    verdict = _run(UidServer(conflict=500, body=b""))
    assert verdict["support"] == "ungraceful"
    assert "500" in verdict["behaviour"]


@pytest.mark.parametrize(
    "answer",
    [ConnectionError("redirected nowhere"), 503, RateLimitError(reason="429 Too Many Requests")],
    ids=["no-answer", "overloaded", "rate-limited"],
)
def test_no_answer_is_not_graded(answer):
    server = UidServer(conflict=answer, body=b"")
    assert _run(server)["support"] == "unknown"
    assert server.objects == {}


def test_first_object_that_cannot_be_created_grades_nothing():
    server = UidServer(create_status=500)
    assert _run(server)["support"] == "unknown"


def test_no_answer_to_the_get_is_not_graded():
    server = UidServer(conflict=409, body=b"", get_status=503)
    assert _run(server)["support"] == "unknown"


def test_no_answer_to_the_other_uid_is_not_graded():
    server = UidServer(conflict=409, body=b"")
    put = server.put

    def put_overloaded_for_other_uids(url, body, headers=None):
        if "-other" in body:
            server.headers.append(headers or {})
            return server._response(503, b"")
        return put(url, body, headers)

    server.put = put_overloaded_for_other_uids
    assert _run(server)["support"] == "unknown"


def test_href_in_a_raised_403():
    """Cyrus: the 403 is raised by the client, the href is in its body."""
    exc = AuthorizationError(
        url="403 Forbidden",
        reason="Forbidden",
        preconditions=["{urn:ietf:params:xml:ns:caldav}no-uid-conflict"],
        body=CONFLICT,
    )
    assert _run(UidServer(conflict=exc), feature="save.no-uid-conflict.href-given") == {"support": "full"}


def test_if_none_match_probe_cleans_up():
    """Bedework-like: the INM probe's PUT with another UID is deleted too."""
    exc = AuthorizationError(
        url="403 Forbidden",
        reason="Forbidden",
        preconditions=["{urn:ietf:params:xml:ns:caldav}no-uid-conflict"],
    )
    server = UidServer(conflict=exc, inm_conflict=412)
    _run(server)
    assert server.objects == {}
