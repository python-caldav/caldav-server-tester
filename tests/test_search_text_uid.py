"""Unit tests for CheckSearch._probe_text_uid (search.text.uid).

A calendar-query with a UID filter works if it answers the one object
with that UID.  iCloud answers it with 412.
https://github.com/python-caldav/caldav/issues/783
"""

from unittest.mock import Mock

import pytest
from caldav.compatibility_hints import FeatureSet
from caldav.lib.error import AuthorizationError, DAVError, ReportError

from caldav_server_tester.checker import ServerQuirkChecker
from caldav_server_tester.checks import CheckSearch

pytestmark = pytest.mark.skipif(
    "search.text.uid" not in FeatureSet.FEATURES,
    reason="needs a caldav with the search.text.uid feature",
)


UID = "csc_event_with_categories"


def _event(uid: str = UID) -> Mock:
    event = Mock()
    event.icalendar_component = {"UID": uid}
    return event


def _probe(**search_result) -> tuple[CheckSearch, Mock]:
    cal = Mock()
    cal.search.configure_mock(**search_result)
    client = Mock()
    client.features = FeatureSet()
    checker = ServerQuirkChecker(client, debug_mode=None)
    checker.calendar = cal
    check = CheckSearch(checker)
    check._probe_text_uid()
    return check, cal


def _support(check: CheckSearch) -> str:
    return check.checker.features_checked.is_supported("search.text.uid", str)


def test_full_when_one_object_is_returned() -> None:
    check, cal = _probe(return_value=[_event()])
    assert _support(check) == "full"
    assert cal.search.call_args.kwargs["uid"] == "csc_event_with_categories"
    assert cal.search.call_args.kwargs["post_filter"] is False


def test_unsupported_when_the_filter_is_ignored() -> None:
    check, _ = _probe(return_value=[_event(), _event("other-1"), _event("other-2")])
    assert _support(check) == "unsupported"


def test_broken_when_another_object_is_returned() -> None:
    check, _ = _probe(return_value=[_event("some-other-uid")])
    assert _support(check) == "broken"


def test_broken_when_the_object_is_not_found() -> None:
    check, _ = _probe(return_value=[])
    assert _support(check) == "broken"


@pytest.mark.parametrize(
    "exc", [ReportError("412"), AuthorizationError("403"), DAVError("500")], ids=["412", "403", "500"]
)
def test_ungraceful_when_the_search_fails(exc: Exception) -> None:
    check, _ = _probe(side_effect=exc)
    assert _support(check) == "ungraceful"
