"""Whether a second object resource with a UID already in the calendar is refused.

RFC 4791 section 5.3.2.1 has the server refuse it with the CALDAV:no-uid-conflict
precondition.  Radicale answers 409, Xandikos and Stalwart 412, Cyrus 403 (which
the client raises rather than returns).
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

    def __init__(self, conflict=409, body=CONFLICT, create_status=201):
        self.conflict = conflict
        self.body = body
        self.create_status = create_status
        self.objects = {}
        self.deleted = []
        self.headers = []

    def put(self, url, body, headers=None):
        self.headers.append(headers or {})
        uid = next(line[4:] for line in body.split("\r\n") if line.startswith("UID:"))
        if any(u == uid and other != url for other, u in self.objects.items()):
            if isinstance(self.conflict, Exception):
                raise self.conflict
            if self.conflict is not None:
                return self._response(self.conflict, self.body)
        elif self.create_status >= 300:
            return self._response(self.create_status, b"")
        self.objects[url] = uid
        return self._response(201, b"")

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


def _run(server, features=None):
    checker = Mock()
    checker._features_checked = FeatureSet()
    if features:
        checker._features_checked.copy_feature_set(features, collapse=False)
    checker.debug_mode = None
    checker._client_obj = server
    checker.fixture_base_year = 2027
    checker.calendar.url.join.side_effect = lambda name: BASE + name
    CheckNoUidConflict(checker)._run_check()
    return checker._features_checked.is_supported("save.no-uid-conflict", return_type=dict)


@pytest.mark.parametrize("status", [403, 409, 412])
def test_refused_with_the_precondition_is_full(status):
    server = UidServer(conflict=status)
    assert _run(server) == {"support": "full"}
    assert server.objects == {}


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


def test_refused_without_the_precondition_is_ungraceful():
    """SOGo: the library cannot tell it from any other failed PUT."""
    verdict = _run(UidServer(conflict=409, body=b""))
    assert verdict["support"] == "ungraceful"
    assert "no-uid-conflict" in verdict["behaviour"]
    assert "409" in verdict["behaviour"]


def test_precondition_in_the_wrong_namespace_is_ungraceful():
    """Purelymail puts no-uid-conflict in the CardDAV namespace."""
    exc = AuthorizationError(
        url="403 Forbidden", reason="Forbidden", preconditions=["{urn:ietf:params:xml:ns:carddav}no-uid-conflict"]
    )
    assert _run(UidServer(conflict=exc))["support"] == "ungraceful"


def test_second_put_carries_if_none_match():
    """As save(no_overwrite=True) does: Bedework drops the precondition then."""
    server = UidServer()
    _run(server)
    assert server.headers[1].get("If-None-Match") == "*"


@pytest.mark.parametrize("support", ["broken", "unsupported", "unknown"])
def test_no_if_none_match_where_the_server_mishandles_it(support):
    """A 412 there could be about the header, not the UID."""
    server = UidServer()
    _run(server, {"save.if-none-match": {"support": support}})
    assert "If-None-Match" not in server.headers[1]


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
