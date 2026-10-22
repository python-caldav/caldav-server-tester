"""An exception VEVENT marked STATUS:CANCELLED, beside its master.

caldav's ``cancel()`` makes one of a single occurrence.  Zimbra turns it into
an EXDATE on the master instead.
"""

from unittest.mock import Mock

import icalendar
import pytest
from caldav.compatibility_hints import FeatureSet
from caldav.lib.error import DAVError, PutError

import caldav_server_tester.checks as checks
from caldav_server_tester.checks import CheckCancelledRecurrenceException

FEATURE = "save-load.event.recurrences.exception.cancelled"

## caldav 3.5.0, which CI runs, does not know the feature yet
pytestmark = pytest.mark.skipif(FEATURE not in FeatureSet.FEATURES, reason=f"caldav does not know {FEATURE}")


def _stored(data, transform):
    """What the server gives back for the PUT ``data``."""
    cal = icalendar.Calendar.from_ical(data)
    transform(cal)
    return cal


def _drop_exception(cal):
    cal.subcomponents[:] = [c for c in cal.subcomponents if "RECURRENCE-ID" not in c]


def _to_exdate(cal):
    [exc] = [c for c in cal.subcomponents if "RECURRENCE-ID" in c]
    _drop_exception(cal)
    cal.subcomponents[0].add("EXDATE", exc["RECURRENCE-ID"].dt)


def _drop_status(cal):
    [exc] = [c for c in cal.subcomponents if "RECURRENCE-ID" in c]
    exc.pop("STATUS")


def _floating(cal):
    """The server hands times back as floating (no zone)."""
    for comp in cal.subcomponents:
        for prop in ("RECURRENCE-ID", "EXDATE"):
            if prop in comp:
                dt = comp[prop].dt if prop == "RECURRENCE-ID" else comp[prop].dts[0].dt
                comp.pop(prop)
                comp.add(prop, dt.replace(tzinfo=None))


def _in_oslo(cal):
    """The server hands RECURRENCE-ID back in its own zone."""
    from zoneinfo import ZoneInfo

    [exc] = [c for c in cal.subcomponents if "RECURRENCE-ID" in c]
    rid = exc.pop("RECURRENCE-ID").dt
    exc.add("RECURRENCE-ID", rid.astimezone(ZoneInfo("Europe/Oslo")))


def _run(monkeypatch, transform=lambda cal: None, add_event=None, load=None, exception="full"):
    checker = Mock()
    checker._features_checked = FeatureSet({"save-load.event.recurrences.exception": {"support": exception}})
    checker.debug_mode = None
    if add_event is not None:
        checker.calendar.add_event.side_effect = add_event
    obj = Mock()

    def _load():
        if load is not None:
            raise load
        [data] = checker.calendar.add_event.call_args.args
        obj.icalendar_instance = _stored(data, transform)

    obj.load.side_effect = _load
    monkeypatch.setattr(checks, "url_object", lambda *a, **k: obj)
    CheckCancelledRecurrenceException(checker)._run_check()
    return checker, obj, checker._features_checked.is_supported(FEATURE, dict)


def test_stored_as_cancelled_is_full(monkeypatch):
    checker, obj, observed = _run(monkeypatch)
    assert observed["support"] == "full"
    [data] = checker.calendar.add_event.call_args.args
    assert "STATUS:CANCELLED" in data and "RECURRENCE-ID:" in data
    obj.delete.assert_called_once()


@pytest.mark.parametrize(
    "transform,behaviour",
    [
        (_to_exdate, "turned into an EXDATE"),
        (_drop_exception, "the cancelled exception is dropped"),
        (_drop_status, "comes back as no STATUS"),
    ],
    ids=["exdate", "dropped", "status-lost"],
)
def test_not_kept_is_unsupported(monkeypatch, transform, behaviour):
    _, _, observed = _run(monkeypatch, transform)
    assert observed["support"] == "unsupported"
    assert behaviour in observed["behaviour"]


@pytest.mark.parametrize("transform", [_floating, _in_oslo], ids=["floating", "tzid"])
def test_kept_in_another_spelling_is_full(monkeypatch, transform):
    _, _, observed = _run(monkeypatch, transform)
    assert observed["support"] == "full"


def test_exdate_in_floating_time(monkeypatch):
    _, _, observed = _run(monkeypatch, lambda cal: (_to_exdate(cal), _floating(cal)))
    assert observed["behaviour"] == "turned into an EXDATE"


def test_no_answer_is_unknown(monkeypatch):
    _, _, observed = _run(monkeypatch, add_event=DAVError("connection reset"))
    assert observed["support"] == "unknown"


def test_refused_is_ungraceful(monkeypatch):
    _, _, observed = _run(monkeypatch, add_event=PutError("400 Bad Request"))
    assert observed["support"] == "ungraceful"


def test_unreadable_is_ungraceful(monkeypatch):
    _, obj, observed = _run(monkeypatch, load=DAVError("500 Internal Server Error"))
    assert observed["support"] == "ungraceful"
    obj.delete.assert_called_once()


def test_skipped_without_exception_support(monkeypatch):
    checker, _, _ = _run(monkeypatch, exception="unsupported")
    checker.calendar.add_event.assert_not_called()
