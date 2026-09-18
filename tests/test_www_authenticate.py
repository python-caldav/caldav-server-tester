"""Unit tests for the ``auth.www-authenticate`` probe.

RFC7235 section 3.1 requires a 401 to carry a ``WWW-Authenticate`` header
naming the scheme the client should use.  Yahoo Calendar answers a bare 401
with no such header, and the caldav library then never builds an auth object at
all: the supplied password is never transmitted, and the 401 reaches the caller
as an ``AuthorizationError`` indistinguishable from a rejected one.  See
https://github.com/python-caldav/caldav/issues/713.  A conformant challenge
naming only schemes the library has no implementation for - Negotiate, NTLM -
is the same dead end one step later, which is the second feature.

The probe is only reachable on a server one has already authenticated against -
on such a server the answer says which ``auth_type`` has to be pinned in the
profile, rather than left to negotiation.
"""

from unittest.mock import Mock

import pytest
from caldav.compatibility_hints import FeatureSet
from caldav.lib.http_sync import CaseInsensitiveDict
from caldav.lib.url import URL

from caldav_server_tester.checker import ServerQuirkChecker
from caldav_server_tester.checks import CheckWWWAuthenticate

URL_STR = "https://dav.example.com/dav/"
HEADER = "auth.www-authenticate"
SCHEME = "auth.www-authenticate.usable-scheme"


class _Response:
    def __init__(self, status_code, headers=None, case_insensitive=True) -> None:
        self.status_code = status_code
        ## A plain dict is how a foreign response object spells its headers -
        ## and the case the production CaseInsensitiveDict() wrapping exists
        ## for.  With the fake using one too, removing that wrapping would not
        ## fail any test.
        self.headers = CaseInsensitiveDict(headers or {}) if case_insensitive else dict(headers or {})


class _Session:
    """A session that answers every request with the same canned outcome.

    Doubles as the class the check instantiates: it constructs
    ``type(client.session)()``, so the outcome is handed over on the class and
    every instance made from it answers the same way.  ``calls`` and
    ``instances`` are class-level for the same reason - the instance the check
    made is not one the test holds a reference to.
    """

    outcome = None
    calls = []
    instances = 0
    closed = 0

    def __init__(self) -> None:
        self.trust_env = True
        type(self).instances += 1

    def request(self, method, url, **kwargs):
        type(self).calls.append((method, url, kwargs, self.trust_env))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome

    def close(self) -> None:
        type(self).closed += 1


def _client(outcome, **overrides) -> Mock:
    """A client with every attribute the probe reads spelled out.

    A bare ``Mock`` will not do: ``client.proxy`` would auto-create a truthy
    attribute and the probe would build a proxy dict out of it.  Each client
    gets a session class of its own, so one test cannot see another's calls.
    """
    client = Mock()
    client.features = FeatureSet()
    client.url = URL.objectify(URL_STR)
    client.proxy = None
    client.timeout = 42
    client.ssl_verify_cert = True
    client.ssl_cert = None
    client.headers = {"User-Agent": "python-caldav/3.3.1", "Accept": "*/*"}
    session_class = type("_ProbeSession", (_Session,), {"outcome": outcome, "calls": [], "instances": 0, "closed": 0})
    client.session = session_class()
    for key, value in overrides.items():
        setattr(client, key, value)
    return client


def _probe(outcome, **overrides):
    """Run the probe against a server answering with ``outcome``.

    Returns ``(checker, session_class)`` - the real ``ServerQuirkChecker`` the
    suite uses elsewhere rather than a hand-built stand-in for its internals,
    and the session class so a test can see what was actually sent.
    """
    client = _client(outcome, **overrides)
    checker = ServerQuirkChecker(client, debug_mode=None)
    CheckWWWAuthenticate(checker).run_check()
    return checker, type(client.session)


def _verdict(outcome, feature=HEADER, **overrides):
    checker, _ = _probe(outcome, **overrides)
    return checker.features_checked.is_supported(feature, dict)


class TestAConformantServer:
    def test_a_401_naming_a_scheme_is_full(self) -> None:
        assert _verdict(_Response(401, {"WWW-Authenticate": 'Basic realm="dav"'}))["support"] == "full"

    def test_the_challenge_is_reported(self) -> None:
        verdict = _verdict(_Response(401, {"WWW-Authenticate": 'Digest realm="dav", Basic realm="dav"'}))
        assert "Digest" in verdict["behaviour"]

    def test_the_header_is_found_whatever_its_case(self) -> None:
        """HTTP/2 lowercases every header name.  The fake's headers are a plain
        dict here, so this fails if the probe stops normalising the case."""
        verdict = _verdict(_Response(401, {"www-authenticate": "Basic"}, case_insensitive=False))
        assert verdict["support"] == "full"

    def test_a_usable_scheme_is_full(self) -> None:
        verdict = _verdict(_Response(401, {"WWW-Authenticate": 'Digest realm="dav"'}), feature=SCHEME)
        assert verdict["support"] == "full"
        assert verdict["behaviour"] == "offers digest"


class TestTheYahooCase:
    def test_a_401_without_the_header_is_unsupported(self) -> None:
        assert _verdict(_Response(401, {"Content-Type": "application/json"}))["support"] == "unsupported"

    def test_an_empty_header_counts_as_no_header(self) -> None:
        assert _verdict(_Response(401, {"WWW-Authenticate": ""}))["support"] == "unsupported"

    def test_no_header_leaves_the_scheme_question_unanswered(self) -> None:
        """There is no challenge to judge, so 'unsupported' would report a
        second violation the server did not commit."""
        assert _verdict(_Response(401, {}), feature=SCHEME)["support"] == "unknown"


