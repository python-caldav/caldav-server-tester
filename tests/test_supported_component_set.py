"""The supported-calendar-component-set property, asked four questions.

RFC 4791 section 5.2.3 makes the property optional; where a calendar has it,
storing a component type it does not list "MUST result in an error", and
where it has none, every type must be accepted.  Section 5.3.1.1 has a
MKCALENDAR whose property cannot be set answer 207 with 403/409 for it and
create nothing.  So:

- ``supported-components.property-exists``: does the test calendar report it?
- ``supported-components.honoured``: does a calendar accept exactly what it
  lists - everything where it lists nothing?
- ``create-calendar.with-supported-component-types``: does a MKCALENDAR asking
  for {VTODO} give a calendar with that set?  And ``.multiple``: one asking for
  {VEVENT, VTODO}?

Bedework 5 answers the property "200 ok" and creates a VEVENT-only collection
anyway; Bedework 3.10, DAViCal and Stalwart report the VTODO-only set and take
a VEVENT; CCS refuses a two-type MKCALENDAR the 5.3.1.1 way.
"""

from unittest.mock import Mock

import pytest
from caldav.compatibility_hints import FeatureSet
from caldav.lib.error import MkcalendarError, PropfindError, PutError

import caldav_server_tester.checks as checks_mod
from caldav_server_tester.checker import ServerQuirkChecker
from caldav_server_tester.checks import CheckMakeDeleteCalendar, CheckSupportedComponentSet, PrepareCalendar

PARENT = "create-calendar.with-supported-component-types"
MULTIPLE = "create-calendar.with-supported-component-types.multiple"
EXISTS = "supported-components.property-exists"
HONOURED = "supported-components.honoured"

ALL = ["VEVENT", "VTODO", "VJOURNAL"]
MULTI_ERROR_BODY = (
    '<?xml version="1.0"?><D:multistatus xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">'
    "<D:response><D:href>/cal/x/</D:href><D:propstat><D:prop><C:supported-calendar-component-set/>"
    "</D:prop><D:status>HTTP/1.1 403 Forbidden</D:status></D:propstat></D:response></D:multistatus>"
)


def listing_as_asked(asked):
    return list(asked)


def accepting_listed(asked, component):
    return component in asked


def accepting_all(asked, component):
    return True


class ComponentSetServer:
    """A server answering the probe's MKCALENDARs and saves.

    ``reports(asked)`` is what a calendar created for ``asked`` lists (``None``:
    the property is absent, ``"fail"``: the PROPFIND fails), ``accepts(asked,
    component)`` whether such a calendar stores ``component``.  ``refuses`` is
    ``"always"``, ``"with-component-set"``, or ``"multiple-207"`` /
    ``"multiple-500"`` / ``"multiple-403"`` for a MKCALENDAR naming several
    types.  ``test_calendar_reports`` is what the test calendar lists.
    """

    def __init__(
        self,
        reports=listing_as_asked,
        accepts=accepting_listed,
        refuses=None,
        test_calendar_reports=ALL,
        raises_on=None,
        property_raises=False,
        multi_body=None,
    ):
        self.reports = reports
        self.accepts = accepts
        self.refuses = refuses
        self.test_calendar_reports = test_calendar_reports
        self.raises_on = raises_on
        self.property_raises = property_raises
        self.multi_body = multi_body
        self.created = []
        self.deleted = []
        self.saved = []
        self.objects_deleted = []

    def make_calendar(self, cal_id=None, supported_calendar_component_set=None, **kwargs):
        asked = supported_calendar_component_set
        if self.refuses == "always" or (self.refuses == "with-component-set" and asked):
            raise MkcalendarError("403 Forbidden", status=403)
        if asked and len(asked) > 1:
            if self.refuses == "multiple-207":
                raise MkcalendarError("207 Multi-Status", body=self.multi_body or MULTI_ERROR_BODY, status=207)
            if self.refuses == "multiple-500":
                raise MkcalendarError("500 Internal Server Error", status=500)
            if self.refuses == "multiple-403":
                raise MkcalendarError("403 Forbidden", status=403)
        self.created.append((cal_id, asked))
        return self._calendar(cal_id, asked or ALL, self.reports(asked) if asked else ALL)

    def _calendar(self, cal_id, asked, reported):
        cal = Mock()
        cal.cal_id = cal_id
        cal.url = f"/dav/user/{cal_id}/"
        cal.events.return_value = []

        def get_supported_components(with_fallback=True):
            if self.property_raises and cal_id == CheckSupportedComponentSet.CAL_ID:
                raise ValueError("the fake blew up in a place the probe does not catch")
            if reported == "fail":
                raise PropfindError("500 Internal Server Error", status=500)
            if reported is None:
                raise PropfindError("supported-calendar-component-set not supported")
            return list(reported)

        cal.get_supported_components.side_effect = get_supported_components
        cal.save_event.side_effect = lambda **kw: self._save(asked, "VEVENT", kw)
        cal.save_todo.side_effect = lambda **kw: self._save(asked, "VTODO", kw)
        cal.save_journal.side_effect = lambda **kw: self._save(asked, "VJOURNAL", kw)
        return cal

    def _save(self, asked, component, kwargs):
        if self.raises_on == component:
            raise RuntimeError("the probe blew up")
        if not self.accepts(asked, component):
            raise PutError("403 Forbidden")
        self.saved.append((asked, component))
        obj = Mock()
        obj.delete.side_effect = lambda: self.objects_deleted.append(kwargs.get("uid"))
        return obj

    def calendar(self, cal_id=None, name=None):
        return self._calendar(cal_id, ALL, ALL)

    def test_calendar(self):
        return self._calendar("caldav-server-checker-calendar", ALL, self.test_calendar_reports)

    def delete(self, cal):
        self.deleted.append(getattr(cal, "cal_id", None))


