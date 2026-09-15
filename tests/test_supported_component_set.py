"""A calendar created VTODO-only, and whether it really is one.

RFC 4791 section 5.2.3 lets a client hand MKCALENDAR a
CALDAV:supported-calendar-component-set, and section 5.3.1 has the server
answer 403 (CALDAV:supported-calendar-component-set) if it cannot do what was
asked.  Bedework 5 does neither: it answers the property "200 ok" and then
creates a VEVENT-only collection anyway, because a Bedework collection's
component set is a function of its internal calType, which CalDAV cannot set.
So the restriction has to be measured after the fact, from what the collection
advertises and from what it accepts.
"""

from unittest.mock import Mock

import pytest
from caldav.compatibility_hints import FeatureSet
from caldav.lib.error import PutError

import caldav_server_tester.checks as checks_mod
from caldav_server_tester.checker import ServerQuirkChecker
from caldav_server_tester.checks import CheckMakeDeleteCalendar, CheckSupportedComponentSet

FEATURE = "create-calendar.with-supported-component-types"


class ComponentSetServer:
    """A server that answers a component-restricted MKCALENDAR in one of the
    ways the four support levels describe.

    ``advertises`` is what the created collection reports for
    supported-calendar-component-set (``[]`` for a server that does not report
    the property at all), ``accepts_wrong_type`` whether a VEVENT may be saved
    to the collection the probe asked to be VTODO-only, and ``refuses`` whether
    the MKCALENDAR carrying the component set is refused outright.
    """

    def __init__(self, advertises=("VTODO",), accepts_wrong_type=False, refuses=None):
        self.advertises = list(advertises)
        self.accepts_wrong_type = accepts_wrong_type
        self.refuses = refuses
        self.created = []
        self.deleted = []
        self.saved = []

    def make_calendar(self, cal_id=None, supported_calendar_component_set=None, **kwargs):
        if self.refuses == "always" or (self.refuses == "with-component-set" and supported_calendar_component_set):
            raise checks_mod.DAVError("403 Forbidden")
        self.created.append((cal_id, supported_calendar_component_set))
        return self.calendar(cal_id=cal_id)

    def calendar(self, cal_id=None, name=None):
        cal = Mock()
        cal.cal_id = cal_id
        cal.url = f"/dav/user/{cal_id}/"
        cal.events.return_value = []
        cal.get_supported_components.return_value = list(self.advertises)
        cal.save_event.side_effect = self._save_event
        return cal

    def _save_event(self, *args, **kwargs):
        if not self.accepts_wrong_type:
            raise PutError("403 Forbidden")
        self.saved.append(kwargs.get("uid"))
        return Mock()

    def delete(self, cal):
        self.deleted.append(getattr(cal, "cal_id", None))


def make_check(monkeypatch, server, create_calendar="full"):
    monkeypatch.setattr(checks_mod.time, "sleep", lambda _s: None)
    client = Mock()
    client.features = FeatureSet()
    monkeypatch.setattr(checks_mod.DAVObject, "delete", server.delete)
    checker = ServerQuirkChecker(client, debug_mode=None)
    checker.principal = Mock()
    checker.principal.calendar.side_effect = server.calendar
    checker.principal.make_calendar.side_effect = server.make_calendar
    checker.principal.calendars.return_value = []
    checker.features_checked.set_feature("create-calendar", {"support": create_calendar})
    checker._checks_run.add(CheckMakeDeleteCalendar)
    probe = CheckSupportedComponentSet(checker)
    probe.expected_features = client.features
    return probe


def observed(check, as_type=dict):
    return check.checker.features_checked.is_supported(FEATURE, as_type)


def test_a_restriction_that_sticks_is_full(monkeypatch) -> None:
    check = make_check(monkeypatch, ComponentSetServer(advertises=["VTODO"], accepts_wrong_type=False))

    check._run_check()

    assert observed(check, str) == "full"


