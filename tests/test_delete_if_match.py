"""Whether a DELETE honours If-Match.

RFC 9110 section 13.1.1 has the server refuse a request whose If-Match etag
the object no longer has.  SOGo does so on PUT (412), but a DELETE with a
stale etag goes through and the object is gone.
"""

from unittest.mock import Mock

import pytest
from caldav.compatibility_hints import FeatureSet
from caldav.lib.error import AuthorizationError

from caldav_server_tester.checks import CheckDeleteIfMatch

BASE = "http://example.com/cal/"
FEATURE = "delete.if-match"


class VersionedServer:
    """A collection giving each stored version of an object a new etag.

    ``delete_if_match`` is how a conditional DELETE is evaluated: "rfc"
    (holds for the current etag only), "ignored" (always holds) or "never".
    ``stale_answer`` overrides the answer to a stale DELETE with a status, or
    raises it where it is an exception.  ``etags`` False leaves the ETag
    header out; ``bump`` False keeps the etag across versions.
    """

    def __init__(
        self,
        delete_if_match="rfc",
        stale_answer=None,
        etags=True,
        bump=True,
        create_status=201,
        missing_answer=404,
        missing_plain_answer=404,
    ):
        self.missing_answer = missing_answer
        self.missing_plain_answer = missing_plain_answer
        self.delete_if_match = delete_if_match
        self.stale_answer = stale_answer
        self.etags = etags
        self.bump = bump
        self.create_status = create_status
        self.versions = {}
        self.deletes = []

    def _etag(self, url):
        return f'"v{self.versions[url]}"'

    def put(self, url, body, headers=None):
        if self.create_status >= 300:
            return self._response(self.create_status)
        if url in self.versions:
            self.versions[url] += 1 if self.bump else 0
        else:
            self.versions[url] = 0
        return self._response(201, url)

    def request(self, url, method="GET"):
        if url not in self.versions:
            return self._response(404)
        return self._response(200, url)

    def delete(self, url, headers=None):
        etag = (headers or {}).get("If-Match")
        self.deletes.append(etag)
        if url not in self.versions:
            if etag is not None and isinstance(self.missing_answer, Exception):
                raise self.missing_answer
            return self._response(self.missing_answer if etag is not None else self.missing_plain_answer)
        if etag is not None:
            current = etag == self._etag(url)
            if not current and self.stale_answer is not None:
                if isinstance(self.stale_answer, Exception):
                    raise self.stale_answer
                return self._response(self.stale_answer)
            holds = {"rfc": current, "ignored": True, "never": False}[self.delete_if_match]
            if not holds:
                return self._response(412)
        del self.versions[url]
        return self._response(204)

    def _response(self, status, url=None):
        response = Mock()
        response.status = status
        response.headers = {"Etag": self._etag(url)} if url and self.etags else {}
        return response


def _run(server, feature=FEATURE):
    checker = Mock()
    checker._features_checked = FeatureSet()
    checker.debug_mode = None
    checker._client_obj = server
    checker.fixture_base_year = 2027
    checker.calendar.url.join.side_effect = lambda name: BASE + name
    CheckDeleteIfMatch(checker)._run_check()
    return checker._features_checked.is_supported(feature, return_type=dict)


def test_stale_refused_and_current_taken_is_full():
    server = VersionedServer()
    assert _run(server)["support"] == "full"
    ## The stale etag first, then the current one
    stale = server.deletes.index('"v0"')
    assert server.deletes[stale : stale + 2] == ['"v0"', '"v1"']
    assert not server.versions


def test_stale_if_match_deleting_is_broken():
    """SOGo"""
    server = VersionedServer(delete_if_match="ignored")
    verdict = _run(server)
    assert verdict["support"] == "broken"
    assert "stale If-Match deleted the object (204)" in verdict["behaviour"]


def test_current_if_match_refused_too_is_unsupported():
    server = VersionedServer(delete_if_match="never")
    verdict = _run(server)
    assert verdict["support"] == "unsupported"


@pytest.mark.parametrize("status", [400, 409, 500])
def test_stale_if_match_refused_without_412_is_ungraceful(status):
    verdict = _run(VersionedServer(stale_answer=status))
    assert verdict["support"] == "ungraceful"
    assert str(status) in verdict["behaviour"]


def test_stale_if_match_answered_403_is_ungraceful():
    verdict = _run(VersionedServer(stale_answer=AuthorizationError("403 Forbidden")))
    assert verdict["support"] == "ungraceful"


def test_no_etag_is_unknown():
    assert _run(VersionedServer(etags=False))["support"] == "unknown"


def test_an_etag_unchanged_by_an_update_is_unknown():
    assert _run(VersionedServer(bump=False))["support"] == "unknown"


def test_an_object_that_cannot_be_stored_is_unknown():
    assert _run(VersionedServer(create_status=403))["support"] == "unknown"


def test_the_object_is_deleted_whatever_the_verdict():
    server = VersionedServer(delete_if_match="never")
    _run(server)
    assert not server.versions


MISSING = "delete.missing.if-match"
PLAIN = "delete.missing"


def test_a_conditional_delete_of_a_missing_object_answered_404_is_full():
    assert _run(VersionedServer(), MISSING) == {"support": "full"}


def test_a_conditional_delete_of_a_missing_object_answered_412_is_unsupported():
    """Baikal, Robur"""
    verdict = _run(VersionedServer(missing_answer=412), MISSING)
    assert verdict["support"] == "unsupported"
    assert verdict["behaviour"] == "a DELETE with If-Match on a missing object answered 412"


def test_a_conditional_delete_of_a_missing_object_answered_otherwise_is_ungraceful():
    assert _run(VersionedServer(missing_answer=500), MISSING)["support"] == "ungraceful"


def test_the_missing_object_is_probed_whatever_the_stale_verdict():
    """It needs no etag, so a server without one still gets a verdict."""
    assert _run(VersionedServer(etags=False, missing_answer=412), MISSING)["support"] == "unsupported"


def test_a_conditional_delete_answered_as_the_plain_one_is_full():
    """RFC 9110 section 13.2.1: the precondition is ignored where the request
    would fail without it, so a 403 for both is right"""
    verdict = _run(VersionedServer(missing_plain_answer=403, missing_answer=403), MISSING)
    assert verdict == {"support": "full", "behaviour": "answered 403, as a plain DELETE of a missing object is"}


def test_a_conditional_delete_answered_412_where_the_plain_one_is_refused_is_unsupported():
    verdict = _run(VersionedServer(missing_plain_answer=403, missing_answer=412), MISSING)
    assert verdict["support"] == "unsupported"


def test_a_conditional_delete_is_not_graded_where_the_plain_one_succeeds():
    """With nothing to fail, the condition decides, and a 412 is right"""
    verdict = _run(VersionedServer(missing_plain_answer=204, missing_answer=412), MISSING)
    assert verdict["support"] == "unknown"


def test_a_plain_delete_of_a_missing_object_answered_404_is_full():
    assert _run(VersionedServer(missing_answer=412), PLAIN) == {"support": "full"}


@pytest.mark.parametrize("status", [204, 200])
def test_a_plain_delete_of_a_missing_object_succeeding_is_unsupported(status):
    verdict = _run(VersionedServer(missing_plain_answer=status), PLAIN)
    assert verdict == {"support": "unsupported", "behaviour": f"a DELETE of a missing object answered {status}"}


def test_a_plain_delete_of_a_missing_object_answered_otherwise_is_ungraceful():
    assert _run(VersionedServer(missing_plain_answer=403), PLAIN)["support"] == "ungraceful"