def make_check(monkeypatch, server, create_calendar="full", mixed=None):
    monkeypatch.setattr(checks_mod.time, "sleep", lambda _s: None)
    client = Mock()
    client.features = FeatureSet()
    monkeypatch.setattr(checks_mod.DAVObject, "delete", server.delete)
    checker = ServerQuirkChecker(client, debug_mode=None)
    checker.principal = Mock()
    checker.principal.calendar.side_effect = server.calendar
    checker.principal.make_calendar.side_effect = server.make_calendar
    checker.principal.calendars.return_value = []
    checker.calendar = server.test_calendar()
    checker.features_checked.set_feature("create-calendar", {"support": create_calendar})
    for feature, value in (mixed or {}).items():
        checker.features_checked.set_feature(feature, value)
    checker._checks_run.update({CheckMakeDeleteCalendar, PrepareCalendar})
    probe = CheckSupportedComponentSet(checker)
    probe.expected_features = client.features
    return probe


def observed(check, feature, as_type=str):
    return check.checker.features_checked.is_supported(feature, as_type)


def explicit(check, feature):
    return feature in check.checker.features_checked._server_features


def run(monkeypatch, server, **kwargs):
    check = make_check(monkeypatch, server, **kwargs)
    check.run_check()
    return check


## One fake server per column of the design's grade table


def test_sabredav_shape_is_full_throughout(monkeypatch) -> None:
    check = run(monkeypatch, ComponentSetServer())

    assert observed(check, EXISTS) == "full"
    assert observed(check, HONOURED) == "full"
    assert observed(check, PARENT) == "full"
    assert observed(check, MULTIPLE) == "full"


def test_ccs_shape_refuses_a_set_of_several(monkeypatch) -> None:
    """CCS answers 207 with 403 for the property and creates nothing, as RFC
    4791 section 5.3.1.1 has a server do that cannot set it."""
    check = run(monkeypatch, ComponentSetServer(refuses="multiple-207"))

    assert observed(check, PARENT) == "full"
    assert observed(check, HONOURED) == "full"
    assert observed(check, MULTIPLE) == "unsupported"
    assert "403" in observed(check, MULTIPLE, dict)["behaviour"]


def test_advertised_but_not_enforced_is_disobedience_not_a_creation_failure(monkeypatch) -> None:
    """Bedework 3.10, DAViCal, Stalwart: the calendar lists ['VTODO'], as asked,
    and takes a VEVENT anyway."""
    check = run(monkeypatch, ComponentSetServer(accepts=accepting_all))

    assert observed(check, PARENT) == "full"
    assert observed(check, HONOURED) == "broken"
    assert "VEVENT" in observed(check, HONOURED, dict)["behaviour"]
    assert observed(check, EXISTS) == "full"
    assert observed(check, MULTIPLE) == "full"


def test_a_set_of_several_reported_as_one_name_is_broken(monkeypatch) -> None:
    """Bedework 3.10 is said to report a VEVENT+VTODO calendar as 'VEVENTVTODO'."""
    server = ComponentSetServer(
        reports=lambda asked: ["".join(asked)] if len(asked) > 1 else list(asked),
        accepts=accepting_all,
    )
    check = run(monkeypatch, server)

    assert observed(check, MULTIPLE) == "broken"
    assert "VEVENTVTODO" in observed(check, MULTIPLE, dict)["behaviour"]


