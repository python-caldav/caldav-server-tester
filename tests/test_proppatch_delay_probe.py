"""CheckProppatchDelay - measuring how long a PROPPATCH takes to show.

Infomaniak answers a PROPPATCH at once, but PROPFIND keeps returning the old
display name or colour for ~10s, while a PUT is readable immediately.  The
property probes then saw the old value and graded calendar-color and
calendar-order "broken" (read-only).  The library waits for such a server when
`synchronous-write.proppatch` carries a `delay`; this check is what measures it.
"""

## DISCLAIMER: those tests are AI-generated, and haven't been reviewed

from types import SimpleNamespace
from unittest.mock import Mock

from caldav.compatibility_hints import FeatureSet
from caldav.elements import dav
from caldav.lib.error import PropsetError

from caldav_server_tester.checker import ServerQuirkChecker
from caldav_server_tester.checks import CheckCalendarProperties, CheckProppatchDelay, PrepareCalendar

TAG = dav.DisplayName.tag


def _checker(monkeypatch, stale_reads: int, configured=None, read_time=0.0) -> tuple[ServerQuirkChecker, dict]:
    """A calendar answering the first ``stale_reads`` PROPFINDs after a
    PROPPATCH with the old display name, each PROPFIND taking ``read_time``
    seconds on a fake clock that sleeping advances too."""
    import caldav_server_tester.checks as checks_mod

    clock = {"now": 0.0}

    def sleep(seconds):
        clock["now"] += seconds

    monkeypatch.setattr(checks_mod, "time", SimpleNamespace(sleep=sleep, monotonic=lambda: clock["now"]))
    client = Mock()
    features = FeatureSet()
    if configured is not None:
        features.copyFeatureSet(
            {"synchronous-write.proppatch": {"support": "unsupported", "delay": configured}}, collapse=False
        )
    client.features = features
    client.request = Mock(return_value="response")
    checker = ServerQuirkChecker(client, debug_mode=None)
    checker.principal = Mock()
    checker._checks_run.add(PrepareCalendar)

    state = {"visible": "My calendar", "pending": None, "stale": 0, "reads": 0, "writes": [], "library": []}

    def set_properties(elements):
        state["library"].append(client.features.is_supported("synchronous-write.proppatch", dict))
        state["writes"].append((elements[0].value, client._write_delay))
        state["pending"] = elements[0].value
        state["stale"] = stale_reads

    def get_properties(elements):
        state["reads"] += 1
        clock["now"] += read_time
        if state["pending"] is not None:
            if state["stale"]:
                state["stale"] -= 1
            else:
                state["visible"], state["pending"] = state["pending"], None
        return {TAG: state["visible"]}

    cal = Mock()
    cal.client = client
    cal.set_properties.side_effect = set_properties
    cal.get_properties.side_effect = get_properties
    checker.calendar = cal
    return checker, state


def _run(checker) -> dict:
    ## run_check(), not _run_check(): it hands the library what has been
    ## observed so far, and asserts the declared feature was recorded - which
    ## a key unknown to caldav's FEATURES never is.
    CheckProppatchDelay(checker).run_check()
    return checker.features_checked.is_supported("synchronous-write.proppatch", dict)


class TestProppatchDelay:
    def test_a_synchronous_server_is_full(self, monkeypatch) -> None:
        checker, state = _checker(monkeypatch, stale_reads=0)
        observed = _run(checker)
        assert observed["support"] == "full"
        assert "delay" not in observed

    def test_latency_alone_is_not_a_delay(self, monkeypatch) -> None:
        """The first read-back takes time too; when it already shows the
        change, the server is synchronous however slow the network is."""
        checker, _ = _checker(monkeypatch, stale_reads=0, read_time=0.1)
        observed = _run(checker)
        assert observed["support"] == "full"

    def test_the_restore_gets_the_margin(self, monkeypatch) -> None:
        """One measurement of a varying delay is too tight a bound for the
        library's wait on the restore as well."""
        checker, state = _checker(monkeypatch, stale_reads=3)
        _run(checker)
        assert state["library"][-1]["delay"] == 6

    def test_measures_the_delay(self, monkeypatch) -> None:
        checker, _ = _checker(monkeypatch, stale_reads=4)
        observed = _run(checker)
        assert observed["support"] == "unsupported"
        assert observed["delay"] == 4

    def test_the_time_spent_on_requests_counts(self, monkeypatch) -> None:
        """Counting only the sleeps read Infomaniak's ~10s as 7."""
        checker, _ = _checker(monkeypatch, stale_reads=7, read_time=0.4)
        observed = _run(checker)
        assert observed["delay"] == 11  ## 7 sleeps and 8 PROPFINDs: 10.2s, rounded up

    def test_a_name_that_never_changes_is_not_a_delay(self, monkeypatch) -> None:
        checker, _ = _checker(monkeypatch, stale_reads=10_000)
        observed = _run(checker)
        assert observed["support"] == "unknown"

    def test_waits_longer_than_the_configured_delay(self, monkeypatch) -> None:
        """A 10s poll could never observe Infomaniak's ~10-11s."""
        checker, _ = _checker(monkeypatch, stale_reads=20, configured=15)
        observed = _run(checker)
        assert observed["delay"] == 20

    def test_a_rejected_proppatch_is_unknown(self, monkeypatch) -> None:
        checker, _ = _checker(monkeypatch, stale_reads=0)
        checker.calendar.set_properties.side_effect = PropsetError("403")
        observed = _run(checker)
        assert observed["support"] == "unknown"

    def test_the_display_name_is_restored(self, monkeypatch) -> None:
        checker, state = _checker(monkeypatch, stale_reads=3)
        _run(checker)
        assert state["writes"][-1][0] == "My calendar"

    def test_measuring_suspends_both_waits(self, monkeypatch) -> None:
        """Neither the sleep after every write nor the library's own poll may
        run while measuring, or the delay reads as 0.  The restore is an
        ordinary write again, waited for by the library."""
        checker, state = _checker(monkeypatch, stale_reads=2)
        checker._features_checked.copyFeatureSet(
            {"synchronous-write": {"support": "unsupported", "delay": 16}}, collapse=False
        )
        checker._client_obj._write_delay = 16
        _run(checker)
        assert state["writes"][0][1] == 0
        assert state["library"][0]["support"] == "full"
        assert state["library"][-1]["support"] == "unsupported"
        assert checker._client_obj._write_delay == 16


def test_property_probes_run_after_the_delay_is_known() -> None:
    """With the delay recorded, the library waits out each set_properties()
    of the colour and order probes, so they read back the new value."""
    assert CheckProppatchDelay in CheckCalendarProperties.depends_on


def test_property_probes_give_the_library_a_margin(monkeypatch) -> None:
    """One measurement of a varying delay is too tight a bound for the
    library's wait during the colour and order probes."""
    checker, state = _checker(monkeypatch, stale_reads=0)
    checker._features_checked.copyFeatureSet(
        {"synchronous-write.proppatch": {"support": "unsupported", "delay": 10}}, collapse=False
    )
    checker._client_obj.features = checker._features_checked
    check = CheckCalendarProperties(checker)
    check.expected_features = FeatureSet()
    check._run_check()
    assert {w["delay"] for w in state["library"]} == {20}
    assert checker._client_obj.features is checker._features_checked
