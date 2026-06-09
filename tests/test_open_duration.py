"""Unit tests for CheckOpenTimeRangeSearch._run_check's DTSTART+DURATION logic.

``search.time-range.open.start.duration`` asks whether a component that carries
DTSTART+DURATION (no DTEND/DUE) is found by an overlapping time-range search,
for both VTODO and VEVENT.

The tricky case: a server that does not honour the VTODO time-range at all
(returns out-of-range tasks too - tracked separately as
search.time-range.todo.strict) makes the VTODO duration probe inconclusive, not
a failure.  Such a result must NOT be reported as a VTODO/VEVENT asymmetry
("broken"); the feature is judged from the conclusive VEVENT result instead.
This is the Open-Xchange case.
"""

from datetime import date
from unittest.mock import Mock

from caldav.compatibility_hints import FeatureSet

from caldav_server_tester.checker import ServerQuirkChecker
from caldav_server_tester.checks import CheckOpenTimeRangeSearch


class _Comp(dict):
    """Minimal stand-in for an icalendar component (UID + a base-year date)."""

    def __init__(self, uid: str) -> None:
        super().__init__()
        self["UID"] = uid
        self["dtstart"] = True
        self.name = "VTODO" if "task" in uid else "VEVENT"
        self._start = date(2000, 1, 18)

    @property
    def start(self) -> date:
        return self._start


def _obj(uid: str) -> Mock:
    o = Mock()
    o.component = _Comp(uid)
    return o


def _calendar(todo_results=(), event_results=()) -> Mock:
    """Mock calendar: todo searches return todo_results, event searches event_results."""
    cal = Mock()

    def _search(**kwargs):
        if kwargs.get("todo"):
            return list(todo_results)
        if kwargs.get("event"):
            return list(event_results)
        return []

    cal.search.side_effect = _search
    return cal


def _make_check(tasklist, cal) -> CheckOpenTimeRangeSearch:
    client = Mock()
    client.features = FeatureSet()
    checker = ServerQuirkChecker(client, debug_mode=None)
    checker.calendar = cal
    checker.tasklist = tasklist
    checker.fixture_base_year = 2000
    ## The duration probe only runs when basic todo time-range search "worked",
    ## and the VEVENT branch only when event time-range search "worked".
    checker._features_checked.set_feature("search.time-range.todo")
    checker._features_checked.set_feature("search.time-range.event")
    return CheckOpenTimeRangeSearch(checker)


def _support(check: CheckOpenTimeRangeSearch) -> str:
    return check.checker.features_checked.is_supported("search.time-range.open.start.duration", str)


DUR_TASK = "csc_task_with_duration"
SIM_TASK = "csc_simple_task3"  # out-of-range sanity task (Jan 9, searches are Jan 18)
DUR_EVENT = "csc_event_with_duration"
SIM_EVENT = "csc_simple_event1"  # out-of-range sanity event (Jan 1)


def test_full_when_both_component_types_honour_duration() -> None:
    """Strict server: each typed search returns only its in-range duration component."""
    tasks = _calendar(todo_results=[_obj(DUR_TASK)])
    cal = _calendar(event_results=[_obj(DUR_EVENT)])
    check = _make_check(tasks, cal)
    check._run_check()
    assert _support(check) == "full"


def test_full_when_vtodo_inconclusive_but_vevent_ok() -> None:
    """OX case: the VTODO time-range is ignored (the out-of-range task leaks in),
    so the VTODO duration probe is inconclusive; VEVENT works -> full, NOT broken."""
    tasks = _calendar(todo_results=[_obj(DUR_TASK), _obj(SIM_TASK)])
    cal = _calendar(event_results=[_obj(DUR_EVENT)])
    check = _make_check(tasks, cal)
    check._run_check()
    assert _support(check) == "full"


def test_broken_on_genuine_asymmetry() -> None:
    """Nextcloud case: VTODO duration found (strictly), VEVENT duration NOT found,
    with no spurious leak -> a genuine asymmetry -> broken."""
    tasks = _calendar(todo_results=[_obj(DUR_TASK)])
    cal = _calendar(event_results=[])  # duration event never found
    check = _make_check(tasks, cal)
    check._run_check()
    assert _support(check) == "broken"