def test_bedework_5_shape(monkeypatch) -> None:
    """Every calendar is VEVENT-only, whatever was asked for, and says so."""
    server = ComponentSetServer(
        reports=lambda asked: ["VEVENT"], accepts=lambda asked, component: component == "VEVENT"
    )
    check = run(monkeypatch, server)

    assert observed(check, PARENT) == "unsupported"
    assert observed(check, HONOURED) == "full"
    ## measured, not inherited from the negative parent
    assert explicit(check, MULTIPLE)
    assert observed(check, MULTIPLE) == "broken"


def test_sogo_shape(monkeypatch) -> None:
    """The set is ignored: VEVENT, VFREEBUSY and VTODO are listed and taken."""
    server = ComponentSetServer(
        reports=lambda asked: ["VEVENT", "VFREEBUSY", "VTODO"],
        accepts=lambda asked, component: component in ("VEVENT", "VTODO"),
    )
    check = run(monkeypatch, server)

    assert observed(check, PARENT) == "unsupported"
    assert observed(check, HONOURED) == "full"
    assert explicit(check, MULTIPLE)
    assert observed(check, MULTIPLE) == "full"


def test_a_set_lacking_the_requested_type_is_not_honoured_at_creation(monkeypatch) -> None:
    check = run(monkeypatch, ComponentSetServer(reports=lambda asked: ["VFREEBUSY"]))

    assert observed(check, PARENT) == "unsupported"


def test_zimbra_shape(monkeypatch) -> None:
    """Zimbra adds VFREEBUSY to the set it reports - which nothing is ever stored
    as - so a VTODO calendar is a VTODO calendar; a mixed one is not, since
    tasks live on task lists of their own."""
    server = ComponentSetServer(
        reports=lambda asked: ["VFREEBUSY", "VTODO"] if asked == ["VTODO"] else ["VEVENT", "VFREEBUSY"],
        accepts=lambda asked, component: component in asked or (component == "VEVENT" and len(asked) > 1),
    )
    check = run(monkeypatch, server)

    assert observed(check, PARENT) == "full"
    assert observed(check, HONOURED) == "full"
    assert observed(check, MULTIPLE) == "broken"
    assert "VFREEBUSY" in observed(check, MULTIPLE, dict)["behaviour"]


def test_robur_shape(monkeypatch) -> None:
    """No property, so every type must be accepted - yet a VJOURNAL is refused,
    on the probe calendar itself and not just on a dedicated one."""
    server = ComponentSetServer(
        reports=lambda asked: None,
        accepts=lambda asked, component: component in ("VEVENT", "VTODO"),
        test_calendar_reports=None,
    )
    check = run(monkeypatch, server)

    assert observed(check, EXISTS) == "unsupported"
    assert observed(check, HONOURED) == "broken"
    assert "VJOURNAL" in observed(check, HONOURED, dict)["behaviour"]


def test_a_listed_set_without_the_journal_refuses_it(monkeypatch) -> None:
    check = run(monkeypatch, ComponentSetServer())

    assert observed(check, HONOURED) == "full"


def test_an_ignored_and_unreported_set_is_unsupported(monkeypatch) -> None:
    """Nothing reported, and the VEVENT goes in: an absent property means every
    type is welcome, so obedience holds, but the calendar is not VTODO-only."""
    server = ComponentSetServer(reports=lambda asked: None, accepts=accepting_all)
    check = run(monkeypatch, server)

    assert observed(check, PARENT) == "unsupported"
    assert observed(check, HONOURED) == "full"


def test_enforced_but_not_advertised(monkeypatch) -> None:
    """The VEVENT is refused, though the calendar lists no set: the creation did
    what was asked, but a calendar without the property must take every type."""
    check = run(monkeypatch, ComponentSetServer(reports=lambda asked: None))

    assert observed(check, PARENT) == "full"
    assert observed(check, HONOURED) == "broken"
    assert "no component set" in observed(check, HONOURED, dict)["behaviour"]


def test_another_set_reported_and_the_wrong_type_refused(monkeypatch) -> None:
    """Undecided case in the design: another set is a creation failure, and
    refusing a type the property lists is disobedience."""
    server = ComponentSetServer(reports=lambda asked: ["VEVENT", "VTODO"])
    check = run(monkeypatch, server)

    assert observed(check, PARENT) == "unsupported"
    assert observed(check, HONOURED) == "broken"


## The test calendar


def test_property_exists_follows_the_test_calendar(monkeypatch) -> None:
    check = run(monkeypatch, ComponentSetServer(test_calendar_reports=None))

    assert observed(check, EXISTS) == "unsupported"


