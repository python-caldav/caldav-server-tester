"""The probe-calendar sweep must never delete a calendar the user supplied.

cleanup() deliberately refuses to delete a calendar it did not create
(calendar_was_created).  The sweep that removes leftover throwaway calendars
runs afterwards and matched on a bare prefix, so a real calendar that merely
happens to live at .../testcalendar-2019/ was deleted with all its events --
defeating the very guard cleanup() exists to provide.
"""

from unittest.mock import Mock

from caldav_server_tester.checker import ServerQuirkChecker


def _cal(url: str, name: str = "") -> Mock:
    cal = Mock()
    cal.url = url
    cal.get_display_name.return_value = name
    cal.deleted = False

    def delete():
        cal.deleted = True

    cal.delete.side_effect = delete
    return cal


def _checker(calendars: list, **attrs) -> ServerQuirkChecker:
    c = ServerQuirkChecker.__new__(ServerQuirkChecker)
    principal = Mock()
    principal.calendars.return_value = calendars
    c.principal = principal
    c.extra_principals = []
    c.calendar = attrs.get("calendar")
    c.tasklist = attrs.get("tasklist")
    c.journallist = attrs.get("journallist")
    c.calendar_was_created = attrs.get("calendar_was_created", True)
    return c


class TestSweepProtectsUserCalendars:
    def test_user_supplied_calendar_is_never_deleted(self) -> None:
        """--caldav-calendar pointed at a real calendar that looks like a probe."""
        victim = _cal("http://dav/joe/testcalendar-2019/", "My Tests")
        c = _checker([victim], calendar=victim, calendar_was_created=False)
        c._purge_probe_calendars()
        assert not victim.deleted

    def test_user_supplied_tasklist_and_journallist_are_spared(self) -> None:
        tl = _cal("http://dav/joe/csc-tasks/", "Tasks")
        jl = _cal("http://dav/joe/csc-journals/", "Journals")
        c = _checker([tl, jl], tasklist=tl, journallist=jl, calendar_was_created=False)
        c._purge_probe_calendars()
        assert not tl.deleted and not jl.deleted

    def test_a_calendar_the_tool_created_is_still_swept(self) -> None:
        """The guard must not disable the sweep's actual job."""
        leftover = _cal("http://dav/joe/caldav-server-checker-calendar/", "probe")
        c = _checker([leftover], calendar=None, calendar_was_created=True)
        assert c._purge_probe_calendars() == 1
        assert leftover.deleted

    def test_unrelated_calendars_are_untouched(self) -> None:
        real = _cal("http://dav/joe/work/", "Work")
        c = _checker([real])
        assert c._purge_probe_calendars() == 0
        assert not real.deleted


class TestSweepMatchesTheProbeShape:
    def test_a_real_calendar_merely_prefixed_testcalendar_is_spared(self) -> None:
        """The free-namespace probe uses testcalendar-<uuid>; a human-named
        'testcalendar-2019' is not that shape and is not ours to delete."""
        human = _cal("http://dav/joe/testcalendar-2019/", "Test calendar 2019")
        c = _checker([human], calendar=None, calendar_was_created=True)
        assert c._purge_probe_calendars() == 0
        assert not human.deleted

    def test_the_uuid_shaped_probe_calendar_is_still_swept(self) -> None:
        import uuid

        probe = _cal(f"http://dav/joe/testcalendar-{uuid.uuid4()}/", "")
        c = _checker([probe], calendar=None, calendar_was_created=True)
        assert c._purge_probe_calendars() == 1
        assert probe.deleted
