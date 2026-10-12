"""Unit tests for the ``propfind.principal-depth-1`` probe.

RFC 3744 does not require a principal to be a collection, and a Depth: 1
PROPFIND on a non-collection has nothing to list.  GMX (Cosmo) answers it with
400 "Depth must be 0 for non-collection resources".  The caldav library's
"no calendar-home-set" fallback lists the principal at depth 1, so this decides
whether that fallback can work at all.  See
https://github.com/python-caldav/caldav/pull/755.
"""

from unittest.mock import Mock

import pytest
from caldav.compatibility_hints import FeatureSet
from caldav.lib.error import AuthorizationError, DAVError, NotFoundError

from caldav_server_tester.checker import ServerQuirkChecker
from caldav_server_tester.checks import CheckGetCurrentUserPrincipal, CheckPropfindAllprop

FEATURE = "propfind.principal-depth-1"
MULTISTATUS = b'<?xml version="1.0"?><D:multistatus xmlns:D="DAV:"><D:response><D:href>/p/</D:href><D:propstat><D:prop><D:resourcetype/></D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response></D:multistatus>'
COSMO_400 = b'<?xml version="1.0"?><D:error xmlns:D="DAV:"><cosmo:bad-request xmlns:cosmo="http://osafoundation.org/cosmo/DAV">Depth must be 0 for non-collection resources</cosmo:bad-request></D:error>'


def _response(status, raw):
    response = Mock()
    response.status = status
    response.raw = raw
    return response


def _run(depth1, depth0=None):
    """Run the check against a principal whose depth-1 PROPFIND gives ``depth1``.

    ``depth1`` (and ``depth0``) is a response, or an exception to raise.
    Depth-0 PROPFINDs succeed unless ``depth0`` is given.
    """
    client = Mock()
    client.features = FeatureSet()
    checker = ServerQuirkChecker(client, debug_mode=None)
    checker.principal = Mock()
    checker.principal.url = "https://dav.example.com/p/"

    def propfind(url, props=None, depth=0):
        outcome = depth1 if depth == 1 else (depth0 or _response(207, MULTISTATUS))
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    client.propfind.side_effect = propfind
    CheckPropfindAllprop(checker).run_check()
    return checker._features_checked


def test_principal_depth1_multistatus_is_full() -> None:
    features = _run(_response(207, MULTISTATUS))
    assert features.is_supported(FEATURE, str) == "full"
    assert features.is_supported("propfind", str) == "full"


def test_principal_depth1_400_is_unsupported() -> None:
    """The GMX case: a 400 for Depth: 1 on the principal."""
    features = _run(_response(400, COSMO_400))
    assert features.is_supported(FEATURE, str) == "unsupported"
    assert "400" in features.is_supported(FEATURE, dict)["behaviour"]
    ## The depth-0 features are unaffected
    assert features.is_supported("propfind", str) == "full"


@pytest.mark.parametrize("error", [DAVError("refused"), AuthorizationError("refused")])
def test_principal_depth1_error_is_unsupported(error) -> None:
    features = _run(error)
    assert features.is_supported(FEATURE, str) == "unsupported"


def test_principal_depth1_2xx_without_multistatus_is_unsupported() -> None:
    features = _run(_response(200, b"<html>login page</html>"))
    assert features.is_supported(FEATURE, str) == "unsupported"


def test_principal_depth1_unknown_when_principal_url_is_broken() -> None:
    """A 404 at depth 1 says nothing about depth 1 when depth 0 fails too.

    CheckGetCurrentUserPrincipal drops a principal that 404s, so this is
    graded unknown rather than unsupported.
    """
    features = _run(NotFoundError("gone"), depth0=NotFoundError("gone"))
    assert features.is_supported(FEATURE, str) == "unknown"


def test_principal_depth1_unknown_without_principal() -> None:
    client = Mock()
    client.features = FeatureSet()
    client.url = "https://dav.example.com/"
    client.propfind.return_value = _response(207, MULTISTATUS)
    checker = ServerQuirkChecker(client, debug_mode=None)
    checker.principal = None
    ## the principal lookup already ran and failed; don't let it run again
    checker._checks_run.add(CheckGetCurrentUserPrincipal)
    CheckPropfindAllprop(checker).run_check()
    assert checker._features_checked.is_supported(FEATURE, str) == "unknown"