def test_a_failing_propfind_on_the_test_calendar_is_unknown(monkeypatch) -> None:
    check = run(monkeypatch, ComponentSetServer(test_calendar_reports="fail"))

    assert observed(check, EXISTS) == "unknown"


@pytest.mark.parametrize("mixed", ["save-load.todo.mixed-calendar", "save-load.journal.mixed-calendar"])
def test_a_refused_type_on_a_test_calendar_without_the_property_is_disobedience(monkeypatch, mixed) -> None:
    """No property, yet no VJOURNAL either.  Where no calendar can be made to
    look at, what PrepareCalendar found out is all there is, and the verdict
    says it is inferred."""
    check = run(
        monkeypatch, ComponentSetServer(test_calendar_reports=None), create_calendar="unsupported", mixed={mixed: False}
    )

    assert observed(check, EXISTS) == "unsupported"
    assert observed(check, HONOURED) == "broken"
    assert "save-load" in observed(check, HONOURED, dict)["behaviour"]


def test_a_flag_on_the_test_calendar_does_not_override_a_probe_calendar(monkeypatch) -> None:
    """PrepareCalendar sets mixed-calendar False after any failure of its first
    save, a gateway error included.  With a probe calendar that took every type
    there is better evidence."""
    server = ComponentSetServer(reports=lambda asked: None, accepts=accepting_all, test_calendar_reports=None)
    check = run(monkeypatch, server, mixed={"save-load.journal.mixed-calendar": False})

    assert observed(check, HONOURED) == "full"


def test_a_refused_type_the_test_calendar_does_not_list_is_obedience(monkeypatch) -> None:
    server = ComponentSetServer(test_calendar_reports=["VEVENT", "VTODO"])
    check = run(monkeypatch, server, mixed={"save-load.journal.mixed-calendar": False})

    assert observed(check, HONOURED) == "full"


def test_journals_refused_everywhere_count_as_refused(monkeypatch) -> None:
    """save-load.journal negative: the mixed-calendar child inherits it."""
    server = ComponentSetServer(test_calendar_reports=None)
    check = run(monkeypatch, server, create_calendar="unsupported", mixed={"save-load.journal": "ungraceful"})

    assert observed(check, HONOURED) == "broken"


def test_an_unreadable_probe_calendar_property_is_not_reported_as_not_created(monkeypatch) -> None:
    check = run(monkeypatch, ComponentSetServer(reports=lambda asked: "fail"))

    assert observed(check, HONOURED) == "unknown"
    assert "could not be read" in observed(check, HONOURED, dict)["behaviour"]


## Without MKCALENDAR


def test_without_mkcalendar_obedience_is_unknown(monkeypatch) -> None:
    server = ComponentSetServer()
    check = run(monkeypatch, server, create_calendar="unsupported")

    assert observed(check, EXISTS) == "full"
    assert observed(check, HONOURED) == "unknown"
    assert not explicit(check, PARENT)
    assert not explicit(check, MULTIPLE)
    assert observed(check, MULTIPLE) == "unsupported"
    assert server.created == []


def test_without_mkcalendar_a_refusing_test_calendar_is_still_disobedience(monkeypatch) -> None:
    server = ComponentSetServer(test_calendar_reports=None)
    check = run(monkeypatch, server, create_calendar="unsupported", mixed={"save-load.journal.mixed-calendar": False})

    assert observed(check, HONOURED) == "broken"


## Refused or failing creation


def test_a_207_refusing_another_property_is_not_the_conformant_refusal(monkeypatch) -> None:
    body = MULTI_ERROR_BODY.replace("<C:supported-calendar-component-set/>", "<D:displayname/>")
    check = run(monkeypatch, ComponentSetServer(refuses="multiple-207", multi_body=body))

    assert observed(check, MULTIPLE) == "ungraceful"


def test_a_creation_refusing_the_component_set_is_ungraceful(monkeypatch) -> None:
    check = run(monkeypatch, ComponentSetServer(refuses="with-component-set"))

    assert observed(check, PARENT) == "ungraceful"
    ## not probed: inherits the parent
    assert not explicit(check, MULTIPLE)
    assert observed(check, MULTIPLE) == "ungraceful"
    assert observed(check, HONOURED) == "unknown"


def test_a_creation_failing_either_way_says_nothing(monkeypatch) -> None:
    check = run(monkeypatch, ComponentSetServer(refuses="always"))

    assert observed(check, PARENT) == "unknown"
    assert not explicit(check, MULTIPLE)


