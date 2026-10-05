"""CheckSynchronousWrite - measuring the server's own write delay.

Servers like Infomaniak/SabreDAV process writes asynchronously: a PUT returns
before the object is readable back.  The library's answer is `synchronous-write`
declared unsupported with a `delay`, a flat sleep after every write - but that value has to be written
into a profile by hand, which means somebody has to measure it first, and until
they do the tester mis-probes the server rather than reporting it as slow.

This check makes the delay an observation.  It PUTs one object with the
configured delay suspended and times how long the object takes to become
readable by direct GET (never by search, which would re-measure `search-cache`),
then combines that with what the calendar create/delete probe measured.
"""

## DISCLAIMER: those tests are AI-generated, and haven't been reviewed

from unittest.mock import Mock

from caldav.compatibility_hints import FeatureSet
from caldav.lib.error import NotFoundError

from caldav_server_tester.checker import ServerQuirkChecker
from caldav_server_tester.checks import CheckSynchronousWrite, PrepareCalendar


def _checker(monkeypatch, configured=None) -> ServerQuirkChecker:
    import caldav_server_tester.checks as checks_mod

    monkeypatch.setattr(checks_mod.time, "sleep", lambda _s: None)
    client = Mock()
    features = FeatureSet()
    if configured is not None:
        features.copyFeatureSet({"synchronous-write": {"support": "unsupported", "delay": configured}}, collapse=False)
    client.features = features
    client.request = Mock(return_value="response")
    checker = ServerQuirkChecker(client, debug_mode=None)
    checker.principal = Mock()
    checker.calendar = Mock()
    checker._checks_run.add(PrepareCalendar)
    return checker


def _readback(monkeypatch, fail_times: int) -> dict:
    """Patch url_object so a direct GET 404s the first N times."""
    import caldav_server_tester.checks as checks_mod

    state = {"n": 0}

    def load(_self=None):
        state["n"] += 1
        if state["n"] <= fail_times:
            raise NotFoundError("not written yet")

    def url_object(cal, uid, obj_class=None):
        obj = Mock()
        obj.load.side_effect = load
        return obj

    monkeypatch.setattr(checks_mod, "url_object", url_object)
    return state


def _run(checker) -> dict:
    CheckSynchronousWrite(checker)._run_check()
    return checker.features_checked.is_supported("synchronous-write", dict)


class TestSaveLoadDelayMeasurement:
    def test_a_synchronous_server_costs_nothing_extra(self, monkeypatch) -> None:
        """The read-back is only polled when the first one fails."""
        checker = _checker(monkeypatch)
        state = _readback(monkeypatch, fail_times=0)

        observed = _run(checker)

        assert observed["support"] == "full"
        assert observed.get("save-load-delay") == 0
        assert state["n"] == 1  ## one GET, no polling

    def test_measures_the_save_load_delay(self, monkeypatch) -> None:
        checker = _checker(monkeypatch)
        _readback(monkeypatch, fail_times=4)

        observed = _run(checker)

        assert observed["save-load-delay"] == 4

    def test_an_object_that_never_appears_is_not_a_delay(self, monkeypatch) -> None:
        """A read-back that never succeeds says nothing about timing."""
        checker = _checker(monkeypatch)
        _readback(monkeypatch, fail_times=999)

        observed = _run(checker)

        assert observed["support"] == "unknown"

    def test_probing_suspends_the_configured_delay(self, monkeypatch) -> None:
        """Otherwise the sleep has already happened and the delay reads as 0."""
        checker = _checker(monkeypatch, configured=16)
        seen = []
        _readback(monkeypatch, fail_times=0)
        checker.calendar.save_object.side_effect = lambda *a, **kw: (
            seen.append(checker._client_obj._write_delay),
            Mock(),
        )[1]

        _run(checker)

        assert seen == [0]
        assert checker._client_obj._write_delay == 16


class TestAggregateVerdict:
    """Only a delayed object write makes writes asynchronous, and justifies a sleep."""

    def _with_calendar_delays(self, checker, create=None, delete=None) -> None:
        if create is not None:
            checker._features_checked.set_feature(
                "synchronous-write.create-calendar", {"support": "unsupported", "delay": create}
            )
        if delete is not None:
            checker._features_checked.set_feature(
                "synchronous-write.delete-calendar", {"support": "unsupported", "delay": delete}
            )

    def test_delays_on_creation_and_save_load_recommend_a_delay(self, monkeypatch) -> None:
        """The recommended sleep is the object write's; the calendar delays
        belong to create-calendar and delete-calendar, and every PUT sleeping
        as long as the slowest MKCALENDAR would be the over-prescription."""
        checker = _checker(monkeypatch)
        self._with_calendar_delays(checker, create=6, delete=4)
        _readback(monkeypatch, fail_times=3)

        observed = _run(checker)

        assert observed["support"] == "unsupported"
        assert observed["delay"] == 3
        assert "create-calendar takes ~6s" in observed["behaviour"]
        assert "consider configuring a delay" in observed["behaviour"]

    def test_a_delayed_save_load_alone_recommends_a_delay(self, monkeypatch) -> None:
        """An object not readable on return is what the post-write sleep is for."""
        checker = _checker(monkeypatch)
        _readback(monkeypatch, fail_times=3)

        observed = _run(checker)

        assert observed["support"] == "unsupported"
        assert observed["delay"] == 3

    def test_a_delayed_creation_alone_is_full(self, monkeypatch) -> None:
        """Asynchronous collection creation is create-calendar's to report.

        It carries its own delay, which the library polls for; grading object
        writes unsupported as well would report the one quirk twice - and
        Infomaniak flip-flopped between full and unsupported, depending on
        whether a run happened to catch the calendar delay.
        """
        checker = _checker(monkeypatch)
        self._with_calendar_delays(checker, create=6)
        _readback(monkeypatch, fail_times=0)

        observed = _run(checker)

        assert observed == {"support": "full", "save-load-delay": 0}

    def test_a_delayed_deletion_alone_is_full(self, monkeypatch) -> None:
        checker = _checker(monkeypatch)
        self._with_calendar_delays(checker, delete=4)
        _readback(monkeypatch, fail_times=0)

        observed = _run(checker)

        assert observed == {"support": "full", "save-load-delay": 0}

    def test_a_retried_deletion_is_not_asynchronous(self, monkeypatch) -> None:
        """A fragile delay is how long the request kept failing, not a queue.

        Cyrus answers 500 when a just-re-created calendar is deleted within the
        same second, and a retry after ~1s works.  Nothing was written late.
        """
        checker = _checker(monkeypatch)
        checker._features_checked.set_feature(
            "delete-calendar", {"support": "fragile", "behaviour": "answers 500 for ~1s", "delay": 1}
        )
        _readback(monkeypatch, fail_times=0)

        observed = _run(checker)

        assert observed["support"] == "full"
        assert "behaviour" not in observed

    def test_nothing_delayed_is_plain_full(self, monkeypatch) -> None:
        checker = _checker(monkeypatch)
        _readback(monkeypatch, fail_times=0)

        observed = _run(checker)

        assert observed["support"] == "full"
        assert "behaviour" not in observed


class TestConfiguredValueIsStillReported:
    def test_an_unprobeable_server_falls_back_to_the_profile(self, monkeypatch) -> None:
        """No calendar to write to - the configured value is the best we have."""
        checker = _checker(monkeypatch, configured=16)
        checker.calendar = None

        observed = _run(checker)

        assert observed["support"] == "unsupported"
        assert observed["delay"] == 16
        assert "not probed" in observed["note"]