def test_a_restriction_that_is_ignored_is_unsupported(monkeypatch) -> None:
    """The Bedework 5 shape: the collection comes back VEVENT-only and takes a
    VEVENT quite happily."""
    server = ComponentSetServer(advertises=["VEVENT"], accepts_wrong_type=True)
    check = make_check(monkeypatch, server)

    check._run_check()

    assert observed(check, str) == "unsupported"
    assert "VTODO" in observed(check)["behaviour"]
    assert "VEVENT" in observed(check)["behaviour"]


def test_a_restriction_that_is_advertised_but_not_enforced_is_full(monkeypatch) -> None:
    """Advertising it is half the job and the feature says so ("advertises (or
    enforces)"), but a wrong-type object getting in is worth writing down."""
    check = make_check(monkeypatch, ComponentSetServer(advertises=["VTODO"], accepts_wrong_type=True))

    check._run_check()

    assert observed(check, str) == "full"
    assert "not enforced" in observed(check)["behaviour"]


def test_a_restriction_that_is_enforced_but_not_advertised_is_full(monkeypatch) -> None:
    """No property to read back - RFC 4791 section 5.2.3 makes it optional -
    but the collection refuses the wrong type, so the restriction is real."""
    check = make_check(monkeypatch, ComponentSetServer(advertises=[], accepts_wrong_type=False))

    check._run_check()

    assert observed(check, str) == "full"
    assert "not advertised" in observed(check)["behaviour"]


def test_a_creation_refusing_the_component_set_is_ungraceful(monkeypatch) -> None:
    check = make_check(monkeypatch, ComponentSetServer(refuses="with-component-set"))

    check._run_check()

    assert observed(check, str) == "ungraceful"


def test_a_creation_failing_either_way_says_nothing(monkeypatch) -> None:
    """Not evidence about the component set: this server is refusing to create
    calendars at all, which is create-calendar's verdict to give."""
    check = make_check(monkeypatch, ComponentSetServer(refuses="always"))

    check._run_check()

    assert observed(check, str) == "unknown"


def test_it_collapses_under_an_unsupported_create_calendar(monkeypatch) -> None:
    server = ComponentSetServer()
    check = make_check(monkeypatch, server, create_calendar="unsupported")

    check._run_check()

    assert FEATURE not in check.checker.features_checked._server_features
    assert server.created == []


@pytest.mark.parametrize("advertises,accepts", [(["VTODO"], False), (["VEVENT"], True), ([], False)])
def test_the_probe_calendar_is_deleted_again(monkeypatch, advertises, accepts) -> None:
    server = ComponentSetServer(advertises=advertises, accepts_wrong_type=accepts)
    check = make_check(monkeypatch, server)

    check._run_check()

    assert CheckSupportedComponentSet.CAL_ID in server.deleted


class FailingComponentSetServer(ComponentSetServer):
    """Fails the wrong-type save with ``status`` rather than a refusal."""

    def __init__(self, status, **kwargs):
        super().__init__(**kwargs)
        self.status = status

    def _save_event(self, *args, **kwargs):
        raise PutError(f"{self.status} Server Error")


@pytest.mark.parametrize("advertises", [[], ["VTODO"]], ids=["unadvertised", "advertised"])
def test_a_500_on_the_wrong_type_is_ungraceful(monkeypatch, advertises) -> None:
    """A 500 is the server failing on the wrong type: enforcement, ungracefully."""
    check = make_check(monkeypatch, FailingComponentSetServer(500, advertises=advertises))

    check._run_check()

    assert observed(check, str) == "ungraceful"
    assert "500" in observed(check, dict)["behaviour"]


def test_a_gateway_or_overload_status_on_the_wrong_type_is_not_enforcement(monkeypatch) -> None:
    """A 503 says the server had a bad moment, not that it enforced anything."""
    check = make_check(monkeypatch, FailingComponentSetServer(503, advertises=[]))

    check._run_check()

    assert observed(check, str) == "unknown"