@pytest.mark.parametrize("refuses", ["multiple-500", "multiple-403"])
def test_a_set_of_several_refused_otherwise_is_ungraceful(monkeypatch, refuses) -> None:
    check = run(monkeypatch, ComponentSetServer(refuses=refuses))

    assert observed(check, PARENT) == "full"
    assert observed(check, MULTIPLE) == "ungraceful"


class FailingComponentSetServer(ComponentSetServer):
    """Fails the VEVENT save into the VTODO-only calendar with ``status``."""

    def __init__(self, status, **kwargs):
        super().__init__(**kwargs)
        self.status = status

    def _save(self, asked, component, kwargs):
        if component == "VEVENT" and asked == ["VTODO"]:
            raise PutError(f"{self.status} Server Error")
        return super()._save(asked, component, kwargs)


def test_a_500_on_the_wrong_type_is_ungraceful_obedience(monkeypatch) -> None:
    """A 500 is the server failing on the wrong type: enforcement, ungracefully.
    The listed set still answers the creation question."""
    check = run(monkeypatch, FailingComponentSetServer(500))

    assert observed(check, PARENT) == "full"
    assert observed(check, HONOURED) == "ungraceful"
    assert "500" in observed(check, HONOURED, dict)["behaviour"]


def test_a_500_on_the_wrong_type_with_nothing_reported_is_ungraceful(monkeypatch) -> None:
    check = run(monkeypatch, FailingComponentSetServer(500, reports=lambda asked: None))

    assert observed(check, PARENT) == "ungraceful"
    assert "500" in observed(check, PARENT, dict)["behaviour"]


def test_a_gateway_status_on_the_wrong_type_is_not_enforcement(monkeypatch) -> None:
    check = run(monkeypatch, FailingComponentSetServer(503, reports=lambda asked: None, accepts=accepting_all))

    assert observed(check, PARENT) == "unknown"
    assert observed(check, HONOURED) == "unknown"


## Clean-up


@pytest.mark.parametrize(
    "server",
    [
        ComponentSetServer(),
        ComponentSetServer(accepts=accepting_all),
        ComponentSetServer(refuses="multiple-207"),
        ComponentSetServer(raises_on="VTODO"),
        ComponentSetServer(property_raises=True),
    ],
    ids=["sabredav", "bedework3", "ccs", "save-raises", "probe-raises"],
)
def test_both_probe_calendars_are_deleted_before_and_after(monkeypatch, server) -> None:
    check = run(monkeypatch, server)

    for cal_id in (CheckSupportedComponentSet.CAL_ID, CheckSupportedComponentSet.CAL_ID_MULTIPLE):
        assert server.deleted.count(cal_id) >= 2, cal_id
        assert cal_id.startswith("caldav-server-checker")
    assert explicit(check, EXISTS)
    assert explicit(check, HONOURED)


def test_the_first_probe_calendar_is_gone_before_the_second_is_made(monkeypatch) -> None:
    """A provider with a calendar quota would refuse the second otherwise."""
    server = ComponentSetServer()
    order = []
    original_make, original_delete = server.make_calendar, server.delete
    server.make_calendar = lambda cal_id=None, **kw: (order.append(("make", cal_id)), original_make(cal_id, **kw))[1]
    server.delete = lambda cal: (order.append(("delete", getattr(cal, "cal_id", None))), original_delete(cal))[1]
    run(monkeypatch, server)

    first, second = CheckSupportedComponentSet.CAL_ID, CheckSupportedComponentSet.CAL_ID_MULTIPLE
    assert order.index(("delete", first), order.index(("make", first))) < order.index(("make", second))


def test_saved_objects_are_deleted_again(monkeypatch) -> None:
    server = ComponentSetServer(accepts=accepting_all)
    run(monkeypatch, server)

    assert len(server.objects_deleted) == len(server.saved) == 5


@pytest.mark.parametrize(
    ("create_calendar", "server"),
    [
        ("full", ComponentSetServer()),
        ("unsupported", ComponentSetServer()),
        ("full", ComponentSetServer(refuses="always")),
        ("full", ComponentSetServer(refuses="with-component-set")),
        ("full", ComponentSetServer(raises_on="VEVENT")),
        ("full", ComponentSetServer(property_raises=True)),
        ("full", ComponentSetServer(test_calendar_reports="fail")),
    ],
    ids=["full", "no-mkcalendar", "no-creation", "ungraceful", "raises", "probe-raises", "propfind-fails"],
)
def test_run_check_finds_every_declared_feature(monkeypatch, create_calendar, server) -> None:
    """run_check asserts every declared feature was set or has a set ancestor,
    and the grouping node supported-components never is."""
    check = run(monkeypatch, server, create_calendar=create_calendar)

    assert explicit(check, EXISTS)
    assert explicit(check, HONOURED)
