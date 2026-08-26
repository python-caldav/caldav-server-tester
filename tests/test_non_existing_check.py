"""Unit tests for CheckNonExistingResource.

The check has to keep two things apart that servers do not necessarily treat
alike: a missing calendar *object* and a missing calendar *collection*.  Robur
answers 403 for both, but ``CalendarObjectResource.load()`` retries a failed GET
as a calendar-multiget REPORT against the parent calendar, where Robur reports
the missing href with an inner 404 - so the object lookup ends in
``NotFoundError`` and only the collection lookup surfaces the 403.

The object probe therefore asks twice: once with ``multiget_fallback=False``,
which is what the server itself answered, and - if that was not a 404 - once
through the fallback, to find out whether the caller nevertheless gets the
``NotFoundError`` it expects.  Rescued is a quirk; not rescued is unsupported.
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

## "no value given" - None already means "raises nothing at all"
_SAME = object()

OBJECT_FEATURE = "non-existing-raises-not-found.object"
COLLECTION_FEATURE = "non-existing-raises-not-found.collection"


def _checker(
    object_raises=None,
    object_raises_through_fallback=_SAME,
    collection_raises=None,
    auto_create=False,
):
    """A checker whose missing-object and missing-calendar lookups raise to order.

    ``object_raises`` is what a raw lookup (``multiget_fallback=False``) does;
    ``object_raises_through_fallback`` is what a plain ``load()`` does, and
    defaults to the same thing - a server with no rescue in play.
    """
    if object_raises_through_fallback is _SAME:
        object_raises_through_fallback = object_raises
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

    def _load(only_if_unloaded=False, multiget_fallback=True):
        raises = object_raises if multiget_fallback is False else object_raises_through_fallback
        if raises is not None:
            raise raises
        return missing_object

    missing_object.load.side_effect = _load
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

    def test_rescued_by_the_multiget_fallback_is_a_quirk(self, monkeypatch) -> None:
        """The Robur shape: 403 from the server, NotFoundError out of the library.

        The caller gets the exception it expects, so nothing downstream has to
        care - but the profile must not claim the server answers 404, because
        it does not, and the next thing that stops going through load() would
        be surprised.
        """
        features, _, _ = _run(
            monkeypatch,
            object_raises=AuthorizationError("403"),
            object_raises_through_fallback=NotFoundError("inner 404 from the multiget"),
        )
        assert features.is_supported(OBJECT_FEATURE, str) == "quirk"
        behaviour = features.is_supported(OBJECT_FEATURE, dict)["behaviour"]
        assert "AuthorizationError" in behaviour
        assert "multiget" in behaviour

    def test_the_raw_lookup_is_what_decides_full(self, monkeypatch) -> None:
        """A server answering 404 itself is not probed a second time."""
        checker, url_object, _ = _checker(object_raises=NotFoundError("nope"))
        monkeypatch.setattr(checks_mod, "url_object", url_object)
        CheckNonExistingResource(checker).run_check()
        assert url_object.return_value.load.call_count == 1

    def test_no_error_through_the_fallback_is_broken(self, monkeypatch) -> None:
        features, _, _ = _run(
            monkeypatch,
            object_raises=AuthorizationError("403"),
            object_raises_through_fallback=None,
        )
        assert features.is_supported(OBJECT_FEATURE, str) == "broken"

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
            object_raises=AuthorizationError("403"),
            object_raises_through_fallback=NotFoundError("inner 404 from the multiget"),
            collection_raises=AuthorizationError("403"),
        )
        assert features.is_supported(OBJECT_FEATURE, str) == "quirk"
        assert features.is_supported(COLLECTION_FEATURE, str) == "unsupported"
        assert "AuthorizationError" in features.is_supported(COLLECTION_FEATURE, dict)["behaviour"]

    def test_a_4xx_daverror_is_unsupported(self, monkeypatch) -> None:
        """A server answering the wrong *client* error really is deviating."""
        features, _, _ = _run(
            monkeypatch,
            object_raises=NotFoundError("nope"),
            collection_raises=ReportError("403 Forbidden"),
        )
        assert features.is_supported(COLLECTION_FEATURE, str) == "unsupported"
        assert "ReportError" in features.is_supported(COLLECTION_FEATURE, dict)["behaviour"]

    def test_a_5xx_is_unknown_rather_than_unsupported(self, monkeypatch) -> None:
        """A 500 says the server broke, not that it answers the wrong error.

        This probe's verdict is copied into a server profile and read back as a
        statement about the server's error handling.  A momentary 503 must not
        be able to make that statement.
        """
        features, _, _ = _run(
            monkeypatch,
            object_raises=NotFoundError("nope"),
            collection_raises=ReportError("503 Service Unavailable"),
        )
        observed = features.is_supported(COLLECTION_FEATURE, dict)
        assert observed["support"] == "unknown"
        assert "503" in observed["behaviour"]

    def test_a_5xx_on_the_object_probe_is_unknown_too(self, monkeypatch) -> None:
        features, _, _ = _run(monkeypatch, object_raises=ReportError("500 Internal Server Error"))
        observed = features.is_supported(OBJECT_FEATURE, dict)
        assert observed["support"] == "unknown"
        assert "500" in observed["behaviour"]

    def test_no_error_at_all_is_broken(self, monkeypatch) -> None:
        features, _, _ = _run(monkeypatch, object_raises=NotFoundError("nope"))
        assert features.is_supported(COLLECTION_FEATURE, str) == "broken"

    def test_not_probed_when_auto_create_was_never_established(self, monkeypatch) -> None:
        """``is_supported()`` is False for "unknown" just as for "unsupported".

        If CheckMakeDeleteCalendar raised - a dropped connection, a 500 - then
        ``create-calendar.auto`` is unknown, and probing anyway would create the
        very calendar this check then reports as non-existing.
        """
        checker, url_object, _ = _checker(object_raises=NotFoundError("nope"))
        checker._features_checked.set_feature("create-calendar.auto", None)
        monkeypatch.setattr(checks_mod, "url_object", url_object)
        CheckNonExistingResource(checker).run_check()
        features = checker.features_checked
        observed = features.is_supported(COLLECTION_FEATURE, dict)
        assert observed["support"] == "unknown"
        assert "create-calendar.auto" in observed["behaviour"]
        checker.principal.calendar.assert_not_called()

    def test_not_probed_on_auto_creating_servers(self, monkeypatch) -> None:
        """Looking up a non-existing calendar on a server that auto-creates
        calendars would create the very thing we are probing for."""
        features, checker, _ = _run(monkeypatch, object_raises=NotFoundError("nope"), auto_create=True)
        assert features.is_supported(COLLECTION_FEATURE, str) == "unknown"
        checker.principal.calendar.assert_not_called()
        ## ...and the parent verdict still stands on its own
        assert features.is_supported(OBJECT_FEATURE, str) == "full"