class TestAChallengeWeCannotUse:
    @pytest.mark.parametrize("challenge", ["Negotiate", 'NTLM, Negotiate realm="corp"'])
    def test_a_scheme_the_library_lacks_is_unsupported(self, challenge) -> None:
        """A conformant challenge, and still a dead end: caldav raises
        NotImplementedError for anything but basic, digest and bearer."""
        assert _verdict(_Response(401, {"WWW-Authenticate": challenge}), feature=SCHEME)["support"] == "unsupported"

    def test_the_header_itself_is_still_full(self) -> None:
        assert _verdict(_Response(401, {"WWW-Authenticate": "Negotiate"}))["support"] == "full"

    def test_one_usable_scheme_among_several_is_enough(self) -> None:
        verdict = _verdict(_Response(401, {"WWW-Authenticate": "Negotiate, Basic"}), feature=SCHEME)
        assert verdict["support"] == "full"


class TestNoQuestionCouldBePut:
    @pytest.mark.parametrize("status", [200, 207, 403, 404, 405, 500])
    @pytest.mark.parametrize("feature", [HEADER, SCHEME])
    def test_anything_but_a_401_is_unknown(self, status, feature) -> None:
        """Only a 401 is an answer to this question.  A server that serves an
        unauthenticated PROPFIND, or refuses it some other way, never got
        around to naming a scheme, and grading that "unsupported" would report
        a violation nobody committed."""
        assert _verdict(_Response(status), feature=feature)["support"] == "unknown"

    def test_a_failed_request_is_unknown(self) -> None:
        verdict = _verdict(OSError("connection reset"))
        assert verdict["support"] == "unknown"
        assert "connection reset" in verdict["behaviour"]


class TestWhatIsSent:
    def _sent(self, **overrides):
        _, session_class = _probe(_Response(401, {"WWW-Authenticate": "Basic"}), **overrides)
        return session_class

    def test_the_probe_carries_no_credentials(self) -> None:
        """The whole point: the request has to be the unauthenticated one, or
        the server has no reason to answer 401 in the first place."""
        (method, url, kwargs, trust_env) = self._sent().calls[0]
        assert method == "PROPFIND"
        assert url == URL_STR
        assert kwargs["auth"] is None
        ## ...and trust_env off, or the HTTP library substitutes ~/.netrc
        assert trust_env is False

    def test_a_throwaway_session_is_used_and_closed(self) -> None:
        """Not the client's own: that one carries the cookies the server handed
        out during the authenticated connect, which would make the probe a
        logged-in request answered 207."""
        session_class = self._sent()
        assert session_class.instances == 2  ## the client's, plus the probe's
        assert session_class.closed == 1

    def test_the_connection_settings_are_honoured(self) -> None:
        """A self-signed test server would otherwise fail the probe rather than
        answer it."""
        kwargs = self._sent().calls[0][2]
        assert kwargs["verify"] is True
        assert kwargs["timeout"] == 42
        assert kwargs["cert"] is None

    def test_a_client_certificate_is_passed_on(self) -> None:
        kwargs = self._sent(ssl_cert=("client.pem", "client.key")).calls[0][2]
        assert kwargs["cert"] == ("client.pem", "client.key")

    def test_a_proxy_is_passed_on_keyed_by_scheme(self) -> None:
        kwargs = self._sent(proxy="http://proxy.example.com:8080").calls[0][2]
        assert kwargs["proxies"] == {"https": "http://proxy.example.com:8080"}

    def test_no_proxy_means_no_proxy_dict(self) -> None:
        assert self._sent().calls[0][2]["proxies"] is None

    def test_the_clients_own_headers_travel_with_the_probe(self) -> None:
        """A server that varies by User-Agent must answer the probe the same
        way it answers every other request of the run."""
        kwargs = self._sent().calls[0][2]
        assert kwargs["headers"]["User-Agent"] == "python-caldav/3.3.1"
        assert kwargs["headers"]["Depth"] == "0"
        assert "xml" in kwargs["headers"]["Content-Type"]

    def test_an_authorization_header_is_stripped(self) -> None:
        """A client configured with a hand-built Authorization header would
        otherwise make the 'unauthenticated' request an authenticated one."""
        headers = {"User-Agent": "x", "authorization": "Basic c2VjcmV0"}
        kwargs = self._sent(headers=headers).calls[0][2]
        assert not [k for k in kwargs["headers"] if k.lower() == "authorization"]

    def test_the_request_body_is_a_propfind(self) -> None:
        body = self._sent().calls[0][2]["data"]
        assert "propfind" in body
        assert "resourcetype" in body


class TestTheCheckMachinery:
    def test_run_check_declares_what_it_probes(self) -> None:
        """``Check.run_check`` asserts that a check probes exactly the features
        it declares, and a feature key with no FEATURES entry records nothing -
        so a probe that only ever passes when driven by ``_run_check`` directly
        would be a probe that never fires in a real run."""
        checker, _ = _probe(_Response(401, {"WWW-Authenticate": "Basic"}))
        recorded = checker.features_checked.dotted_feature_set_list()
        assert HEADER in recorded
        assert SCHEME in recorded
