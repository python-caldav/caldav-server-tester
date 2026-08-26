"""Unit tests for CheckNonExistingResource.

The check has to keep two things apart that servers do not necessarily treat
alike: a missing calendar *object* and a missing calendar *collection*.  Robur
answers 403 for both, but ``CalendarObjectResource.load()`` retries a failed GET
as a calendar-multiget REPORT against the parent calendar, where Robur reports
the missing href with an inner 404 - so the object lookup ends in
``NotFoundError`` and only the collection lookup surfaces the 403.
"""

from unittest.mock import Mock

from caldav.compatibility_hints import FeatureSet
from caldav.lib.error import AuthorizationError, NotFoundError, ReportError

import caldav_server_tester.checks as checks_mod
from caldav_server_tester.checker import ServerQuirkChecker
from caldav_server_tester.checks import (
    CheckGetCurrentUserPrincipal,
    CheckMakeDeleteCalendar,
    CheckNonExistingResource,
    PrepareCalendar,
)

OBJECT_FEATURE = "non-existing-raises-not-found"
COLLECTION_FEATURE = "non-existing-raises-not-found.collection"


def _checker(object_raises=None, collection_raises=None, auto_create=False):
    """A checker whose missing-object and missing-calendar lookups raise to order."""
    client = Mock()
    client.features = FeatureSet()
    checker = ServerQuirkChecker(client, debug_mode=None)
    for dependency in (CheckGetCurrentUserPrincipal, CheckMakeDeleteCalendar, PrepareCalendar):
        checker._checks_run.add(dependency)
    if auto_create:
        checker._features_checked.set_feature("create-calendar.auto", True)

    checker.calendar = Mock()
    checker.principal = Mock()

    missing_object = Mock()
    missing_object.load.side_effect = object_raises
    monkeypatched = Mock(return_value=missing_object)

    missing_calendar = Mock()
    missing_calendar.get_events.side_effect = collection_raises
    checker.principal.calendar.return_value = missing_calendar

    return checker, monkeypatched, missing_calendar


def _run(monkeypatch, **kwargs):
    checker, url_object, missing_calendar = _checker(**kwargs)
    monkeypatch.setattr(checks_mod, "url_object", url_object)
    CheckNonExistingResource(checker).run_check()
    return checker.features_checked, checker, missing_calendar


class TestObjectProbe:
    def test_not_found_is_full(self, monkeypatch) -> None:
        features, _, _ = _run(monkeypatch, object_raises=NotFoundError("nope"))
        assert features.is_supported(OBJECT_FEATURE, str) == "full"

    def test_other_daverror_is_unsupported(self, monkeypatch) -> None:
        features, _, _ = _run(monkeypatch, object_raises=AuthorizationError("403"))
        assert features.is_supported(OBJECT_FEATURE, str) == "unsupported"
        assert "AuthorizationError" in features.is_supported(OBJECT_FEATURE, dict)["behaviour"]

    def test_no_error_at_all_is_broken(self, monkeypatch) -> None:
        features, _, _ = _run(monkeypatch, object_raises=None)
        assert features.is_supported(OBJECT_FEATURE, str) == "broken"


class TestCollectionProbe:
    def test_not_found_is_full(self, monkeypatch) -> None:
        features, _, _ = _run(
            monkeypatch,
            object_raises=NotFoundError("nope"),
            collection_raises=NotFoundError("nope"),
        )
        assert features.is_supported(COLLECTION_FEATURE, str) == "full"

    def test_robur_shape_is_recorded_on_the_subfeature_only(self, monkeypatch) -> None:
        """The whole point: an object lookup that 404s and a calendar lookup that
        403s must not collapse into one verdict."""
        features, _, _ = _run(
            monkeypatch,
            object_raises=NotFoundError("inner 404 from the multiget"),
            collection_raises=AuthorizationError("403"),
        )
        assert features.is_supported(OBJECT_FEATURE, str) == "full"
        assert features.is_supported(COLLECTION_FEATURE, str) == "unsupported"
        assert "AuthorizationError" in features.is_supported(COLLECTION_FEATURE, dict)["behaviour"]

    def test_report_error_is_also_unsupported(self, monkeypatch) -> None:
        features, _, _ = _run(
            monkeypatch,
            object_raises=NotFoundError("nope"),
            collection_raises=ReportError("500"),
        )
        assert features.is_supported(COLLECTION_FEATURE, str) == "unsupported"

    def test_no_error_at_all_is_broken(self, monkeypatch) -> None:
        features, _, _ = _run(monkeypatch, object_raises=NotFoundError("nope"))
        assert features.is_supported(COLLECTION_FEATURE, str) == "broken"

    def test_not_probed_on_auto_creating_servers(self, monkeypatch) -> None:
        """Looking up a non-existing calendar on a server that auto-creates
        calendars would create the very thing we are probing for."""
        features, checker, _ = _run(monkeypatch, object_raises=NotFoundError("nope"), auto_create=True)
        assert COLLECTION_FEATURE not in features.dotted_feature_set_list()
        checker.principal.calendar.assert_not_called()
        ## ...and the parent verdict still stands on its own
        assert features.is_supported(OBJECT_FEATURE, str) == "full"
