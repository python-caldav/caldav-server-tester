import logging
import re
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from caldav.calendarobjectresource import Event, Journal, Todo
from caldav.collection import Principal
from caldav.davobject import DAVObject
from caldav.lib.error import AuthorizationError, DAVError, NotFoundError, PutError, ReportError
from caldav.search import CalDAVSearcher

from .checks_base import Check

utc = timezone.utc


def _base_year(now=None):
    """The calendar year the reusable test fixtures live in: *next* year.

    Historically all fixtures were placed in year 2000 to avoid clashing with
    real calendar content when the only available calendar is a production one.
    That backfires on servers that restrict the search window: OX App Suite, for
    instance, only exposes objects within roughly ``[now - 1 month, now + 18
    months]`` and silently hides everything outside it (verified empirically) -
    so the year-2000 fixtures are invisible to search/REPORT there and every
    fixture-dependent check misfires.

    Placing the fixtures in the *next calendar year* keeps them in the
    near future: at most ~14 months ahead (a January run), comfortably inside
    OX's window, and always in the future so OX's past-edge never hides a fresh
    fixture.  The base year is stable within a calendar year (it only changes on
    Jan 1), which keeps the ``--no-cleanup`` reuse feature effective.

    A few checks deliberately keep year-2000 *probe* objects to detect exactly
    this sliding-window behaviour (see ``csc_olddate_*`` and
    ``search.unlimited-time-range`` / ``search.time-range.*.old-dates``).
    """
    now = now or datetime.now(tz=utc)
    return now.year + 1


def _filter_fixture_window(objects, base_year):
    """Keep only objects whose (start/due/end) date falls in the fixture year.

    The fixtures all live within ``base_year`` (see :func:`_base_year`); this
    filters a calendar listing down to the reusable test set, excluding both
    unrelated real content and the year-2000 old-date probes.
    """

    def asdate(foo):
        ## datetime is a subclass of date, so we use exact type check to exclude datetime
        return foo if isinstance(foo, date) and not isinstance(foo, datetime) else foo.date()

    def dt(obj):
        """a datetime from the object, if applicable, otherwise 1980"""
        x = obj.component
        if "dtstart" in x:
            return x.start
        if "due" in x or "dtend" in x:
            return x.end
        return date(1980, 1, 1)

    def d(obj):
        return asdate(dt(obj))

    return (x for x in objects if date(base_year, 1, 1) <= d(x) <= date(base_year + 1, 1, 1))


class CheckGetCurrentUserPrincipal(Check):
    """
    Checks support for get-current-user-principal
    """

    features_to_be_checked = {"get-current-user-principal"}
    depends_on = set()

    def _run_check(self):
        try:
            self.checker.principal = self.client.principal()
            self.set_feature("get-current-user-principal")
        except AssertionError:
            raise
        except Exception:
            self.checker.principal = None
            self.set_feature("get-current-user-principal", False)
        return self.checker.principal


class CheckMakeDeleteCalendar(Check):
    """
    Checks (relatively) thoroughly that it's possible to create a calendar and delete it
    """

    features_to_be_checked = {
        "get-current-user-principal.has-calendar",
        "create-calendar.auto",
        "create-calendar",
        "create-calendar.set-displayname",
        "create-calendar.set-displayname.stable-url",
        "delete-calendar",
        "delete-calendar.free-namespace",
    }
    depends_on = {CheckGetCurrentUserPrincipal}

    @staticmethod
    def _calendar_is_accessible(cal) -> bool:
        """Probe whether a calendar is accessible by calling events().

        Returns True if events() succeeds, False if the server returns any DAV
        error (404 Not Found, 403 Forbidden, 500 Internal Server Error, etc.).
        """
        try:
            cal.events()
            return True
        except DAVError:
            return False

    def _try_make_calendar(self, cal_id, **kwargs):
        """
        Does some attempts on creating and deleting calendars, and sets some
        flags - while others should be set by the caller.

        Note: this probe deliberately does NOT set a display name.  The
        display-name behaviour (and its coupling to the calendar URL on some
        servers) is checked separately and deterministically in
        _check_set_displayname(), so that a display-name-triggered calendar
        relocation cannot pollute the create/delete/free-namespace detection
        here.
        """
        calmade = False

        ## In case calendar already exists ... wipe it first
        try:
            self.checker.principal.calendar(cal_id=cal_id).delete()
        except Exception:
            pass

        ## create the calendar
        try:
            cal = self.checker.principal.make_calendar(cal_id=cal_id, **kwargs)
            ## calendar creation probably went OK, but we need to be sure...
            cal.events()
            ## calendar creation must have gone OK.
            calmade = True
            self.checker.principal.calendar(cal_id=cal_id).events()
            self.set_feature("create-calendar")

        except DAVError:
            ## calendar creation created an exception.  Maybe the calendar exists?
            cal = self.checker.principal.calendar(cal_id=cal_id)
            if not self._calendar_is_accessible(cal):
                cal = None
            if not cal:
                ## cal not made and does not exist, exception thrown.
                return False

        assert cal

        try:
            ## Use DAVObject.delete directly to bypass Calendar.delete()
            ## workarounds - we want to test the server's raw DELETE behavior
            DAVObject.delete(cal)
            cal = self.checker.principal.calendar(cal_id=cal_id)
            if not self._calendar_is_accessible(cal):
                cal = None
            ## Delete throw no exceptions, but was the calendar deleted?
            if not cal or self.checker.features_checked.is_supported("create-calendar.auto"):
                self.set_feature("delete-calendar")
                ## Calendar probably deleted OK.
                ## (in the case of non_existing_calendar_found, we should add
                ## some events to the calendar, delete the calendar and make
                ## sure no events are found on a new calendar with same ID)
            else:
                ## Calendar not deleted.
                ## Perhaps the server needs some time to delete the calendar
                time.sleep(10)
                cal = self.checker.principal.calendar(cal_id=cal_id)
                if self._calendar_is_accessible(cal):
                    ## Calendar not deleted, but no exception thrown.
                    ## Perhaps it's a "move to thrashbin"-regime on the server
                    self.set_feature(
                        "delete-calendar",
                        {"support": "unknown", "behaviour": "move to trashbin?"},
                    )
                else:
                    ## Calendar was deleted, it just took some time.
                    self.set_feature(
                        "delete-calendar",
                        {"support": "fragile", "behaviour": "delayed deletion"},
                    )
                    return calmade
            return calmade
        except DAVError as e:
            time.sleep(10)
            try:
                DAVObject.delete(cal)
                self.set_feature(
                    "delete-calendar",
                    {
                        "support": "fragile",
                        "behaviour": "deleting a recently created calendar causes exception",
                    },
                )
            except DAVError as e2:
                self.set_feature("delete-calendar", False)
            return calmade

    def _run_check(self):
        self._probe_make_delete()
        ## The display-name behaviour is probed separately and deterministically,
        ## but only if we were actually able to create calendars at all.
        if self.checker.features_checked.is_supported("create-calendar"):
            self._check_set_displayname()

    def _probe_make_delete(self):
        try:
            cal = self.checker.principal.calendar(cal_id="this_should_not_exist")
            cal.events()
            self.set_feature("create-calendar.auto")
        except (
            NotFoundError,
            AuthorizationError,
            ReportError,
        ):  ## robur throws a 403 .. and that's ok.
            ## Some unknown server throws 500 internal server error ... well, we should survive that, too
            ## (perhaps we should do an `except Exception` instead?)
            self.set_feature("create-calendar.auto", False)

        ## Check on "no_default_calendar" flag
        try:
            cals = self.checker.principal.calendars()
            calname = cals[0].get_display_name()
            self.set_feature("get-current-user-principal.has-calendar", True)
        except Exception:
            self.set_feature("get-current-user-principal.has-calendar", False)

        _unknown_del = {"support": "unknown", "behaviour": "cannot test, delete-calendar not supported"}
        ## First attempt: a fixed cal_id.  If it succeeds, a previous run's
        ## calendar with the same id was successfully removed (or never existed),
        ## i.e. the namespace is free after deletion.
        ## TODO: this is a lie on the very first run - we haven't really verified
        ## this until a second script run.
        makeret = self._try_make_calendar(cal_id="caldav-server-checker-mkdel-test")
        if makeret:
            if self.checker.features_checked.is_supported("delete-calendar"):
                self.set_feature("delete-calendar.free-namespace", True)
            else:
                self.set_feature("delete-calendar.free-namespace", _unknown_del)
            return
        ## The fixed cal_id is stuck (a previous calendar was not freed); a fresh
        ## unique cal_id should still work -> namespace is not freed on delete.
        unique_id = "testcalendar-" + str(uuid.uuid4())
        makeret = self._try_make_calendar(cal_id=unique_id)
        if makeret:
            self.set_feature("delete-calendar.free-namespace", False)
            return
        makeret = self._try_make_calendar(cal_id=unique_id, method="mkcol")
        if makeret:
            self.set_feature("create-calendar", {"support": "quirk", "behaviour": "mkcol-required"})
            if self.checker.features_checked.is_supported("delete-calendar"):
                self.set_feature("delete-calendar.free-namespace", False)
            else:
                self.set_feature("delete-calendar.free-namespace", _unknown_del)
        else:
            self.set_feature("create-calendar", False)
            self.set_feature(
                "delete-calendar", {"support": "unknown", "behaviour": "cannot test, create-calendar not supported"}
            )
            self.set_feature(
                "delete-calendar.free-namespace",
                {"support": "unknown", "behaviour": "cannot test, create-calendar not supported"},
            )

    def _check_set_displayname(self):
        """Deterministically probe display-name support and URL stability.

        Two distinct server behaviours are checked:

        * ``create-calendar.set-displayname`` - does a display name given at
          creation time stick?
        * ``create-calendar.set-displayname.stable-url`` - does setting it leave
          the calendar URL unchanged?  Some servers (Zimbra) apply the display
          name via a rename that *moves* the collection, relocating its canonical
          URL to a display-name-derived path - while others (OX) keep the URL at
          the requested cal_id and store the name as an ordinary property.

        We create a calendar with a UNIQUE display name (exercising the same
        create path the library uses) and then look up where the calendar with
        that name ended up: still at the cal_id (stable) or under a
        display-name-derived URL (relocated).  A unique name avoids the
        namespace-collision fallback that makes a "create with name, read back by
        cal_id" probe flap between runs.  We deliberately do NOT probe via a
        rename/PROPPATCH after creation: that is a different operation (e.g. OX
        accepts a name at creation but rejects later renames).
        """
        cal_id = "caldav-server-checker-displayname-test"
        unique_name = "csc-displayname-" + str(uuid.uuid4())

        def _segment(cal):
            return str(cal.url).rstrip("/").rsplit("/", 1)[-1]

        def _find_by_displayname(name):
            try:
                cals = self.checker.principal.calendars()
            except Exception:
                return None
            for c in cals:
                try:
                    if c.get_display_name() == name:
                        return c
                except Exception:
                    pass
            return None

        def _cleanup():
            try:
                self.checker.principal.calendar(cal_id=cal_id).delete()
            except Exception:
                pass
            leftover = _find_by_displayname(unique_name)
            if leftover is not None:
                try:
                    leftover.delete()
                except Exception:
                    pass

        ## clean slate
        _cleanup()

        try:
            self.checker.principal.make_calendar(cal_id=cal_id, name=unique_name)
        except DAVError:
            ## Couldn't create the probe calendar.  Leave the feature unset; the
            ## parent create-calendar status already records the issue.
            return

        try:
            located = _find_by_displayname(unique_name)
            if located is None:
                ## the name did not stick anywhere
                self.set_feature("create-calendar.set-displayname", False)
                return

            self.set_feature("create-calendar.set-displayname", True)
            if _segment(located) == cal_id:
                self.set_feature("create-calendar.set-displayname.stable-url", True)
            else:
                self.set_feature(
                    "create-calendar.set-displayname.stable-url",
                    {
                        "support": "unsupported",
                        "behaviour": f"setting the display name relocated the calendar URL to {_segment(located)!r}",
                    },
                )
        finally:
            _cleanup()


class PrepareCalendar(Check):
    """
    This "check" doesn't check anything, but ensures the calendar has some known events
    """

    depends_on = {CheckMakeDeleteCalendar}
    features_to_be_checked = {
        "save-load.event.recurrences",
        "save-load.event.recurrences.count",
        "save-load.event.recurrences.exception",
        "save-load.todo.recurrences",
        "save-load.todo.recurrences.count",
        "save-load.event",
        "save-load.todo",
        "save-load.todo.mixed-calendar",
        "save-load.journal",
        "save-load.journal.mixed-calendar",
        "save-load.get-by-url",
        "save-load.stable-url",
    }

    def _find_or_create_calendar(self, cal_id, name, test_cal_info):
        """Find or create the main test calendar; set up self.checker.calendar."""
        try:
            if "name" in test_cal_info:
                calendar = self.checker.principal.calendar(name=name)
            else:
                calendar = self.checker.principal.calendar(cal_id=cal_id)
            ## At least one out of those two will raise NotFoundError if calendar doesn't exist
            calendar.get_display_name()
            calendar.events()
        except Exception:
            if not self.checker.features_checked.is_supported("create-calendar"):
                raise RuntimeError(
                    "Server does not support calendar creation and no existing test calendar was found. "
                    "Specify a calendar to use with --caldav-calendar <display-name>."
                )
            calendar = self.checker.principal.make_calendar(cal_id=cal_id, name=name)

        self.checker.calendar = calendar
        self.checker.tasklist = calendar
        self.checker.journallist = calendar

    def _prepare_task_calendar(self, cal_id, name, add_if_not_existing):
        """Handle task calendar setup and save-load.todo / save-load.todo.mixed-calendar features."""
        base = self.checker.fixture_base_year
        try:
            task_with_dtstart = add_if_not_existing(
                Todo,
                summary="task with a dtstart",
                uid="csc_simple_task1",
                dtstart=date(base, 1, 7),
            )
            task_with_dtstart.load()
            self.set_feature("save-load.todo")
            self.set_feature("save-load.todo.mixed-calendar")
        except Exception:
            try:
                tasklist = self.checker.principal.calendar(cal_id=f"{cal_id}_tasks")
                tasklist.todos()
            except Exception:
                tasklist = self.checker.principal.make_calendar(
                    cal_id=f"{cal_id}_tasks",
                    name=f"{name} - tasks",
                    supported_calendar_component_set=["VTODO"],
                )
            self.checker.tasklist = tasklist
            try:
                task_with_dtstart = add_if_not_existing(
                    Todo,
                    summary="task with a dtstart",
                    uid="csc_simple_task1",
                    dtstart=date(base, 1, 7),
                )
            except DAVError as e:  ## exception e for debugging purposes
                self.set_feature("save-load.todo", "ungraceful")
                return False

            task_with_dtstart.load()
            self.set_feature("save-load.todo")
            self.set_feature("save-load.todo.mixed-calendar", False)
        return True

    def _prepare_journal_calendar(self, cal_id, name, add_if_not_existing):
        """Handle journal calendar setup and save-load.journal / save-load.journal.mixed-calendar features."""
        base = self.checker.fixture_base_year
        try:
            simple_journal = add_if_not_existing(
                Journal,
                summary="simple journal entry",
                uid="csc_simple_journal1",
                dtstart=date(base, 1, 11),
            )
            simple_journal.load()
            self.set_feature("save-load.journal")
            self.set_feature("save-load.journal.mixed-calendar")
        except Exception:
            journallist = None
            try:
                journallist = self.checker.principal.calendar(cal_id=f"{cal_id}_journals")
                journallist.journals()
            except Exception:
                try:
                    journallist = self.checker.principal.make_calendar(
                        cal_id=f"{cal_id}_journals",
                        name=f"{name} - journals",
                        supported_calendar_component_set=["VJOURNAL"],
                    )
                except Exception:
                    self.set_feature("save-load.journal", False)
                    self.checker.cnt -= 1
                    journallist = None
            if journallist is not None:
                self.checker.journallist = journallist
                try:
                    simple_journal = add_if_not_existing(
                        Journal,
                        summary="simple journal entry",
                        uid="csc_simple_journal1",
                        dtstart=date(base, 1, 11),
                    )
                    simple_journal.load()
                    self.set_feature("save-load.journal")
                    self.set_feature("save-load.journal.mixed-calendar", False)
                except Exception:
                    self.set_feature("save-load.journal", "ungraceful")
                    self.checker.cnt -= 1

    def _create_test_events(self, calendar, cal_id, name, add_if_not_existing):
        """Create all the test event/task/journal objects in the calendar.

        All fixtures live in ``base`` (next calendar year, see :func:`_base_year`)
        so they stay inside the search window of sliding-window servers.  The
        relative month/day offsets are unchanged from when the fixtures lived in
        year 2000, so every fixture-dependent check keeps the same arithmetic -
        only the year differs.
        """
        base = self.checker.fixture_base_year
        todo_ok = self._prepare_task_calendar(cal_id, name, add_if_not_existing)
        if not todo_ok:
            return False

        self._prepare_journal_calendar(cal_id, name, add_if_not_existing)

        simple_event = add_if_not_existing(
            Event,
            summary="simple event with a start time and an end time",
            uid="csc_simple_event1",
            dtstart=datetime(base, 1, 1, 12, 0, 0, tzinfo=utc),
            dtend=datetime(base, 1, 1, 13, 0, 0, tzinfo=utc),
        )
        simple_event.load()
        self.set_feature("save-load.event")

        non_duration_event = add_if_not_existing(
            Event,
            summary="event with a start time but no end time",
            uid="csc_simple_event2",
            dtstart=datetime(base, 1, 2, 12, 0, 0, tzinfo=utc),
        )

        one_day_event = add_if_not_existing(
            Event,
            summary="event with a start date but no end date",
            uid="csc_simple_event3",
            dtstart=date(base, 1, 3),
        )

        two_days_event = add_if_not_existing(
            Event,
            summary="event with a start date and end date",
            uid="csc_simple_event4",
            dtstart=date(base, 1, 4),
            dtend=date(base, 1, 6),
        )

        event_with_categories = add_if_not_existing(
            Event,
            summary="event with categories",
            uid="csc_event_with_categories",
            categories="hands,feet,head",
            dtstart=datetime(base, 1, 7, 12, 0, 0),
            dtend=datetime(base, 1, 7, 13, 0, 0),
        )

        event_with_class = add_if_not_existing(
            Event,
            summary="event with confidential class",
            uid="csc_event_with_class",
            dtstart=datetime(base, 1, 16, 12, 0, 0, tzinfo=utc),
            dtend=datetime(base, 1, 16, 13, 0, 0, tzinfo=utc),
            class_="CONFIDENTIAL",
        )

        event_with_duration = add_if_not_existing(
            Event,
            summary="event with duration instead of dtend",
            uid="csc_event_with_duration",
            dtstart=datetime(base, 1, 17, 12, 0, 0, tzinfo=utc),
            duration=timedelta(hours=1),
        )

        task_with_due = add_if_not_existing(
            Todo,
            summary="task with a due date",
            uid="csc_simple_task2",
            due=date(base, 1, 8),
        )

        task_with_dtstart_and_due = add_if_not_existing(
            Todo,
            summary="task with a dtstart time and due time",
            uid="csc_simple_task3",
            dtstart=datetime(base, 1, 9, 12, 0, 0, tzinfo=utc),
            due=datetime(base, 1, 9, 13, 0, 0, tzinfo=utc),
        )

        ## Task with DTSTART + DURATION (no DUE): used by CheckOpenTimeRangeSearch
        ## for two tests:
        ## 1. Duration overlap: DTSTART=<base>-01-18T12:00Z, DURATION=PT2H → ends at 14:00Z.
        ##    Searches overlapping the interval [12:00, 14:00] should return this task.
        ##    RFC4791 section 9.9: VTODO overlaps [start, end] if DTSTART+DURATION > start.
        ## 2. Open-start filtering: DTSTART=<base>-01-18 is after end=<base>-01-15, so this
        ##    task must NOT be returned by an end-only search with end=<base>-01-15.
        add_if_not_existing(
            Todo,
            summary="task with dtstart and duration (no due)",
            uid="csc_task_with_duration",
            dtstart=datetime(base, 1, 18, 12, 0, 0, tzinfo=utc),
            duration=timedelta(hours=2),
        )

        ## TODO: there are more variants to be tested - dtstart date and due date,
        ## only duration, no time spec at all, ...

        try:
            event_with_alarm = add_if_not_existing(
                Event,
                summary="event with alarm",
                uid="csc_event_with_alarm",
                dtstart=datetime(base, 1, 10, 8, 0, 0, tzinfo=utc),
                dtend=datetime(base, 1, 10, 9, 0, 0, tzinfo=utc),
                alarm_trigger=timedelta(minutes=-15),
                alarm_action="DISPLAY",
            )
        except Exception:
            ## Some servers reject events with alarms or old dates
            self.checker.cnt -= 1
            logging.warning("Server rejected event with alarm")

        recurring_event = add_if_not_existing(
            Event,
            summary="monthly recurring event",
            uid="csc_monthly_recurring_event",
            rrule={"FREQ": "MONTHLY"},
            dtstart=datetime(base, 1, 12, 12, 0, 0, tzinfo=utc),
            dtend=datetime(base, 1, 12, 13, 0, 0, tzinfo=utc),
        )
        recurring_event.load()
        self.set_feature("save-load.event.recurrences")

        ## All-day (VALUE=DATE) yearly recurring event, used to test
        ## whether the server handles implicit recurrence for all-day events.
        ## DTSTART is <base>-02-01; the second occurrence is <base+1>-02-01.
        add_if_not_existing(
            Event,
            summary="yearly recurring all-day event",
            uid="csc_yearly_recurring_allday_event",
            rrule={"FREQ": "YEARLY"},
            dtstart=date(base, 2, 1),
        )

        event_with_rrule_and_count = add_if_not_existing(
            Event,
            f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Example Corp.//CalDAV Client//EN
BEGIN:VEVENT
UID:weeklymeeting
DTSTAMP:{base}1013T151313Z
DTSTART:{base}1018T140000Z
DTEND:{base}1018T150000Z
SUMMARY:Weekly meeting for three weeks
RRULE:FREQ=WEEKLY;COUNT=3
END:VEVENT
END:VCALENDAR""",
        )
        event_with_rrule_and_count.load()
        component = event_with_rrule_and_count.component
        rrule = component.get("RRULE", None)
        count = rrule and rrule.get("COUNT")
        self.set_feature("save-load.event.recurrences.count", count == [3])

        try:
            recurring_task = add_if_not_existing(
                Todo,
                summary="monthly recurring task",
                uid="csc_monthly_recurring_task",
                rrule={"FREQ": "MONTHLY"},
                dtstart=datetime(base, 1, 12, 12, 0, 0, tzinfo=utc),
                due=datetime(base, 1, 12, 13, 0, 0, tzinfo=utc),
            )
            recurring_task.load()
            self.set_feature("save-load.todo.recurrences")
        except DAVError:
            self.set_feature("save-load.todo.recurrences", "ungraceful")

        try:
            task_with_rrule_and_count = add_if_not_existing(
                Todo,
                f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Example Corp.//CalDAV Client//EN
BEGIN:VTODO
UID:csc_recurring_count_task
DTSTAMP:{base}1013T151313Z
DTSTART:{base}1016T065500Z
STATUS:NEEDS-ACTION
DURATION:PT10M
SUMMARY:Weekly task to be done three times
RRULE:FREQ=WEEKLY;BYDAY=MO;COUNT=3
CATEGORIES:CHORE
PRIORITY:3
END:VTODO
END:VCALENDAR""",
            )
            task_with_rrule_and_count.load()
            component = task_with_rrule_and_count.component
            rrule = component.get("RRULE", None)
            count = rrule and rrule.get("COUNT")
            self.set_feature("save-load.todo.recurrences.count", count == [3])
        except DAVError:
            self.set_feature("save-load.todo.recurrences.count", "ungraceful")

        try:
            recurring_event_with_exception = add_if_not_existing(
                Event,
                f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//tobixen//Caldav-Server-Tester//en_DK
BEGIN:VEVENT
UID:csc_monthly_recurring_with_exception
DTSTART:{base}0113T120000Z
DTEND:{base}0113T130000Z
DTSTAMP:20240429T181103Z
RRULE:FREQ=MONTHLY
SUMMARY:Monthly recurring with exception
END:VEVENT
BEGIN:VEVENT
UID:csc_monthly_recurring_with_exception
RECURRENCE-ID:{base}0213T120000Z
DTSTART:{base}0213T120000Z
DTEND:{base}0213T130000Z
DTSTAMP:20240429T181103Z
SUMMARY:February recurrence with different summary
END:VEVENT
END:VCALENDAR""",
            )

            ## Check whether the server stores exception VEVENTs as part of the same
            ## calendar object resource as the master VEVENT, or incorrectly (either as
            ## separate objects or already expanded into 3 VEVENTs instead of 2).
            ## When stored incorrectly, client-side expansion gives wrong results.
            try:
                exception_uid = "csc_monthly_recurring_with_exception"
                objs_with_exception_uid = [
                    obj for obj in calendar.events() if obj.icalendar_component.get("UID") == exception_uid
                ]
                if len(objs_with_exception_uid) != 1:
                    ## Multiple objects with the same UID: server split exception into separate object
                    self.set_feature("save-load.event.recurrences.exception", False)
                else:
                    ## One object - check it has exactly 2 VEVENTs (master + exception)
                    vevents = [
                        c for c in objs_with_exception_uid[0].icalendar_instance.subcomponents if c.name == "VEVENT"
                    ]
                    self.set_feature(
                        "save-load.event.recurrences.exception",
                        len(vevents) == 2,
                    )
            except Exception:
                self.set_feature("save-load.event.recurrences.exception", "ungraceful")
        except DAVError:
            self.set_feature("save-load.event.recurrences.exception", "ungraceful")

        self._create_olddate_probes()

        return True

    def _create_olddate_probes(self):
        """Create the persistent year-2000 probe objects.

        The reusable fixtures live in the near future (next year) so that
        sliding-window servers can see them, but a couple of checks still need
        an object that is *intentionally* far in the past, to detect whether the
        server hides far-past objects from search:

        * ``csc_olddate_event`` (2000-01-01) - used by
          ``search.time-range.event.old-dates`` and ``search.unlimited-time-range``.
        * ``csc_olddate_task`` (2000-01-09) - used by
          ``search.time-range.todo.old-dates``.

        These are PUT unconditionally (idempotent overwrite by UID) rather than
        via the near-future reuse machinery, since they fall outside the fixture
        window and would never be matched there.  They are NOT counted in
        ``checker.cnt`` and are excluded from the comp-type reference sets by
        :func:`_filter_fixture_window`.
        """
        try:
            self.checker.calendar.save_object(
                Event,
                summary="old-date probe event (year 2000)",
                uid="csc_olddate_event",
                dtstart=datetime(2000, 1, 1, 12, 0, 0, tzinfo=utc),
                dtend=datetime(2000, 1, 1, 13, 0, 0, tzinfo=utc),
            )
        except Exception:
            logging.warning("Server rejected the year-2000 old-date probe event")
        try:
            self.checker.tasklist.save_object(
                Todo,
                summary="old-date probe task (year 2000)",
                uid="csc_olddate_task",
                dtstart=datetime(2000, 1, 9, 12, 0, 0, tzinfo=utc),
                due=datetime(2000, 1, 9, 13, 0, 0, tzinfo=utc),
            )
        except Exception:
            logging.warning("Server rejected the year-2000 old-date probe task")

    def _check_get_by_url(self, calendar):
        """Check if GET requests to server-reported calendar object URLs work."""
        ## Tests the URL returned by the server (via PROPFIND/REPORT), not the
        ## client-constructed PUT URL - some servers (e.g. Zimbra) accept PUTs
        ## to client URLs but return different URLs that fail on GET.
        try:
            server_event = calendar.object_by_uid("csc_simple_event1")
            r = self.client.request(str(server_event.url))
            self.set_feature("save-load.get-by-url", r.status != 404)
        except Exception:
            self.set_feature("save-load.get-by-url", None)

    def _check_stable_url(self, calendar):
        """Check whether the server reports objects under the same URL the client stored them at.

        Some servers (e.g. OX App Suite) canonicalize the calendar path, so an
        object looked up via REPORT (object_by_uid) is reported under a different
        URL than the one the client PUT to.  Clients must therefore not assume a
        searched object's URL equals the URL it was created at.
        """
        try:
            server_event = calendar.object_by_uid("csc_simple_event1")
            client_url = calendar.url.join("csc_simple_event1.ics")
            stable = server_event.url.canonical() == client_url.canonical()
            self.set_feature("save-load.stable-url", stable)
        except Exception:
            self.set_feature("save-load.stable-url", None)

    def _run_check(self):
        ## NOTE: Any objects created here with a UID starting with "csc_" will be
        ## cleaned up by the csc_* fallback in checker.cleanup(). For servers that
        ## support calendar deletion, the whole calendar is deleted instead.
        ## If you add new objects here, make sure their UIDs start with "csc_".

        ## The reusable fixtures live in next calendar year (see _base_year): near
        ## enough to stay inside sliding-window servers' search windows, stable
        ## enough that the --no-cleanup reuse feature works for a whole year.
        base = _base_year()
        self.checker.fixture_base_year = base

        cal_id = "caldav-server-checker-calendar"
        test_cal_info = self.checker.expected_features.is_supported(
            "test-calendar.compatibility-tests", return_type=dict
        )
        name = test_cal_info.get("name", "Calendar for checking server feature support")

        self._find_or_create_calendar(cal_id, name, test_cal_info)
        calendar = self.checker.calendar

        ## TODO: replace this with one search if possible(?)
        ## Some servers (e.g. CCS) reject time-range queries for some dates
        ## (min-date-time restriction), so fall back to empty lists.
        ## The range only needs to span the fixtures (Jan 1 - Feb 13); we stop at
        ## March 1 rather than year-end so the *end* bound also stays inside a
        ## sliding window (OX rejects a query whose end is >~18 months ahead).
        try:
            events_in_window = calendar.search(event=True, start=datetime(base, 1, 1), end=datetime(base, 3, 1))
        except (AuthorizationError, DAVError):
            events_in_window = []
        try:
            tasks_in_window = calendar.search(todo=True, start=datetime(base, 1, 1), end=datetime(base, 3, 1))
        except (AuthorizationError, DAVError):
            tasks_in_window = []
        ## Some servers silently return empty for time-range queries.  Fall back
        ## to listing all objects and filtering by date so existing fixtures from
        ## a previous run are detected and not re-PUT.
        if not events_in_window and not tasks_in_window:
            try:
                events_in_window = calendar.events()
            except (AuthorizationError, DAVError):
                pass
            try:
                tasks_in_window = self.checker.tasklist.todos()
            except (AuthorizationError, DAVError):
                pass
        try:
            journals_in_window = calendar.journals()
        except (AuthorizationError, DAVError):
            journals_in_window = []

        object_by_uid = {}
        self.checker.cnt = 0

        for obj in _filter_fixture_window(events_in_window + tasks_in_window, base):
            object_by_uid[obj.component["uid"]] = obj
        for obj in journals_in_window:
            try:
                object_by_uid[obj.component["uid"]] = obj
            except Exception:
                pass

        def add_if_not_existing(*largs, **kwargs):
            self.checker.cnt += 1
            if largs[0] == Todo:
                cal = self.checker.tasklist
            elif largs[0] == Journal:
                cal = self.checker.journallist
            else:
                cal = self.checker.calendar
            if "uid" in kwargs:
                uid = kwargs["uid"]
            elif not kwargs:
                uid = re.search("UID:(.*)\n", largs[1]).group(1)
            if uid in object_by_uid:
                return object_by_uid.pop(uid)
            try:
                return cal.save_object(*largs, **kwargs)
            except PutError:
                ## 409 Conflict: object exists but is hidden from search
                ## (e.g. OX's sliding window hides old objects from REPORT/PROPFIND).
                ## Try to load the existing object by constructing its URL directly.
                obj_class = largs[0]
                existing = obj_class(cal.client, url=cal.url.join(uid + ".ics"), parent=cal)
                existing.load()
                return existing

        if not self._create_test_events(calendar, cal_id, name, add_if_not_existing):
            return

        ## Delete any stale fixtures from the current fixture window that aren't
        ## part of the current test set (e.g. leftovers from previous test runs).
        ## The year-2000 old-date probes fall outside the window and are excluded
        ## by _filter_fixture_window, so they are not touched here.
        for uid, obj in object_by_uid.items():
            logging.warning("Deleting stale fixture object with UID %s", uid)
            obj.delete()
        if not self.checker.calendar.events():
            logging.error("Calendar appears empty after PrepareCalendar; subsequent checks may be unreliable")
        ## Not asserting on tasklist.todos() here - on servers with broken
        ## comp-type filtering (e.g. Bedework), todos() returns empty even
        ## though todos were saved successfully (verified via load() above).

        self._check_get_by_url(calendar)
        self._check_stable_url(calendar)


class CheckMutable(Check):
    """
    Checks whether the server allows modification of existing calendar objects.

    Modifies the SUMMARY of csc_simple_event1, saves it, reloads it, and
    verifies the server accepted the update.  Replaces the old 'no_overwrite'
    compatibility flag.
    """

    depends_on = {PrepareCalendar}
    features_to_be_checked = {"save-load.mutable"}

    def _run_check(self) -> None:
        cal = self.checker.calendar
        uid = "csc_simple_event1"

        ## OX answers a UID calendar-query with 403 Forbidden rather than a 404,
        ## so fall back to a direct-URL GET on any lookup failure.
        try:
            event = cal.event_by_uid(uid)
        except (NotFoundError, AuthorizationError, DAVError):
            event = Event(cal.client, url=cal.url.join(uid + ".ics"), parent=cal)
            event.load()

        vevent = event.icalendar_instance.walk("VEVENT")[0]
        original_summary = str(vevent.get("SUMMARY", ""))
        modified_summary = original_summary + " (mutable-check)"

        try:
            vevent["SUMMARY"] = modified_summary
            event.save()
            event.load()

            vevent_reloaded = event.icalendar_instance.walk("VEVENT")[0]
            current_summary = str(vevent_reloaded.get("SUMMARY", ""))

            if current_summary == modified_summary:
                self.set_feature("save-load.mutable")
            else:
                self.set_feature(
                    "save-load.mutable",
                    {"support": "broken", "behaviour": "modification not reflected after save and reload"},
                )
        except (DAVError, AuthorizationError):
            self.set_feature("save-load.mutable", False)
        finally:
            try:
                vevent_restore = event.icalendar_instance.walk("VEVENT")[0]
                vevent_restore["SUMMARY"] = original_summary
                event.save()
            except Exception:
                pass


class CheckIfMatchOptional(Check):
    """
    Checks whether an existing object can be repeatedly overwritten by a fresh
    PUT that carries no If-Match etag.

    OX App Suite enforces optimistic concurrency and rejects such a blind
    overwrite with 409 Conflict (it tolerates the first overwrite, then refuses
    subsequent no-If-Match PUTs).  An etag-conditional save() still works, so
    save-load.mutable stays full - this is a distinct, finer capability.
    """

    depends_on = {PrepareCalendar}
    features_to_be_checked = {"save-load.mutable.if-match-optional"}

    def _run_check(self) -> None:
        cal = self.checker.calendar
        uid = "csc_put_overwrite"
        ts = (datetime.now(tz=utc) + timedelta(days=30)).strftime("%Y%m%dT%H%M%SZ")
        end = (datetime.now(tz=utc) + timedelta(days=30, hours=1)).strftime("%Y%m%dT%H%M%SZ")
        data = (
            "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//caldav-server-tester//EN\n"
            f"BEGIN:VEVENT\nUID:{uid}\nDTSTAMP:{ts}\nDTSTART:{ts}\nDTEND:{end}\n"
            "SUMMARY:put-overwrite check\nEND:VEVENT\nEND:VCALENDAR\n"
        )
        ## Use a dedicated, freshly-created object so the result is independent of
        ## how many times the shared probe events have already been PUT.
        try:
            cal.add_event(data)
        except (DAVError, PutError):
            self.set_feature("save-load.mutable.if-match-optional", None)
            return

        try:
            ## Two blind (no If-Match) overwrites - servers that enforce optimistic
            ## concurrency (e.g. OX) tolerate the first then reject the second with 409.
            for _ in range(2):
                Event(cal.client, data=data, parent=cal).save()
            self.set_feature("save-load.mutable.if-match-optional")
        except (DAVError, PutError):
            self.set_feature("save-load.mutable.if-match-optional", False)
        finally:
            try:
                Event(cal.client, url=cal.url.join(uid + ".ics"), parent=cal).delete()
            except Exception:
                pass


class CheckAttendeePartstat(Check):
    """
    Checks whether an attendee's PARTSTAT can be modified via a direct PUT.

    OX App Suite forbids this (403 Forbidden, even with a matching etag) and
    expects the change to be made through iTIP scheduling instead.
    """

    depends_on = {PrepareCalendar}
    features_to_be_checked = {"save-load.mutable.attendee-partstat"}

    def _run_check(self) -> None:
        cal = self.checker.calendar
        start = datetime.now(tz=utc) + timedelta(days=30)
        try:
            ev = cal.add_event(
                uid="csc_attendee_partstat",
                dtstart=start,
                dtend=start + timedelta(hours=1),
                summary="attendee partstat check",
                ical_fragment=("ATTENDEE;ROLE=OPT-PARTICIPANT;PARTSTAT=TENTATIVE:MAILTO:csc-attendee@example.com"),
            )
        except (DAVError, PutError):
            self.set_feature("save-load.mutable.attendee-partstat", None)
            return

        try:
            ev.change_attendee_status(attendee="csc-attendee@example.com", PARTSTAT="ACCEPTED")
        except Exception:
            self.set_feature("save-load.mutable.attendee-partstat", None)
            try:
                ev.delete()
            except Exception:
                pass
            return

        try:
            ev.save()
            self.set_feature("save-load.mutable.attendee-partstat")
        except (DAVError, AuthorizationError):
            self.set_feature("save-load.mutable.attendee-partstat", False)
        finally:
            try:
                ev.delete()
            except Exception:
                pass


class CheckSearch(Check):
    depends_on = {PrepareCalendar}
    features_to_be_checked = {
        "search.time-range.event",
        "search.time-range.event.old-dates",
        "search.text.category",
        "search.time-range.todo",
        "search.time-range.todo.old-dates",
        "search.comp-type",
        "search.comp-type.optional",
        "search.time-range.comp-type-optional",
        "search.text.comp-type-optional",
        "search.combined-is-logical-and",
        "search.unlimited-time-range",
    }  ## TODO: we can do so much better than this

    def _check_time_range_with_recent_data(self, cal, tasklist):
        """Test time-range searches using temporary near-future objects.

        Some servers (e.g. CCS) enforce min-date-time restrictions and
        reject queries for old dates, but work fine with recent dates.
        This check distinguishes "time-range unsupported" from
        "time-range works but only for recent dates".
        """
        now = datetime.now(tz=utc)
        tomorrow = now + timedelta(days=1)
        day_after = now + timedelta(days=2)
        recent_event = None
        recent_task = None
        ## Use unique UIDs to avoid conflicts on servers that enforce unique
        ## UIDs across calendars (e.g. Nextcloud with unique_calendar_ids)
        recent_uid_suffix = uuid.uuid4().hex[:8]

        ## Test event time-range with a recent event
        try:
            recent_event = cal.save_object(
                Event,
                summary="recent time-range check event",
                uid=f"csc_recent_timerange_event_{recent_uid_suffix}",
                dtstart=datetime(tomorrow.year, tomorrow.month, tomorrow.day, 12, 0, 0, tzinfo=utc),
                dtend=datetime(tomorrow.year, tomorrow.month, tomorrow.day, 13, 0, 0, tzinfo=utc),
            )
            events = cal.search(
                start=datetime(tomorrow.year, tomorrow.month, tomorrow.day, 11, 0, 0, tzinfo=utc),
                end=datetime(day_after.year, day_after.month, day_after.day, 0, 0, 0, tzinfo=utc),
                event=True,
                post_filter=False,
            )
            self.set_feature("search.time-range.event", len(events) >= 1)
        except (AuthorizationError, DAVError):
            self.set_feature("search.time-range.event", "ungraceful")
        finally:
            if recent_event:
                try:
                    recent_event.delete()
                except Exception:
                    pass

        ## Test todo time-range with a recent task
        try:
            recent_task = tasklist.save_object(
                Todo,
                summary="recent time-range check task",
                uid=f"csc_recent_timerange_task_{recent_uid_suffix}",
                dtstart=datetime(tomorrow.year, tomorrow.month, tomorrow.day, 12, 0, 0, tzinfo=utc),
                due=datetime(tomorrow.year, tomorrow.month, tomorrow.day, 13, 0, 0, tzinfo=utc),
            )
            tasks = tasklist.search(
                start=datetime(tomorrow.year, tomorrow.month, tomorrow.day, 11, 0, 0, tzinfo=utc),
                end=datetime(day_after.year, day_after.month, day_after.day, 0, 0, 0, tzinfo=utc),
                todo=True,
                include_completed=True,
                post_filter=False,
            )
            self.set_feature("search.time-range.todo", len(tasks) >= 1)
        except (AuthorizationError, DAVError):
            self.set_feature("search.time-range.todo", "ungraceful")
        finally:
            if recent_task:
                try:
                    recent_task.delete()
                except Exception:
                    pass

    def _comptype_search_calendars(self):
        """The distinct calendars PrepareCalendar populated.

        Events, tasks and journals may all live in one calendar, or be split
        across up to three (e.g. servers that reject VTODO/VJOURNAL in a mixed
        calendar get a dedicated ``..._tasks`` / ``..._journals`` calendar).
        """
        cals = []
        for c in (self.checker.calendar, self.checker.tasklist, getattr(self.checker, "journallist", None)):
            if c is not None and not any(c is seen for seen in cals):
                cals.append(c)
        return cals

    def _probe_comptype(self):
        """Set ``search.comp-type``: when a calendar-query DOES specify a
        component type, does the server return only objects of that type?

        Per RFC4791 the server MUST filter a calendar-query by the requested
        component type.  Some servers misclassify (e.g. Bedework returns
        VTODOs for a VEVENT query) or ignore the comp-filter entirely and
        return everything.  When that happens the library falls back to
        client-side filtering, so we send the query with
        ``compatibility_workarounds=False`` to observe the raw server
        behaviour rather than the workaround.

        Supported (``full``) means every comp-type-specific query returned
        only objects of the requested type, across every distinct calendar the
        checker populated.  A query that returns an object of a different type
        is ``broken``.  If no typed query returns anything (nothing to judge
        from) the result is left ``unknown``.
        """
        wanted = {
            "VEVENT": {"event": True},
            "VTODO": {"todo": True, "include_completed": True},
            "VJOURNAL": {"journal": True},
        }
        base = self.checker.fixture_base_year
        try:
            seen_any = False
            mismatches = set()
            for c in self._comptype_search_calendars():
                for comp_name, kwargs in wanted.items():
                    try:
                        results = list(
                            _filter_fixture_window(
                                c.search(post_filter=False, compatibility_workarounds=False, **kwargs), base
                            )
                        )
                    except Exception:
                        ## The server may refuse to search for a component type
                        ## it does not support at all (e.g. VJOURNAL on CCS); it
                        ## cannot misclassify what it won't search for, so skip
                        ## it as a reference.
                        continue
                    for obj in results:
                        got = obj.component.name
                        seen_any = True
                        if got != comp_name:
                            mismatches.add(f"a {comp_name} query returned a {got}")
            if not seen_any:
                self.set_feature("search.comp-type", None)
            elif mismatches:
                self.set_feature(
                    "search.comp-type",
                    {"support": "broken", "behaviour": "; ".join(sorted(mismatches))},
                )
            else:
                self.set_feature("search.comp-type")
        except Exception:
            self.set_feature("search.comp-type", {"support": "ungraceful"})

    def _probe_comptype_optional(self):
        """Set ``search.comp-type.optional``: when a calendar-query omits the
        component type, does the server still return every object?

        The reference set is what the comp-type-*specific* queries (events +
        todos + journals) return across every distinct calendar the checker
        populated - NOT ``self.checker.cnt``.  ``cnt`` is a bookkeeping counter
        that aggregates objects across the event/task/journal calendars and may
        include objects that failed to save; comparing a single-calendar
        comp-type-less search against it produced spurious "fragile"/"ungraceful"
        verdicts on every server that stores journals or tasks in a separate
        calendar (the comp-type-less search there can never reach ``cnt``).
        See https://github.com/python-caldav/caldav/issues/681

        This must be tested WITHOUT a time-range: a comp-type-less query that
        carries a time-range is a separate feature
        (search.time-range.comp-type-optional below), because RFC4791 section 9.7
        forbids a CALDAV:time-range directly under VCALENDAR.
        ``compatibility_workarounds=False`` makes the library send the bare
        comp-type-less query verbatim instead of splitting it per component type.
        """
        base = self.checker.fixture_base_year
        try:
            bare_urls = set()
            typed_urls = set()
            typed_todo_urls = set()
            for c in self._comptype_search_calendars():
                for obj in _filter_fixture_window(c.search(post_filter=False, compatibility_workarounds=False), base):
                    bare_urls.add(str(obj.url))
                for kwargs in (
                    {"event": True},
                    {"todo": True, "include_completed": True},
                    {"journal": True},
                ):
                    try:
                        for obj in _filter_fixture_window(c.search(post_filter=False, **kwargs), base):
                            url = str(obj.url)
                            typed_urls.add(url)
                            if "todo" in kwargs:
                                typed_todo_urls.add(url)
                    except Exception:
                        ## A component type the server does not support at all
                        ## (e.g. VJOURNAL on CCS) - just skip it as a reference.
                        pass

            if not bare_urls:
                self.set_feature(
                    "search.comp-type.optional",
                    {
                        "support": "unsupported",
                        "description": "search that does not include comptype yields nothing",
                    },
                )
            elif typed_urls <= bare_urls:
                ## the comp-type-less query returned (at least) everything the
                ## comp-type-specific queries returned
                self.set_feature("search.comp-type.optional")
            elif typed_todo_urls and not (typed_todo_urls <= bare_urls):
                self.set_feature(
                    "search.comp-type.optional",
                    {
                        "support": "fragile",
                        "description": "search that does not include comptype does not yield tasks",
                    },
                )
            else:
                self.set_feature(
                    "search.comp-type.optional",
                    {
                        "support": "fragile",
                        "description": "unexpected results from date-search without comp-type",
                    },
                )
        except Exception:
            self.set_feature("search.comp-type.optional", {"support": "ungraceful"})

    def _run_check(self):
        cal = self.checker.calendar
        tasklist = self.checker.tasklist
        base = self.checker.fixture_base_year

        ## First, test time-range with recent dates (near-future).
        ## This determines the base search.time-range.event/todo support.
        self._check_time_range_with_recent_data(cal, tasklist)

        ## Then test with old dates (year 2000) for the .old-dates sub-feature,
        ## using the dedicated csc_olddate_* probes that PrepareCalendar PUT far
        ## in the past.  Servers with a sliding window (e.g. OX) hide those, so
        ## this distinguishes "old-date search works" from "window-restricted".
        try:
            events = cal.search(
                start=datetime(2000, 1, 1, tzinfo=utc),
                end=datetime(2000, 1, 2, tzinfo=utc),
                event=True,
                post_filter=False,
            )
            self.set_feature("search.time-range.event.old-dates", len(events) == 1)
        except (AuthorizationError, DAVError):
            self.set_feature("search.time-range.event.old-dates", "ungraceful")

        try:
            tasks = tasklist.search(
                start=datetime(2000, 1, 9, tzinfo=utc),
                end=datetime(2000, 1, 10, tzinfo=utc),
                todo=True,
                include_completed=True,
                post_filter=False,
            )
            self.set_feature("search.time-range.todo.old-dates", len(tasks) == 1)
        except (AuthorizationError, DAVError):
            self.set_feature("search.time-range.todo.old-dates", "ungraceful")

        ## search.text.category
        try:
            events = cal.search(category="hands", event=True, post_filter=False)
            self.set_feature("search.text.category", len(events) == 1)
        except (ReportError, AuthorizationError, DAVError):
            self.set_feature("search.text.category", "ungraceful")
        ## search.combined - category "hands" AND a time-range.  Uses the
        ## in-window "hands" fixture (csc_event_with_categories, <base>-01-07), so
        ## it works regardless of old-date support; only category search is needed.
        if self.feature_checked("search.text.category"):
            try:
                events1 = cal.search(
                    category="hands",
                    event=True,
                    start=datetime(base, 1, 1, 11, 0, 0),
                    end=datetime(base, 1, 13, 14, 0, 0),
                    post_filter=False,
                )
                events2 = cal.search(
                    category="hands",
                    event=True,
                    start=datetime(base, 1, 1, 9, 0, 0),
                    end=datetime(base, 1, 6, 14, 0, 0),
                    post_filter=False,
                )
                self.set_feature("search.combined-is-logical-and", len(events1) == 1 and len(events2) == 0)
            except (AuthorizationError, DAVError):
                self.set_feature("search.combined-is-logical-and", "ungraceful")
        else:
            ## Can't test combined search without category search support
            self.set_feature("search.combined-is-logical-and", None)

        ## search.comp-type: does a query that DOES specify a component type
        ## return only objects of that type? (see _probe_comptype for details)
        self._probe_comptype()

        ## search.comp-type.optional (see _probe_comptype_optional for details)
        self._probe_comptype_optional()

        ## search.time-range.comp-type-optional: does the server accept a
        ## calendar-query that carries a time-range but does NOT specify a
        ## component type?  RFC4791 section 9.7 only allows a CALDAV:time-range
        ## inside a comp-filter for VEVENT/VTODO/VJOURNAL/VFREEBUSY/VALARM, never
        ## directly under VCALENDAR - so 'unsupported' here is FULLY RFC-COMPLIANT
        ## and not a server defect (SabreDAV-based servers such as Baikal reject it
        ## with HTTP 400 "You cannot add time-range filters on the VCALENDAR
        ## component").  compatibility_workarounds=False sends the raw query so we
        ## observe the server's actual reaction rather than the library's
        ## per-component-type split.  Recent dates are used to avoid conflating with
        ## old-date restrictions.  See
        ## https://github.com/python-caldav/caldav/issues/681
        ## We must verify that a known in-range object is actually RETURNED, not
        ## merely that the query did not raise: some servers (e.g. SOGo) accept the
        ## query without error but silently return nothing when no component type is
        ## given, which is not real support.
        now = datetime.now(tz=utc)
        tr_uid = f"csc_tr_comptype_optional_{uuid.uuid4().hex[:8]}"
        tr_event = None
        try:
            tr_event = cal.save_object(
                Event,
                summary="time-range comp-type-optional check event",
                uid=tr_uid,
                dtstart=datetime(now.year, now.month, now.day, 12, 0, 0, tzinfo=utc) + timedelta(days=1),
                dtend=datetime(now.year, now.month, now.day, 13, 0, 0, tzinfo=utc) + timedelta(days=1),
            )
            objects = cal.search(
                start=now,
                end=now + timedelta(days=2),
                post_filter=False,
                compatibility_workarounds=False,
            )
            if any(tr_uid in o.data for o in objects):
                self.set_feature("search.time-range.comp-type-optional")
            else:
                self.set_feature(
                    "search.time-range.comp-type-optional",
                    {
                        "support": "unsupported",
                        "description": "comp-type-less time-range query returns nothing (server silently ignores it or requires a component type)",
                    },
                )
        except (ReportError, AuthorizationError, DAVError):
            self.set_feature(
                "search.time-range.comp-type-optional",
                {
                    "support": "unsupported",
                    "description": "server rejects a time-range filter in a query without a component type (RFC4791 section 9.7 compliant)",
                },
            )
        finally:
            if tr_event:
                try:
                    tr_event.delete()
                except Exception:
                    pass

        ## search.text.comp-type-optional: does a prop-filter (here CATEGORIES) work
        ## without a component type?  Placed under VCALENDAR the prop-filter targets
        ## VCALENDAR's own properties (which lack CATEGORIES), so servers typically
        ## match nothing.  Only meaningful when category search itself works; otherwise
        ## the result would be ambiguous.  compatibility_workarounds=False sends the raw
        ## comp-type-less query.  See https://github.com/python-caldav/caldav/issues/681
        if self.feature_checked("search.text.category"):
            pf_uid = f"csc_pf_comptype_optional_{uuid.uuid4().hex[:8]}"
            pf_cat = f"csccat{uuid.uuid4().hex[:8]}"
            pf_event = None
            try:
                pf_event = cal.save_object(
                    Event,
                    summary="prop-filter comp-type-optional check event",
                    uid=pf_uid,
                    categories=pf_cat,
                    dtstart=datetime(now.year, now.month, now.day, 12, 0, 0, tzinfo=utc) + timedelta(days=1),
                    dtend=datetime(now.year, now.month, now.day, 13, 0, 0, tzinfo=utc) + timedelta(days=1),
                )
                objects = cal.search(
                    category=pf_cat,
                    post_filter=False,
                    compatibility_workarounds=False,
                )
                if any(pf_uid in o.data for o in objects):
                    self.set_feature("search.text.comp-type-optional")
                else:
                    self.set_feature(
                        "search.text.comp-type-optional",
                        {
                            "support": "unsupported",
                            "description": "comp-type-less prop-filter query returns nothing (prop-filter under VCALENDAR matches no component property)",
                        },
                    )
            except (ReportError, AuthorizationError, DAVError):
                self.set_feature(
                    "search.text.comp-type-optional",
                    {
                        "support": "unsupported",
                        "description": "server rejects a prop-filter in a query without a component type",
                    },
                )
            finally:
                if pf_event:
                    try:
                        pf_event.delete()
                    except Exception:
                        pass
        else:
            self.set_feature(
                "search.text.comp-type-optional",
                {
                    "support": "unknown",
                    "description": "cannot test - category/text search not supported by this server",
                },
            )

        ## search.unlimited-time-range: does a REPORT without a time range return all
        ## objects regardless of date?  We look for two probes PrepareCalendar placed:
        ##   * csc_olddate_event - a non-recurring event far in the past (year 2000)
        ##   * csc_simple_event1 - a non-recurring fixture in the near future (next year)
        ## A truly unlimited server returns both.  A sliding-window server (e.g. OX)
        ## returns the near-future fixture but hides the far-past probe -> broken.  A
        ## server with a window so tight it even hides next-year objects is recorded
        ## with that more severe behaviour (and a loud warning), since that makes the
        ## near-future fixtures - and therefore most fixture-dependent checks - unreliable.
        ## Uses _request_report_build_resultlist directly to bypass the
        ## search.unlimited-time-range workaround in search.py, so the actual server
        ## behaviour is observed.
        try:
            searcher = CalDAVSearcher(comp_class=Event)
            xml, comp_class = searcher.build_search_xml_query()
            _, objects = cal._request_report_build_resultlist(xml, comp_class)
            ## Match on the iCalendar UID, not o.id: on URL-canonicalizing servers
            ## (e.g. OX) o.id is the server's internal URL id, not the UID.
            uids = set()
            for o in objects:
                try:
                    uids.add(str(o.icalendar_component.get("UID", "")))
                except Exception:
                    pass
            found_old = "csc_olddate_event" in uids
            found_next = "csc_simple_event1" in uids
            if found_old and found_next:
                self.set_feature("search.unlimited-time-range")
            elif found_next:
                ## Returned the near-future fixture but not the far-past probe:
                ## classic sliding time window (broken, not unsupported).
                self.set_feature(
                    "search.unlimited-time-range",
                    {
                        "support": "broken",
                        "behaviour": "far-past objects (year 2000) are outside the search window",
                    },
                )
            elif objects:
                ## Did not even return the next-year fixture: the window is tighter
                ## than ~14 months ahead, so the near-future fixtures are unreliable.
                logging.error(
                    "search.unlimited-time-range: even next-year fixtures are outside this "
                    "server's search window - fixture-dependent check results are unreliable."
                )
                self.set_feature(
                    "search.unlimited-time-range",
                    {
                        "support": "broken",
                        "behaviour": "even next-year objects are outside the search window; "
                        "fixture-dependent checks are unreliable on this server",
                    },
                )
            else:
                ## Server returned nothing at all for a no-time-range search
                self.set_feature("search.unlimited-time-range", "unsupported")
        except (AuthorizationError, DAVError):
            self.set_feature("search.unlimited-time-range", "ungraceful")


class CheckIsNotDefined(Check):
    """
    Checks if the server supports is-not-defined searches (RFC4791 section 9.7.4).

    Tests whether searching for objects where a property is not defined works.
    Some servers support this for some properties but not others (e.g. DAViCal
    supports it for CLASS but not for CATEGORIES).
    """

    depends_on = {CheckSearch}
    features_to_be_checked = {
        "search.is-not-defined",
        "search.is-not-defined.category",
        "search.is-not-defined.class",
        "search.is-not-defined.dtend",
    }

    def _run_check(self):
        cal = self.checker.calendar

        ## Test no_category: csc_event_with_categories has CATEGORIES set,
        ## other events don't.  no_category=True should exclude it.
        category_works = None
        try:
            events_no_cat = cal.search(event=True, no_category=True, post_filter=False)
            uids = set()
            for e in events_no_cat:
                try:
                    uids.add(str(e.component.get("uid", "")))
                except Exception:
                    pass
            has_cat_event = "csc_event_with_categories" in uids
            if not has_cat_event and len(events_no_cat) >= 1:
                category_works = True
            else:
                category_works = False
        except (ReportError, AuthorizationError, DAVError):
            category_works = "ungraceful"

        ## Set the category-specific sub-feature
        if category_works == "ungraceful":
            self.set_feature("search.is-not-defined.category", "ungraceful")
        elif category_works is True:
            self.set_feature("search.is-not-defined.category")
        else:
            self.set_feature("search.is-not-defined.category", False)

        ## Test no_class: csc_event_with_class has CLASS:CONFIDENTIAL set,
        ## other events don't.  no_class=True should exclude it.
        class_works = None
        try:
            events_no_class = cal.search(event=True, no_class=True, post_filter=False)
            class_uids = set()
            for e in events_no_class:
                try:
                    class_uids.add(str(e.component.get("uid", "")))
                except Exception:
                    pass
            has_class_event = "csc_event_with_class" in class_uids
            if not has_class_event and len(events_no_class) >= 1:
                class_works = True
            else:
                class_works = False
        except (ReportError, AuthorizationError, DAVError):
            class_works = "ungraceful"

        ## Set the class-specific sub-feature
        if class_works == "ungraceful":
            self.set_feature("search.is-not-defined.class", "ungraceful")
        elif class_works is True:
            self.set_feature("search.is-not-defined.class")
        else:
            self.set_feature("search.is-not-defined.class", False)

        ## Test no_dtend: csc_event_with_duration uses DURATION (no DTEND),
        ## while csc_simple_event1 has explicit DTEND.
        ## no_dtend=True should include duration events and exclude dtend events.
        dtend_works = None
        try:
            events_no_dtend = cal.search(event=True, no_dtend=True, post_filter=False)
            dtend_uids = set()
            for e in events_no_dtend:
                try:
                    dtend_uids.add(str(e.component.get("uid", "")))
                except Exception:
                    pass
            has_dtend_event = "csc_simple_event1" in dtend_uids
            has_no_dtend_event = "csc_event_with_duration" in dtend_uids
            if not has_dtend_event and has_no_dtend_event:
                dtend_works = True
            else:
                dtend_works = False
        except (ReportError, AuthorizationError, DAVError):
            dtend_works = "ungraceful"

        ## Set the dtend-specific sub-feature
        if dtend_works == "ungraceful":
            self.set_feature("search.is-not-defined.dtend", "ungraceful")
        elif dtend_works is True:
            self.set_feature("search.is-not-defined.dtend")
        else:
            self.set_feature("search.is-not-defined.dtend", False)

        ## Determine overall is-not-defined support from sub-features
        results = {
            "category": category_works,
            "class": class_works,
            "dtend": dtend_works,
        }
        if all(v == "ungraceful" for v in results.values()):
            self.set_feature("search.is-not-defined", "ungraceful")
        elif all(v is True for v in results.values()):
            self.set_feature("search.is-not-defined")
        elif any(v is True for v in results.values()):
            working = [k for k, v in results.items() if v is True]
            self.set_feature(
                "search.is-not-defined",
                {"support": "fragile", "details": f"works for {', '.join(working)} but not all properties"},
            )
        else:
            self.set_feature("search.is-not-defined", False)


class CheckAlarmSearch(Check):
    depends_on = {CheckSearch}
    features_to_be_checked = {"search.time-range.alarm"}

    def _run_check(self):
        cal = self.checker.calendar
        base = self.checker.fixture_base_year

        ## Check that the alarm event was created successfully
        try:
            obj = cal.object_by_uid("csc_event_with_alarm")
        except Exception:
            self.set_feature("search.time-range.alarm", False)
            return

        ## The alarm event has dtstart <base>-01-10 08:00 and a -15min alarm,
        ## so the alarm triggers at 07:45.

        ## Search that SHOULD find the alarm (07:40-07:55 covers 07:45)
        try:
            events = cal.search(
                event=True,
                alarm_start=datetime(base, 1, 10, 7, 40, tzinfo=utc),
                alarm_end=datetime(base, 1, 10, 7, 55, tzinfo=utc),
                post_filter=False,
            )
        except (AuthorizationError, DAVError):
            self.set_feature("search.time-range.alarm", "ungraceful")
            return

        if len(events) != 1:
            self.set_feature("search.time-range.alarm", False)
            return

        ## Search that should NOT find the alarm (08:00-08:15 is after trigger)
        try:
            events = cal.search(
                event=True,
                alarm_start=datetime(base, 1, 10, 8, 0, tzinfo=utc),
                alarm_end=datetime(base, 1, 10, 8, 15, tzinfo=utc),
                post_filter=False,
            )
        except (AuthorizationError, DAVError):
            self.set_feature("search.time-range.alarm", "ungraceful")
            return

        self.set_feature("search.time-range.alarm", len(events) == 0)


class CheckRecurrenceSearch(Check):
    depends_on = {CheckSearch}
    features_to_be_checked = {
        "search.recurrences.includes-implicit.todo",
        "search.recurrences.includes-implicit.todo.pending",
        "search.recurrences.includes-implicit.event",
        "search.recurrences.includes-implicit.infinite-scope",
        "search.recurrences.expanded.todo",
        "search.recurrences.expanded.event",
        "search.recurrences.expanded.exception",
    }

    def _run_check(self):
        cal = self.checker.calendar
        tl = self.checker.tasklist
        base = self.checker.fixture_base_year

        ## Precondition: basic event time-range search must return exactly the
        ## one recurring event in Jan <base>.  On servers with broken comp-type
        ## filtering (e.g. Bedework) this may return extra objects, making
        ## recurrence checks unreliable.  Some servers (e.g. CCS) reject
        ## some date ranges entirely.  Either way, mark all features unsupported.
        try:
            events = cal.search(
                start=datetime(base, 1, 12, tzinfo=utc),
                end=datetime(base, 1, 13, tzinfo=utc),
                event=True,
                post_filter=False,
            )
        except (AuthorizationError, DAVError):
            events = []
        if len(events) != 1:
            for feat in self.features_to_be_checked:
                self.set_feature(feat, False)
            return

        ## Whether a VTODO time-range search reliably returns just the one recurring
        ## todo.  Some servers (e.g. OX) ignore the time-range for VTODOs and return
        ## every task, so the todo recurrence sub-features can't be measured there.
        ## We mark those todo sub-features unsupported but STILL run the
        ## event-recurrence checks below (event time-range may work fine).
        todo_testable = False
        if self.checker.features_checked.is_supported("search.time-range.todo"):
            try:
                todos = tl.search(
                    start=datetime(base, 1, 12, tzinfo=utc),
                    end=datetime(base, 1, 13, tzinfo=utc),
                    todo=True,
                    include_completed=True,
                    post_filter=False,
                )
                todo_testable = len(todos) == 1
            except (AuthorizationError, DAVError):
                todo_testable = False
        if not todo_testable:
            logging.warning(
                "Recurring-todo precondition not met (VTODO time-range search unreliable in Jan %d); "
                "todo recurrence sub-features reported unsupported",
                base,
            )

        events = cal.search(
            start=datetime(base, 2, 12, tzinfo=utc),
            end=datetime(base, 2, 13, tzinfo=utc),
            event=True,
            post_filter=False,
        )
        implicit_datetime = len(events) == 1
        ## Also check all-day (VALUE=DATE) recurring events: the yearly event
        ## (DTSTART;VALUE=DATE:<base>-02-01) should be found in <base+1>-02-01 range.
        try:
            allday_events = cal.search(
                start=datetime(base + 1, 2, 1, tzinfo=utc),
                end=datetime(base + 1, 2, 2, tzinfo=utc),
                event=True,
                post_filter=False,
            )
            implicit_allday = len(allday_events) == 1
        except (AuthorizationError, DAVError):
            implicit_allday = implicit_datetime
        if implicit_datetime and not implicit_allday:
            ## Datetime recurring events work but all-day (VALUE=DATE) events do not.
            ## On a sliding-window server the all-day event's second occurrence (next
            ## year + 1) may simply be out of window rather than truly broken, so this
            ## verdict can be a false "fragile" on such servers - acceptable.
            self.set_feature(
                "search.recurrences.includes-implicit.event",
                {"support": "fragile", "behaviour": "broken for all-day (VALUE=DATE) events"},
            )
        else:
            self.set_feature("search.recurrences.includes-implicit.event", implicit_datetime)

        if todo_testable:
            todos1 = tl.search(
                start=datetime(base, 2, 12, tzinfo=utc),
                end=datetime(base, 2, 13, tzinfo=utc),
                todo=True,
                include_completed=True,
                post_filter=False,
            )
            self.set_feature("search.recurrences.includes-implicit.todo", len(todos1) == 1)
            if todos1:
                todos2 = tl.search(
                    start=datetime(base, 2, 12, tzinfo=utc),
                    end=datetime(base, 2, 13, tzinfo=utc),
                    todo=True,
                    post_filter=False,
                )
                self.set_feature("search.recurrences.includes-implicit.todo.pending", len(todos2) == 1)
            else:
                self.set_feature("search.recurrences.includes-implicit.todo.pending", False)
        else:
            self.set_feature("search.recurrences.includes-implicit.todo", False)
            self.set_feature("search.recurrences.includes-implicit.todo.pending", False)

        exception = cal.search(
            start=datetime(base, 2, 13, 11, tzinfo=utc),
            end=datetime(base, 2, 13, 13, tzinfo=utc),
            event=True,
            post_filter=False,
        )
        if len(exception) != 1:
            ## Can't reliably check expansion/exception features.  The
            ## includes-implicit.{event,todo,todo.pending} features were already
            ## set above; set the remaining (still-unmeasured) ones unsupported.
            ## (A blanket "if not feature_checked" loop would wrongly skip features
            ## whose spec default is "full", since is_supported() returns the
            ## default for an as-yet-unset feature.)
            for feat in (
                "search.recurrences.includes-implicit.infinite-scope",
                "search.recurrences.expanded.event",
                "search.recurrences.expanded.todo",
                "search.recurrences.expanded.exception",
            ):
                self.set_feature(feat, False)
            return
        ## Far-future occurrence of the monthly recurring event, to test whether
        ## implicit recurrence has an unlimited scope.  Deliberately decades out
        ## (and well beyond any sliding window), so window-restricted servers will
        ## correctly report this as unsupported.
        far_future_recurrence = cal.search(
            start=datetime(2045, 3, 12, tzinfo=utc),
            end=datetime(2045, 3, 13, tzinfo=utc),
            event=True,
            post_filter=False,
        )
        self.set_feature("search.recurrences.includes-implicit.infinite-scope", len(far_future_recurrence) == 1)

        ## server-side expansion
        events = cal.search(
            start=datetime(base, 2, 12, tzinfo=utc),
            end=datetime(base, 2, 13, tzinfo=utc),
            event=True,
            server_expand=True,
            post_filter=False,
        )
        self.set_feature(
            "search.recurrences.expanded.event",
            len(events) == 1 and events[0].component["dtstart"] == datetime(base, 2, 12, 12, 0, 0, tzinfo=utc),
        )
        if todo_testable:
            todos = cal.search(
                start=datetime(base, 2, 12, tzinfo=utc),
                end=datetime(base, 2, 13, tzinfo=utc),
                todo=True,
                server_expand=True,
                post_filter=False,
            )
            self.set_feature(
                "search.recurrences.expanded.todo",
                len(todos) == 1 and todos[0].component["dtstart"] == datetime(base, 2, 12, 12, 0, 0, tzinfo=utc),
            )
        else:
            self.set_feature("search.recurrences.expanded.todo", False)
        exception = cal.search(
            start=datetime(base, 2, 13, 11, tzinfo=utc),
            end=datetime(base, 2, 13, 13, tzinfo=utc),
            event=True,
            server_expand=True,
            post_filter=False,
        )
        self.set_feature(
            "search.recurrences.expanded.exception",
            len(exception) == 1
            and exception[0].component["dtstart"] == datetime(base, 2, 13, 12, 0, 0, tzinfo=utc)
            and exception[0].component["summary"] == "February recurrence with different summary"
            and getattr(exception[0].component.get("RECURRENCE-ID"), "dt", None)
            == datetime(base, 2, 13, 12, tzinfo=utc),
        )

        ## RFC 4791 §9.6.5: non-initial expanded instances MUST include RECURRENCE-ID;
        ## the initial instance MAY omit it.  Query a range covering both the regular
        ## Feb 12 occurrence and the Feb 13 exception to detect servers that omit
        ## RECURRENCE-ID on the initial instance, and annotate the expanded.event feature.
        multi = cal.search(
            start=datetime(base, 2, 12, tzinfo=utc),
            end=datetime(base, 2, 14, tzinfo=utc),
            event=True,
            server_expand=True,
            post_filter=False,
        )
        if len(multi) == 2:
            initial = next(
                (e for e in multi if e.component["dtstart"] == datetime(base, 2, 12, 12, 0, 0, tzinfo=utc)),
                None,
            )
            if initial is not None and getattr(initial.component.get("RECURRENCE-ID"), "dt", None) is None:
                self.set_feature(
                    "search.recurrences.expanded.event",
                    {
                        "behaviour": "initial occurrence lacks RECURRENCE-ID; RFC 4791 §9.6.5 permits this — clients must fall back to DTSTART"
                    },
                )


class CheckCaseSensitiveSearch(Check):
    """
    Checks if the server supports case-sensitive and case-insensitive text searches.

    RFC4791 section 9.7.5 specifies that i;ascii-casemap MUST be the default collation,
    and section 7.5 says servers are REQUIRED to support i;octet (case-sensitive).
    """

    depends_on = {CheckSearch}
    features_to_be_checked = {
        "search.text.case-sensitive",
        "search.text.case-insensitive",
    }

    def _run_check(self):
        cal = self.checker.calendar

        ## The PrepareCalendar check created an event with summary
        ## "simple event with a start time and an end time" (uid csc_simple_event1).
        ## We search for "Simple" (uppercase S) vs "simple" (lowercase s)
        ## to test case sensitivity.

        text_search_filters = True

        ## Case-sensitive search (i;octet collation):
        ## Searching for "Simple" (uppercase S) should NOT match
        ## "simple event ..." (lowercase s).
        ## Using post_filter=False to test server-side behaviour.
        try:
            searcher = CalDAVSearcher(event=True)
            searcher.add_property_filter("SUMMARY", "Simple", case_sensitive=True)
            results_sensitive = searcher.search(cal, post_filter=False)

            searcher2 = CalDAVSearcher(event=True)
            searcher2.add_property_filter("SUMMARY", "simple", case_sensitive=True)
            results_sensitive_match = searcher2.search(cal, post_filter=False)

            ## "Simple" should not match "simple event ...", but "simple" should
            self.set_feature(
                "search.text.case-sensitive", len(results_sensitive) == 0 and len(results_sensitive_match) >= 1
            )

            ## If more than one result is returned for "Simple" (only one event
            ## has "simple" in the summary), the server is not properly filtering
            ## text searches (e.g., robur ignores text filters and returns all
            ## events).  In that case, case-insensitive tests are meaningless.
            if len(results_sensitive) > 1:
                text_search_filters = False
        except (ReportError, DAVError):
            self.set_feature("search.text.case-sensitive", "ungraceful")
            text_search_filters = False

        ## Case-insensitive search (i;ascii-casemap collation):
        ## Searching for "SIMPLE" should match "simple event ..."
        ## when case_sensitive=False.
        ## Skip if text search doesn't filter at all (no point testing
        ## case sensitivity when the server ignores text filters).
        if not text_search_filters:
            self.set_feature("search.text.case-insensitive", False)
            return

        try:
            searcher3 = CalDAVSearcher(event=True)
            searcher3.add_property_filter("SUMMARY", "SIMPLE", case_sensitive=False)
            results_insensitive = searcher3.search(cal, post_filter=False)

            self.set_feature("search.text.case-insensitive", len(results_insensitive) >= 1)
        except (ReportError, DAVError):
            self.set_feature("search.text.case-insensitive", "ungraceful")


class CheckSubstringSearch(Check):
    """
    Checks if the server supports substring text search (text-match with
    match-type="contains").

    Some servers (e.g. Zimbra) accept the REPORT but only do exact match,
    ignoring the contains match-type.
    """

    depends_on = {CheckSearch}
    features_to_be_checked = {
        "search.text.substring",
    }

    def _run_check(self):
        cal = self.checker.calendar

        try:
            ## First, verify text search works at all by searching for
            ## the full summary (should match regardless of substring support)
            searcher_exact = CalDAVSearcher(event=True)
            searcher_exact.add_property_filter("SUMMARY", "simple event with a start time and an end time")
            results_exact = searcher_exact.search(cal, post_filter=False)

            if len(results_exact) != 1:
                ## Text search doesn't filter properly, can't determine
                ## substring support
                self.set_feature("search.text.substring", None)
                return

            ## Now search for a substring of the same summary
            searcher_sub = CalDAVSearcher(event=True)
            searcher_sub.add_property_filter("SUMMARY", "simple event")
            results_sub = searcher_sub.search(cal, post_filter=False)

            self.set_feature("search.text.substring", len(results_sub) == 1)
        except (ReportError, DAVError):
            self.set_feature("search.text.substring", "ungraceful")


class CheckPrincipalSearch(Check):
    """
    Checks if the server supports principal search operations.

    Uses DAVClient.search_principals() which sends a
    DAV:principal-property-search REPORT.
    """

    depends_on = {CheckGetCurrentUserPrincipal}
    features_to_be_checked = {
        "principal-search",
        "principal-search.by-name.self",
        "principal-search.list-all",
    }

    def _run_check(self):
        principal = self.checker.principal
        if not principal:
            self.set_feature("principal-search", False)
            return

        ## Get the display name of the current principal for self-search.
        ## Fall back to the username if no display name is available.
        try:
            search_name = principal.get_display_name()
        except Exception:
            search_name = None
        if not search_name:
            search_name = getattr(self.client, "username", None)

        any_search_worked = False
        any_ungraceful = False

        ## Use search_principals (v3.0+) or principals (v2.x) method
        _search_principals = getattr(self.client, "search_principals", None) or getattr(self.client, "principals", None)
        if not _search_principals:
            self.set_feature("principal-search", False)
            self.set_feature("principal-search.by-name.self", False)
            self.set_feature("principal-search.list-all", False)
            return

        ## principal-search.by-name.self: search for own principal by name
        if search_name:
            try:
                results = _search_principals(name=search_name)
                found_self = any(isinstance(r, Principal) for r in results)
                self.set_feature("principal-search.by-name.self", found_self)
                if found_self:
                    any_search_worked = True
            except (ReportError, DAVError):
                self.set_feature("principal-search.by-name.self", "ungraceful")
                any_ungraceful = True
        else:
            self.set_feature("principal-search.by-name.self", None)

        ## principal-search.list-all: list all principals without filter
        try:
            results = _search_principals()
            found_any = len(results) >= 1
            self.set_feature("principal-search.list-all", found_any)
            if found_any:
                any_search_worked = True
        except (ReportError, DAVError):
            self.set_feature("principal-search.list-all", "ungraceful")
            any_ungraceful = True

        ## principal-search: overall support derived from sub-features
        if any_search_worked:
            self.set_feature("principal-search")
        elif any_ungraceful:
            self.set_feature("principal-search", "ungraceful")
        else:
            self.set_feature("principal-search", False)


class CheckDuplicateUID(Check):
    """
    Checks how server handles events with duplicate UIDs across calendars.

    Some servers allow the same UID in different calendars (treating them
    as separate entities), while others may throw errors or silently ignore
    duplicates.

    Tests:
    - save.duplicate-uid.cross-calendar: Can events with same UID exist in different calendars?
    """

    depends_on = {PrepareCalendar}
    features_to_be_checked = {"save.duplicate-uid.cross-calendar"}

    def _run_check(self) -> None:
        cal1 = self.checker.calendar
        base = self.checker.fixture_base_year

        ## Reuse an event from PrepareCalendar instead of creating a new one
        test_uid = "csc_simple_event1"
        cal2_name = "csc_duplicate_uid_cal2"

        ## Try to find and delete existing cal2 test calendar
        try:
            for cal in self.client.principal().calendars():
                if cal.name == cal2_name:
                    cal.delete()
                    break
        except Exception:
            pass

        try:
            ## Get existing event from first calendar (created by PrepareCalendar).
            ## Fall back to direct URL lookup if the server's REPORT/search can't
            ## find the event (indexing delay, canonicalized URL) or refuses the
            ## query outright (OX answers a UID calendar-query with 403 Forbidden).
            try:
                event1 = cal1.event_by_uid(test_uid)
            except (NotFoundError, AuthorizationError, DAVError):
                event1 = Event(cal1.client, url=cal1.url.join(test_uid + ".ics"), parent=cal1)
            event1.load()  ## csc_simple_event1 now lives in the near future (next year)

            ## Get the event data for reuse in cal2
            event_ical = event1.data

            ## Create second calendar
            try:
                cal2 = self.client.principal().make_calendar(name=cal2_name)
            except DAVError:
                self.set_feature(
                    "save.duplicate-uid.cross-calendar",
                    {"support": "unknown", "behaviour": "cannot test, have access to only one calendar"},
                )
                return

            try:
                ## Try to save event with same UID to second calendar
                event2 = cal2.save_object(Event, event_ical)

                ## Check if the event actually exists in cal2
                events_in_cal2 = list(_filter_fixture_window(cal2.events(), base))

                ## Check if event still exists in cal1 (Zimbra moves it instead of copying)
                try:
                    cal1.event_by_uid(test_uid)
                    event_was_moved = False
                except NotFoundError:
                    event_was_moved = True

                if len(events_in_cal2) == 0:
                    ## Server silently ignored the duplicate
                    self.set_feature(
                        "save.duplicate-uid.cross-calendar", {"support": "unsupported", "behaviour": "silently-ignored"}
                    )
                elif len(events_in_cal2) == 1 and event_was_moved:
                    ## Server moved the event instead of creating a duplicate (Zimbra behavior)
                    self.set_feature(
                        "save.duplicate-uid.cross-calendar",
                        {"support": "unsupported", "behaviour": "moved-instead-of-copied"},
                    )
                    ## Move event back to cal1 to avoid breaking other tests
                    cal1.save_event(event2.data)
                elif len(events_in_cal2) == 1:
                    if events_in_cal2[0].component["uid"] != test_uid:
                        logging.error("Unexpected UID in duplicate-uid cross-calendar test; skipping")
                        return
                    ## Server accepted the duplicate
                    ## Verify they are treated as separate entities.
                    event1 = cal1.event_by_uid(test_uid)
                    event1.load()

                    ## Store original summary to check later
                    original_summary = str(event1.icalendar_instance.walk("vevent")[0].get("summary", ""))

                    ## Modify event in cal2 and verify cal1's event is unchanged
                    event2.icalendar_instance.walk("vevent")[0]["summary"] = "Modified in Cal2"
                    event2.save()

                    event1.load()
                    current_summary = str(event1.icalendar_instance.walk("vevent")[0].get("summary", ""))
                    if current_summary == original_summary:
                        self.set_feature("save.duplicate-uid.cross-calendar", True)
                    else:
                        self.set_feature(
                            "save.duplicate-uid.cross-calendar",
                            {
                                "support": "fragile",
                                "behaviour": "Modifying duplicate in one calendar affects the other",
                            },
                        )
                else:
                    self.set_feature(
                        "save.duplicate-uid.cross-calendar",
                        {"support": "fragile", "behaviour": f"Unexpected: {len(events_in_cal2)} events in cal2"},
                    )

            except (DAVError, AuthorizationError) as e:
                ## Server rejected the duplicate with an error
                self.set_feature(
                    "save.duplicate-uid.cross-calendar",
                    {"support": "ungraceful", "behaviour": f"Server error: {type(e).__name__}"},
                )
            finally:
                ## Cleanup
                try:
                    cal2.delete()
                except Exception:
                    pass

        finally:
            ## No need to cleanup test event - it's owned by PrepareCalendar
            pass


class CheckSyncToken(Check):
    """
    Checks support for RFC6578 sync-collection reports (sync tokens)

    Tests for four known issues:
    1. No sync token support at all
    2. Time-based sync tokens (second-precision, requires sleep between ops)
    3. Fragile sync tokens (returns extra content, race conditions)
    4. Sync breaks on delete (server fails after object deletion)
    """

    depends_on = {PrepareCalendar}
    features_to_be_checked = {
        "sync-token",
        "sync-token.delete",
    }

    def _run_check(self) -> None:
        cal = self.checker.calendar
        base = self.checker.fixture_base_year

        ## Test 1: Check if sync tokens are supported at all
        ## Use disable_fallback=True to detect true server support
        try:
            my_objects = cal.objects(disable_fallback=True)
            sync_token = my_objects.sync_token

            if not sync_token or sync_token == "":
                self.set_feature("sync-token", False)
                return

            ## Initially assume full support
            sync_support = "full"
            sync_behaviour = None
        except (ReportError, DAVError, AttributeError) as e:
            self.set_feature(
                "sync-token",
                {"support": "ungraceful", "behaviour": f"Server error on sync-collection REPORT: {type(e).__name__}"},
            )
            return

        ## Test 2 & 3: Check for time-based and fragile sync tokens
        ## Use unique UID for sync test since this test deletes the event
        ## (Nextcloud trashbin bug - see https://github.com/nextcloud/server/issues/30096)
        test_uid = f"csc_sync_test_event_{int(time.time() * 1000)}"

        ## Create a new event
        test_event = None
        try:
            test_event = cal.save_object(
                Event,
                summary="Sync token test event",
                uid=test_uid,
                dtstart=datetime(base, 4, 1, 12, 0, 0, tzinfo=utc),
                dtend=datetime(base, 4, 1, 13, 0, 0, tzinfo=utc),
            )

            ## Get objects with new sync token
            my_objects = cal.objects(disable_fallback=True)
            sync_token1 = my_objects.sync_token

            ## Immediately check for changes (should be none)
            my_changed_objects = cal.objects_by_sync_token(sync_token=sync_token1, disable_fallback=True)
            immediate_count = len(list(my_changed_objects))

            if immediate_count > 0:
                ## Fragile sync tokens return extra content
                sync_support = "fragile"

            ## Test for time-based sync tokens
            ## Modify the event within the same second
            test_event.icalendar_instance.subcomponents[0]["SUMMARY"] = "Modified immediately"
            test_event.save()

            ## Check for changes immediately (time-based tokens need sleep(1))
            my_changed_objects = cal.objects_by_sync_token(sync_token=sync_token1, disable_fallback=True)
            changed_count_no_sleep = len(list(my_changed_objects))

            if changed_count_no_sleep == 0:
                ## Might be time-based, wait a second and try again
                time.sleep(1)
                test_event.icalendar_instance.subcomponents[0]["SUMMARY"] = "Modified after sleep"
                test_event.save()
                time.sleep(1)

                my_changed_objects = cal.objects_by_sync_token(sync_token=sync_token1, disable_fallback=True)
                changed_count_with_sleep = len(list(my_changed_objects))

                if changed_count_with_sleep >= 1:
                    sync_behaviour = "time-based"
                else:
                    ## Sync tokens might be completely broken
                    sync_support = "broken"

            ## Set the sync-token feature with support and behaviour
            if sync_behaviour:
                self.set_feature("sync-token", {"support": sync_support, "behaviour": sync_behaviour})
            else:
                self.set_feature("sync-token", {"support": sync_support})

            ## Test 4: Check if sync breaks on delete
            sync_token2 = my_changed_objects.sync_token

            ## Sleep if needed
            if sync_behaviour == "time-based":
                time.sleep(1)

            ## Delete the test event
            test_event.delete()
            test_event = None  ## Mark as deleted

            if sync_behaviour == "time-based":
                time.sleep(1)

            try:
                my_changed_objects = cal.objects_by_sync_token(sync_token=sync_token2, disable_fallback=True)
                deleted_count = len(list(my_changed_objects))

                ## If we get here without exception, deletion is supported
                self.set_feature("sync-token.delete", True)
            except (ReportError, DAVError) as e:
                ## Some servers (like sabre-based) return "418 I'm a teapot" or other errors
                self.set_feature(
                    "sync-token.delete", {"support": "unsupported", "behaviour": f"sync fails after deletion: {e}"}
                )
        finally:
            ## Ensure cleanup even if an exception occurred
            if test_event is not None:
                try:
                    test_event.delete()
                except Exception:
                    pass


class CheckFreeBusyQuery(Check):
    """
    Checks support for RFC4791 free/busy-query REPORT

    Tests if the server supports free/busy queries as specified in RFC4791 section 7.10.
    The free/busy query allows clients to retrieve free/busy information for a time range.
    """

    depends_on = {PrepareCalendar}
    features_to_be_checked = {
        "freebusy-query",
    }

    def _run_check(self) -> None:
        cal = self.checker.calendar
        base = self.checker.fixture_base_year

        try:
            ## Try to perform a simple freebusy query
            ## Use the fixture month (next year, Jan) which overlaps the fixtures
            ## and stays inside sliding-window servers' search windows
            start = datetime(base, 1, 1, 0, 0, 0, tzinfo=utc)
            end = datetime(base, 1, 31, 23, 59, 59, tzinfo=utc)

            freebusy = cal.freebusy_request(start, end)

            ## If we got here without exception, the feature is supported
            ## Verify we got a valid freebusy object
            if freebusy and hasattr(freebusy, "vobject_instance"):
                self.set_feature("freebusy-query", True)
            else:
                self.set_feature(
                    "freebusy-query",
                    {"support": "unsupported", "behaviour": "freebusy query returned invalid or empty response"},
                )
        except (ReportError, DAVError, NotFoundError) as e:
            ## Server doesn't support freebusy queries
            ## Common responses: 500 Internal Server Error, 501 Not Implemented
            self.set_feature("freebusy-query", {"support": "ungraceful", "behaviour": f"freebusy query failed: {e}"})
        except Exception as e:
            ## Unexpected error
            self.set_feature(
                "freebusy-query",
                {"support": "broken", "behaviour": f"unexpected error during freebusy query: {e}"},
            )


class CheckScheduling(Check):
    """
    Checks support for CalDAV Scheduling (RFC6638).

    Calls client.supports_scheduling() to detect whether the server
    advertises scheduling support.
    """

    features_to_be_checked = {"scheduling"}

    def _run_check(self) -> None:
        self.set_feature("scheduling", self.client.supports_scheduling())


class CheckSchedulingDetails(Check):
    """
    Checks RFC6638 scheduling sub-features: mailbox (inbox/outbox) and
    calendar-user-address-set.  Depends on CheckScheduling; when scheduling
    is unsupported both sub-features are recorded as unsupported immediately.
    """

    depends_on = {CheckScheduling, CheckGetCurrentUserPrincipal}
    features_to_be_checked = {"scheduling.mailbox", "scheduling.calendar-user-address-set"}

    def _run_check(self) -> None:
        if not self.feature_checked("scheduling"):
            self.set_feature("scheduling.mailbox", False)
            self.set_feature("scheduling.calendar-user-address-set", False)
            return

        principal = self.checker.principal
        if principal is None:
            self.set_feature("scheduling.mailbox", {"support": "unknown"})
            self.set_feature("scheduling.calendar-user-address-set", {"support": "unknown"})
            return

        ## Check inbox + outbox
        try:
            principal.schedule_inbox()
            principal.schedule_outbox()
            self.set_feature("scheduling.mailbox", True)
        except NotFoundError:
            self.set_feature("scheduling.mailbox", False)
        except Exception as e:
            self.set_feature("scheduling.mailbox", {"support": "broken", "behaviour": str(e)})

        ## Check calendar-user-address-set
        try:
            principal.calendar_user_address_set()
            self.set_feature("scheduling.calendar-user-address-set", True)
        except NotFoundError:
            self.set_feature("scheduling.calendar-user-address-set", False)
        except Exception as e:
            self.set_feature(
                "scheduling.calendar-user-address-set",
                {"support": "broken", "behaviour": str(e)},
            )


class CheckFreeBusyQueryRFC6638(Check):
    """
    Checks support for RFC6638 freebusy query via the schedule outbox (section 4.1).

    POSTs a VFREEBUSY REQUEST to the principal's schedule outbox listing an
    attendee, and checks whether the server responds without error.  When a
    second principal is available (via extra_principals), queries that
    principal's free/busy — a more realistic cross-user scenario.  Reports
    unknown when no second principal is configured, since RFC6638 is a
    multi-user protocol and a self-query would give unreliable results.

    Distinct from CheckFreeBusyQuery which uses a REPORT against a
    calendar collection.  Requires scheduling and scheduling.mailbox.
    """

    depends_on = {CheckSchedulingDetails}
    features_to_be_checked = {"scheduling.freebusy-query"}

    def _run_check(self) -> None:
        if not self.feature_checked("scheduling") or not self.feature_checked("scheduling.mailbox"):
            self.set_feature("scheduling.freebusy-query", False)
            return

        principal = self.checker.principal
        if principal is None:
            self.set_feature("scheduling.freebusy-query", {"support": "unknown"})
            return

        ## Determine the attendee address to query.
        ## Prefer a second principal (cross-user scenario); fall back to self-query.
        extra_principals = self.checker.extra_principals
        if extra_principals:
            attendee_principal = extra_principals[0]
            try:
                attendee_address = attendee_principal.get_vcal_address()
            except Exception:
                attendee_username = getattr(attendee_principal.client, "username", None)
                attendee_address = (
                    "mailto:" + attendee_username if attendee_username and "@" in str(attendee_username) else None
                )
            if not attendee_address:
                self.set_feature("scheduling.freebusy-query", {"support": "unknown"})
                return
        else:
            ## No second principal — cannot perform a meaningful cross-user probe.
            ## RFC6638 is a multi-user protocol; mark as unknown rather than
            ## testing against ourselves (self-queries may produce false results).
            ## (The early return above already handles the no-scheduling case.)
            self.set_feature(
                "scheduling.freebusy-query",
                {
                    "support": "unknown",
                    "behaviour": "not tested: only one user configured; server claims scheduling support",
                },
            )
            return

        ## This check does not depend on PrepareCalendar, so derive the base year
        ## directly; the near-future range avoids window restrictions on free-busy.
        base = getattr(self.checker, "fixture_base_year", None) or _base_year()
        dtstart = datetime(base, 1, 9, 0, 0, 0, tzinfo=utc)
        dtend = datetime(base, 1, 10, 0, 0, 0, tzinfo=utc)

        try:
            principal.freebusy_request(dtstart, dtend, [attendee_address])
            self.set_feature("scheduling.freebusy-query")
        except (AuthorizationError, DAVError, NotFoundError) as e:
            self.set_feature("scheduling.freebusy-query", {"support": "ungraceful", "behaviour": str(e)})
        except Exception as e:
            self.set_feature("scheduling.freebusy-query", {"support": "broken", "behaviour": str(e)})


class CheckScheduleTag(Check):
    """
    Checks support for the Schedule-Tag response header and property (RFC6638 sections 3.2-3.3).

    Creates a scheduling object resource (a VEVENT with an ORGANIZER property),
    GETs it back and checks whether the server returns a Schedule-Tag response
    header (captured into props by caldav's load()) and exposes the schedule-tag
    DAV property via PROPFIND, as mandated by RFC6638 section 3.2.

    This check is skipped when the server does not advertise CalDAV scheduling
    support, since Schedule-Tag is only required on scheduling object resources.
    """

    depends_on = {CheckSchedulingDetails, PrepareCalendar}
    features_to_be_checked = {"scheduling.schedule-tag"}

    def _run_check(self) -> None:
        if not self.feature_checked("scheduling"):
            self.set_feature("scheduling.schedule-tag", False)
            return

        ## Resolve the authenticated user's calendar address so that the probe
        ## event has an ORGANIZER that matches the session.  Servers like Stalwart
        ## only assign a Schedule-Tag when the ORGANIZER equals the authenticated
        ## user; a fake address causes the PUT to succeed but return no tag.
        principal = self.checker.principal
        if self.feature_checked("scheduling.calendar-user-address-set"):
            try:
                own_address = str(principal.get_vcal_address())
            except Exception:
                self.set_feature("scheduling.schedule-tag", {"support": "unknown"})
                return
        else:
            username = getattr(self.client, "username", None)
            if not username or "@" not in str(username):
                self.set_feature("scheduling.schedule-tag", {"support": "unknown"})
                return
            own_address = "mailto:" + username

        cal = self.checker.calendar
        probe_uid = f"csc_schedule_tag_probe_{uuid.uuid4().hex}"
        ## Resolve a second attendee address. Prefer a real local account (so
        ## servers like Stalwart trigger full scheduling semantics) and fall back
        ## to a dummy address for single-account setups.
        extra_principals = self.checker.extra_principals
        if extra_principals:
            try:
                attendee_address = str(extra_principals[0].get_vcal_address())
            except Exception:
                attendee_username = getattr(extra_principals[0].client, "username", None)
                attendee_address = (
                    "mailto:" + attendee_username
                    if attendee_username and "@" in str(attendee_username)
                    else "mailto:csc-probe-attendee@example.com"
                )
        else:
            attendee_address = "mailto:csc-probe-attendee@example.com"

        ## Minimal scheduling object resource: a VEVENT with an ORGANIZER property.
        ## Per RFC6638 section 2.3.4, the presence of ORGANIZER makes this a
        ## scheduling object resource and obliges the server to return Schedule-Tag.
        ## The ORGANIZER must match the authenticated user; the second ATTENDEE is a
        ## real local account when available so that servers like Stalwart apply full
        ## scheduling semantics and assign a Schedule-Tag.
        probe_ical = (
            "BEGIN:VCALENDAR\r\n"
            "VERSION:2.0\r\n"
            "PRODID:-//caldav-server-tester//test//EN\r\n"
            "BEGIN:VEVENT\r\n"
            f"UID:{probe_uid}\r\n"
            "DTSTART:20300101T100000Z\r\n"
            "DTEND:20300101T110000Z\r\n"
            "SUMMARY:caldav-server-tester schedule-tag probe\r\n"
            f"ORGANIZER:{own_address}\r\n"
            f"ATTENDEE;RSVP=TRUE;PARTSTAT=NEEDS-ACTION:{own_address}\r\n"
            f"ATTENDEE;RSVP=TRUE;PARTSTAT=NEEDS-ACTION:{attendee_address}\r\n"
            "END:VEVENT\r\n"
            "END:VCALENDAR\r\n"
        )

        try:
            event = cal.add_event(probe_ical)

            ## RFC6638 s3.2: server MUST return Schedule-Tag on successful PUT.
            if event.schedule_tag:
                self.set_feature("scheduling.schedule-tag")
                return

            self.set_feature(
                "scheduling.schedule-tag",
                {
                    "support": "unsupported",
                    "behaviour": "server did not return Schedule-Tag header on GET or via PROPFIND",
                },
            )
        except (DAVError, PutError) as e:
            self.set_feature("scheduling.schedule-tag", {"support": "ungraceful", "behaviour": str(e)})
        except Exception as e:
            self.set_feature("scheduling.schedule-tag", {"support": "broken", "behaviour": str(e)})
        finally:
            try:
                cal.object_by_uid(probe_uid).delete()
            except Exception:
                pass


class CheckSchedulingInboxDelivery(Check):
    """
    Checks two related scheduling features:
      scheduling.mailbox.inbox-delivery – whether incoming iTIP REQUEST messages
        appear in the attendee's schedule-inbox (RFC6638 section 4.1).
      scheduling.auto-schedule – whether the server automatically processes
        iTIP REQUESTs and adds the event to the attendee's calendar without
        requiring explicit inbox acceptance (RFC6638 SCHEDULE-AGENT=SERVER).

    When a second principal is available (via ServerQuirkChecker.extra_principals),
    uses a cross-user probe: the main user invites the second user and checks
    both the inbox and the attendee's calendar.  Reports unknown for both
    features when no second principal is configured, since RFC6638 is a
    multi-user protocol and self-invite results are unreliable (some servers
    skip self-invite delivery entirely, which RFC6638 permits).
    """

    depends_on = {CheckSchedulingDetails, PrepareCalendar}
    features_to_be_checked = {"scheduling.mailbox.inbox-delivery", "scheduling.auto-schedule"}

    def _run_check(self) -> None:
        if not self.feature_checked("scheduling") or not self.feature_checked("scheduling.mailbox"):
            self.set_feature("scheduling.mailbox.inbox-delivery", False)
            self.set_feature("scheduling.auto-schedule", False)
            return

        principal = self.checker.principal

        ## Determine own address for composing the probe invite.
        ## Prefer calendar-user-address-set; fall back to the client username
        ## when it is unavailable (mirrors the fix for
        ## https://github.com/python-caldav/caldav/issues/399).
        if self.feature_checked("scheduling.calendar-user-address-set"):
            try:
                own_address = principal.get_vcal_address()
            except Exception:
                self.set_feature("scheduling.mailbox.inbox-delivery", {"support": "unknown"})
                self.set_feature("scheduling.auto-schedule", {"support": "unknown"})
                return
        else:
            username = getattr(self.client, "username", None)
            if not username or "@" not in str(username):
                ## No address source available; cannot compose probe invite.
                self.set_feature("scheduling.mailbox.inbox-delivery", {"support": "unknown"})
                self.set_feature("scheduling.auto-schedule", {"support": "unknown"})
                return
            own_address = "mailto:" + username

        ## Decide probe mode: cross-user (preferred) or self-invite (fallback)
        extra_principals = self.checker.extra_principals
        if extra_principals:
            attendee_principal = extra_principals[0]
            try:
                attendee_address = attendee_principal.get_vcal_address()
            except Exception:
                ## Fall back to attendee's username when calendar-user-address-set
                ## is unavailable on that account too.
                attendee_username = getattr(attendee_principal.client, "username", None)
                attendee_address = (
                    "mailto:" + attendee_username if attendee_username and "@" in str(attendee_username) else None
                )
            attendees = [attendee_address] if attendee_address else [attendee_principal]
        else:
            ## No second principal — cannot perform a meaningful cross-user probe.
            ## RFC6638 is a multi-user protocol; mark as unknown rather than
            ## testing against ourselves (self-invites may be skipped per RFC6638).
            ## (The early return above already handles the no-scheduling case.)
            behaviour = "not tested: only one user configured; server claims scheduling support"
            self.set_feature("scheduling.mailbox.inbox-delivery", {"support": "unknown", "behaviour": behaviour})
            self.set_feature("scheduling.auto-schedule", {"support": "unknown", "behaviour": behaviour})
            return

        ## Some servers (e.g. sabre/dav / Davis) require the attendee to have at
        ## least one calendar before they will deliver scheduling messages to the
        ## inbox.  Create a temporary calendar for the attendee if needed and
        ## remove it after the probe.
        attendee_temp_cal = None
        if extra_principals:
            try:
                if not attendee_principal.calendars():
                    attendee_temp_cal = attendee_principal.make_calendar(
                        cal_id="csc-inbox-probe-attendee-cal",
                        name="csc-inbox-probe-attendee-cal",
                    )
            except Exception:
                pass

        ## Snapshot attendee inbox before the probe
        try:
            inbox = attendee_principal.schedule_inbox()
            inbox_before = {item.url for item in inbox.get_items()}
        except Exception:
            if attendee_temp_cal:
                try:
                    attendee_temp_cal.delete()
                except Exception:
                    pass
            self.set_feature("scheduling.mailbox.inbox-delivery", {"support": "unknown"})
            self.set_feature("scheduling.auto-schedule", {"support": "unknown"})
            return

        ## Create the probe ical event
        from caldav.lib.vcal import create_ical

        probe_uid = f"csc_inbox_delivery_probe_{uuid.uuid4().hex}"
        ## Use a future date: some servers (e.g. Cyrus) skip iTIP delivery for past events.
        probe_ical = create_ical(
            objtype="VEVENT",
            uid=probe_uid,
            summary="caldav-server-tester inbox-delivery probe",
            dtstart=datetime(2099, 6, 15, 10, 0, 0, tzinfo=utc),
            dtend=datetime(2099, 6, 15, 11, 0, 0, tzinfo=utc),
        )

        ## Use a temporary calendar if possible, fall back to the shared checker calendar
        probe_cal_id = "csc-inbox-delivery-probe"
        use_temp_calendar = self.feature_checked("create-calendar") and self.feature_checked("delete-calendar")
        if use_temp_calendar:
            try:
                probe_calendar = principal.make_calendar(cal_id=probe_cal_id, name=probe_cal_id)
            except Exception:
                use_temp_calendar = False
                probe_calendar = self.checker.calendar
        else:
            probe_calendar = self.checker.calendar

        try:
            probe_calendar.save_with_invites(probe_ical, attendees)
        except Exception as e:
            self.set_feature("scheduling.mailbox.inbox-delivery", {"support": "unknown", "behaviour": str(e)})
            self.set_feature("scheduling.auto-schedule", {"support": "unknown"})
            return

        ## Check if anything new arrived in the attendee inbox.
        ## Poll for up to 30 seconds before concluding that inbox delivery is unsupported:
        ## some servers (e.g. Davis, DAViCal) deliver scheduling messages asynchronously,
        ## and deleting the probe event before polling would race with that delivery.
        new_items: set = set()
        try:
            for _ in range(30):
                inbox_after = {item.url for item in inbox.get_items()}
                new_items = inbox_after - inbox_before
                if new_items:
                    break
                time.sleep(1)
            if new_items:
                ## Clean up the inbox item(s) using the attendee's client
                attendee_client = attendee_principal.client
                for url in new_items:
                    try:
                        DAVObject(client=attendee_client, url=url).delete()
                    except Exception:
                        pass
                self.set_feature("scheduling.mailbox.inbox-delivery", True)
            else:
                self.set_feature("scheduling.mailbox.inbox-delivery", False)

            ## Check whether the event was auto-scheduled into the attendee's calendar.
            ## Only detectable with a cross-user probe; report unknown in self-invite mode.
            if extra_principals:
                auto_scheduled = False
                try:
                    auto_scheduled = any(
                        event.icalendar_component.get("UID") == probe_uid
                        for cal in attendee_principal.calendars()
                        for event in cal.get_events()
                    )
                except Exception:
                    pass
                if auto_scheduled:
                    try:
                        for cal in attendee_principal.calendars():
                            for event in cal.get_events():
                                if event.icalendar_component.get("UID") == probe_uid:
                                    event.delete()
                    except Exception:
                        pass
                self.set_feature("scheduling.auto-schedule", auto_scheduled)
            else:
                self.set_feature("scheduling.auto-schedule", {"support": "unknown"})
        except Exception as e:
            self.set_feature("scheduling.mailbox.inbox-delivery", {"support": "unknown", "behaviour": str(e)})
            self.set_feature("scheduling.auto-schedule", {"support": "unknown"})
        finally:
            ## Clean up the probe event/calendar now that polling is done.
            ## This is intentionally done AFTER polling so that async delivery
            ## is not cancelled by deleting the originating event too early.
            if use_temp_calendar:
                try:
                    probe_calendar.delete()
                except Exception:
                    pass
            else:
                try:
                    probe_calendar.object_by_uid(probe_uid).delete()
                except Exception:
                    pass
            ## Clean up the temporary attendee calendar if we created one.
            if attendee_temp_cal:
                try:
                    attendee_temp_cal.delete()
                except Exception:
                    pass


class CheckScheduleTagStablePartstat(Check):
    """
    Verifies that a PARTSTAT-only attendee update does not change the Schedule-Tag
    (RFC6638 section 3.2 requirement).

    Requires a cross-user setup (extra_principals) and auto-schedule behaviour so
    that the probe event lands in the attendee's calendar automatically.  The check
    is skipped (reported as unknown) when either prerequisite is absent.
    """

    depends_on = {CheckScheduleTag, CheckSchedulingInboxDelivery, PrepareCalendar}
    features_to_be_checked = {"scheduling.schedule-tag.stable-partstat"}

    def _run_check(self) -> None:
        if not self.feature_checked("scheduling.schedule-tag"):
            self.set_feature("scheduling.schedule-tag.stable-partstat", False)
            return

        extra_principals = self.checker.extra_principals
        if not extra_principals:
            self.set_feature("scheduling.schedule-tag.stable-partstat", {"support": "unknown"})
            return

        if not self.feature_checked("scheduling.auto-schedule"):
            ## Without auto-schedule the event won't appear in the attendee's
            ## calendar automatically; skip rather than implement full inbox-accept.
            self.set_feature("scheduling.schedule-tag.stable-partstat", {"support": "unknown"})
            return

        principal = self.checker.principal
        attendee_principal = extra_principals[0]

        try:
            own_address = str(principal.get_vcal_address())
        except Exception:
            self.set_feature("scheduling.schedule-tag.stable-partstat", {"support": "unknown"})
            return

        try:
            attendee_address = str(attendee_principal.get_vcal_address())
        except Exception:
            attendee_username = getattr(attendee_principal.client, "username", None)
            if not attendee_username or "@" not in str(attendee_username):
                self.set_feature("scheduling.schedule-tag.stable-partstat", {"support": "unknown"})
                return
            attendee_address = "mailto:" + attendee_username

        probe_uid = f"csc_tag_partstat_probe_{uuid.uuid4().hex}"
        probe_ical = (
            "BEGIN:VCALENDAR\r\n"
            "VERSION:2.0\r\n"
            "PRODID:-//caldav-server-tester//test//EN\r\n"
            "BEGIN:VEVENT\r\n"
            f"UID:{probe_uid}\r\n"
            "DTSTART:20300601T100000Z\r\n"
            "DTEND:20300601T110000Z\r\n"
            "SUMMARY:caldav-server-tester schedule-tag partstat-stability probe\r\n"
            f"ORGANIZER:{own_address}\r\n"
            f"ATTENDEE;RSVP=TRUE;PARTSTAT=NEEDS-ACTION:{own_address}\r\n"
            f"ATTENDEE;RSVP=TRUE;PARTSTAT=NEEDS-ACTION:{attendee_address}\r\n"
            "END:VEVENT\r\n"
            "END:VCALENDAR\r\n"
        )

        probe_cal = self.checker.calendar
        try:
            probe_cal.save_with_invites(probe_ical, [principal, attendee_address])
        except Exception as e:
            self.set_feature("scheduling.schedule-tag.stable-partstat", {"support": "unknown", "behaviour": str(e)})
            return

        ## Wait for the event to appear in the attendee's calendar (auto-schedule)
        attendee_event = None
        for _ in range(15):
            try:
                for cal in attendee_principal.calendars():
                    try:
                        attendee_event = cal.event_by_uid(probe_uid)
                        break
                    except Exception:
                        pass
            except Exception:
                pass
            if attendee_event:
                break
            time.sleep(1)

        if attendee_event is None:
            self.set_feature("scheduling.schedule-tag.stable-partstat", {"support": "unknown"})
            self._cleanup(probe_cal, probe_uid, attendee_principal)
            return

        try:
            attendee_event.load()
            tag_before = attendee_event.schedule_tag
            if tag_before is None:
                ## Server doesn't return Schedule-Tag for the attendee copy; can't test stability
                self.set_feature("scheduling.schedule-tag.stable-partstat", {"support": "unknown"})
                return

            attendee_event.change_attendee_status(partstat="ACCEPTED")
            attendee_event.save()
            attendee_event.load()
            tag_after = attendee_event.schedule_tag

            if tag_after is None:
                self.set_feature("scheduling.schedule-tag.stable-partstat", {"support": "unknown"})
            elif tag_before == tag_after:
                self.set_feature("scheduling.schedule-tag.stable-partstat")
            else:
                self.set_feature(
                    "scheduling.schedule-tag.stable-partstat",
                    {
                        "support": "unsupported",
                        "behaviour": f"tag changed after PARTSTAT-only update: {tag_before!r} → {tag_after!r}",
                    },
                )
        except Exception as e:
            self.set_feature("scheduling.schedule-tag.stable-partstat", {"support": "unknown", "behaviour": str(e)})
        finally:
            self._cleanup(probe_cal, probe_uid, attendee_principal)

    def _cleanup(self, probe_cal, probe_uid: str, attendee_principal) -> None:
        try:
            probe_cal.object_by_uid(probe_uid).delete()
        except Exception:
            pass
        try:
            for cal in attendee_principal.calendars():
                try:
                    cal.event_by_uid(probe_uid).delete()
                    break
                except Exception:
                    pass
        except Exception:
            pass


class CheckTimezone(Check):
    """
    Checks support for non-UTC timezone information in events.

    Tests if the server accepts events with timezone information using zoneinfo.
    Some servers reject events with timezone data (returning 403 Forbidden).
    Related to GitHub issue https://github.com/python-caldav/caldav/issues/372
    """

    depends_on = {PrepareCalendar}
    features_to_be_checked = {
        "save-load.event.timezone",
    }

    def _run_check(self) -> None:
        cal = self.checker.calendar
        base = self.checker.fixture_base_year

        try:
            ## Create an event with a non-UTC timezone (America/Los_Angeles)
            ## Use unique UID since this test deletes the event
            ## (Nextcloud trashbin bug - see https://github.com/nextcloud/server/issues/30096)
            tz = ZoneInfo("America/Los_Angeles")
            event = cal.save_event(
                summary="Timezone test event",
                dtstart=datetime(base, 6, 15, 14, 0, 0, tzinfo=tz),
                dtend=datetime(base, 6, 15, 15, 0, 0, tzinfo=tz),
                uid=f"csc_timezone_test_event_{int(time.time() * 1000)}",
            )

            ## Try to load the event back
            event.load()

            ## Verify the event was saved correctly
            if event.vobject_instance:
                self.set_feature("save-load.event.timezone")
                ## Clean up
                try:
                    event.delete()
                except Exception:
                    pass
            else:
                self.set_feature(
                    "save-load.event.timezone",
                    {"support": "broken", "behaviour": "Event with timezone was saved but could not be loaded"},
                )
        except AuthorizationError as e:
            ## Server rejected the event with a 403 Forbidden
            ## This is the specific issue reported in GitHub #372
            self.set_feature(
                "save-load.event.timezone",
                {"support": "unsupported", "behaviour": f"Server rejected event with timezone (403 Forbidden): {e}"},
            )
        except DAVError as e:
            ## Other DAV error (e.g., 400 Bad Request, 500 Internal Server Error)
            self.set_feature(
                "save-load.event.timezone",
                {"support": "ungraceful", "behaviour": f"Server error when saving event with timezone: {e}"},
            )
        except Exception as e:
            ## Unexpected error
            self.set_feature(
                "save-load.event.timezone",
                {"support": "broken", "behaviour": f"Unexpected error during timezone test: {e}"},
            )


class CheckRelatedTo(Check):
    """Check if RELATED-TO properties (RFC5545 section 3.8.4.5) are preserved.

    Saves an event with two RELATED-TO lines and loads it back.
    - full: both RELATED-TO lines preserved
    - broken: only the first RELATED-TO line preserved (subsequent ones stripped)
    - unsupported: all RELATED-TO lines stripped
    """

    depends_on = {PrepareCalendar}
    features_to_be_checked = {"save-load.icalendar.related-to"}

    def _run_check(self):
        cal = self.checker.calendar
        base = self.checker.fixture_base_year
        uid = f"csc_related_to_{uuid.uuid4().hex[:8]}"
        test_obj = None
        try:
            test_obj = cal.save_object(
                Event,
                f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//tobixen//Caldav-Server-Tester//en_DK
BEGIN:VEVENT
UID:{uid}
DTSTART:{base}0601T120000Z
DTEND:{base}0601T130000Z
DTSTAMP:{base}0101T000000Z
SUMMARY:event with related-to for feature detection
RELATED-TO;RELTYPE=PARENT:some-parent-uid
RELATED-TO;RELTYPE=SIBLING:some-sibling-uid
END:VEVENT
END:VCALENDAR""",
            )
            test_obj.load()
            count = test_obj.data.count("RELATED-TO")
            if count == 0:
                self.set_feature("save-load.icalendar.related-to", False)
            elif count == 1:
                self.set_feature(
                    "save-load.icalendar.related-to",
                    {
                        "support": "broken",
                        "behaviour": "first RELATED-TO line preserved but subsequent RELATED-TO lines are stripped",
                    },
                )
            else:
                assert count == 2
                self.set_feature("save-load.icalendar.related-to")
        except (AuthorizationError, DAVError) as e:
            self.set_feature("save-load.icalendar.related-to", {"support": "ungraceful", "behaviour": str(e)})
        finally:
            if test_obj:
                try:
                    test_obj.delete()
                except Exception:
                    pass


class CheckOpenTimeRangeSearch(Check):
    """Check open-ended time-range search behaviour for VTODOs and VEVENTs.

    RFC4791 section 9.9: the CALDAV:time-range element's "start" and "end"
    attributes are both optional; if absent, assume -infinity and +infinity
    respectively.  At least one must be present.

    - search.time-range.open.start.duration: components with DTSTART+DURATION (no
      DTEND/DUE) must be found by time-range searches overlapping their computed
      interval.  Tested for both VTODO (csc_task_with_duration) and VEVENT
      (csc_event_with_duration).  If support is asymmetric across component types
      the feature is marked "broken" with a behaviour note; separate per-component
      sub-features may be introduced in a future iteration.

    - search.time-range.open.start: when searching with only an end bound (open
      start, start assumed -infinity), tasks whose DTSTART is after the end bound
      must NOT be returned.  Uses end=<base>-01-01 (all fixture tasks start Jan 8+).

    - search.time-range.open.end: when searching with only a start bound (open end,
      end assumed +infinity), tasks that overlap the start bound must be returned.
      Uses start=<base>-01-09 to find csc_simple_task3 (DTSTART=<base>-01-09T12:00Z,
      DUE=<base>-01-09T13:00Z); per RFC4791 sec 9.9 condition for VTODO with DTSTART+DUE
      and absent end: (start < DUE) OR (start <= DTSTART) → TRUE.
    """

    depends_on = {CheckSearch}
    features_to_be_checked = {
        "search.time-range.open.start.duration",
        "search.time-range.open.start",
        "search.time-range.open.end",
    }

    def _run_check(self):
        if not self.feature_checked("search.time-range.todo"):
            for feat in self.features_to_be_checked:
                self.set_feature(feat, None)
            return

        tasklist = self.checker.tasklist
        cal = self.checker.calendar
        base = self.checker.fixture_base_year

        ## Test 1: Components with DTSTART+DURATION must be found by time-range
        ## searches that overlap their computed interval.
        ## RFC4791 section 9.9:
        ##   VEVENT (duration > 0s): (start < DTSTART+DURATION) AND (end > DTSTART)
        ##   VTODO:  (start <= DTSTART+DURATION) AND ((end > DTSTART) OR (end >= DTSTART+DURATION))

        ## Test 1a: VTODO — csc_task_with_duration (DTSTART=<base>-01-18T12:00Z, DURATION=PT2H → ends 14:00Z).
        ## Two overlap scenarios:
        ##   a) Search [13:00, 15:00]: start inside duration, end after → must match.
        ##   b) Search [11:00, 13:00]: start before DTSTART, end inside duration → must match.
        ## Sanity: csc_simple_task3 (DTSTART=Jan 9) must NOT appear in a Jan-18 search.
        todo_dur_ok = None
        dur_err = None
        try:
            results_a = tasklist.search(
                start=datetime(base, 1, 18, 13, 0, 0, tzinfo=utc),
                end=datetime(base, 1, 18, 15, 0, 0, tzinfo=utc),
                todo=True,
                include_completed=True,
                post_filter=False,
            )
            results_b = tasklist.search(
                start=datetime(base, 1, 18, 11, 0, 0, tzinfo=utc),
                end=datetime(base, 1, 18, 13, 0, 0, tzinfo=utc),
                todo=True,
                include_completed=True,
                post_filter=False,
            )
            found_a = any(r.component.get("UID") == "csc_task_with_duration" for r in results_a)
            found_b = any(r.component.get("UID") == "csc_task_with_duration" for r in results_b)
            not_spurious = not any(r.component.get("UID") == "csc_simple_task3" for r in results_a)
            todo_dur_ok = found_a and found_b and not_spurious
        except (AuthorizationError, DAVError) as e:
            todo_dur_ok = False
            dur_err = str(e)

        ## Test 1b: VEVENT — csc_event_with_duration (DTSTART=<base>-01-17T12:00Z, DURATION=PT1H → ends 13:00Z).
        ## Only run if event time-range search is known to work.
        ## Two overlap scenarios:
        ##   a) Search [12:30, 14:00]: start inside duration, end after → must match.
        ##   b) Search [11:00, 12:30]: start before DTSTART, end inside duration → must match.
        ## Sanity: csc_simple_event1 (Jan 1) must NOT appear in a Jan-17 search.
        event_dur_ok = None
        if self.feature_checked("search.time-range.event"):
            try:
                ev_a = cal.search(
                    start=datetime(base, 1, 17, 12, 30, 0, tzinfo=utc),
                    end=datetime(base, 1, 17, 14, 0, 0, tzinfo=utc),
                    event=True,
                    post_filter=False,
                )
                ev_b = cal.search(
                    start=datetime(base, 1, 17, 11, 0, 0, tzinfo=utc),
                    end=datetime(base, 1, 17, 12, 30, 0, tzinfo=utc),
                    event=True,
                    post_filter=False,
                )
                ev_found_a = any(r.component.get("UID") == "csc_event_with_duration" for r in ev_a)
                ev_found_b = any(r.component.get("UID") == "csc_event_with_duration" for r in ev_b)
                ev_not_spurious = not any(r.component.get("UID") == "csc_simple_event1" for r in ev_a)
                event_dur_ok = ev_found_a and ev_found_b and ev_not_spurious
            except (AuthorizationError, DAVError) as e:
                event_dur_ok = False
                if not dur_err:
                    dur_err = str(e)

        ## Combine VTODO and VEVENT duration results.
        if event_dur_ok is None:
            ## Event search not tested; report based on VTODO result only.
            if todo_dur_ok:
                self.set_feature("search.time-range.open.start.duration")
            elif dur_err:
                self.set_feature(
                    "search.time-range.open.start.duration", {"support": "ungraceful", "behaviour": dur_err}
                )
            else:
                self.set_feature("search.time-range.open.start.duration", False)
        elif todo_dur_ok == event_dur_ok:
            ## Both results agree.
            if todo_dur_ok:
                self.set_feature("search.time-range.open.start.duration")
            elif dur_err:
                self.set_feature(
                    "search.time-range.open.start.duration", {"support": "ungraceful", "behaviour": dur_err}
                )
            else:
                self.set_feature("search.time-range.open.start.duration", False)
        else:
            ## Asymmetric: one component type works, the other does not.
            todo_status = "supported" if todo_dur_ok else "unsupported"
            event_status = "supported" if event_dur_ok else "unsupported"
            self.set_feature(
                "search.time-range.open.start.duration",
                {
                    "support": "broken",
                    "behaviour": f"asymmetric DURATION support: VTODO {todo_status}, VEVENT {event_status}",
                },
            )

        ## Test 2: open-start search (only end bound, start assumed -infinity).
        ## Must not return tasks whose DTSTART is after the end bound.
        ## All fixture tasks have DTSTART on Jan 8 or later, so a search ending
        ## at <base>-01-01 should return none of them.
        ## Uses csc_simple_task3 (DTSTART+DUE) and csc_task_with_duration (DTSTART+DURATION).
        known_future_uids = {"csc_simple_task3", "csc_task_with_duration"}
        try:
            results = tasklist.search(
                end=datetime(base, 1, 1, tzinfo=utc),
                todo=True,
                include_completed=True,
                post_filter=False,
            )
            returned_uids = {r.component.get("UID") for r in results}
            found_future = bool(returned_uids & known_future_uids)
            if found_future:
                self.set_feature(
                    "search.time-range.open.start",
                    {
                        "support": "broken",
                        "behaviour": "tasks with DTSTART after end bound returned when only an end bound is given",
                    },
                )
            else:
                self.set_feature("search.time-range.open.start")
        except (AuthorizationError, DAVError) as e:
            self.set_feature("search.time-range.open.start", {"support": "ungraceful", "behaviour": str(e)})

        ## Test 3: open-end search (only start bound, end assumed +infinity).
        ## RFC4791 sec 9.9: for VTODO with DTSTART+DUE and absent end (+infinity),
        ## condition is (start < DUE) OR (start <= DTSTART).
        ## csc_simple_task3 (DTSTART=<base>-01-09T12:00Z, DUE=<base>-01-09T13:00Z) with
        ## start=<base>-01-09T00:00Z: (Jan 9 < Jan 9T13:00) = TRUE → must be returned.
        try:
            results = tasklist.search(
                start=datetime(base, 1, 9, tzinfo=utc),
                todo=True,
                include_completed=True,
                post_filter=False,
            )
            found = any(r.component.get("UID") == "csc_simple_task3" for r in results)
            self.set_feature("search.time-range.open.end", found)
        except (AuthorizationError, DAVError) as e:
            self.set_feature("search.time-range.open.end", {"support": "ungraceful", "behaviour": str(e)})


class CheckTodoTimeRangeStrict(Check):
    """Check that VTODO time-range searches do not return tasks outside the searched range.

    Motivated by a xandikos bug where having a VTODO with DTSTART+DURATION (no DUE) on
    the calendar caused xandikos's index-fallback logic to also return other tasks whose
    DTSTART+DUE were entirely outside the searched range.

    Two probes:
    1. A freshly created VTODO with DTSTART+DUE in March (next year) must NOT appear in a
       [Jan 9, Jan 10] search.  This exercises the same code path as the xandikos bug
       (index-based filtering of a task with both DTSTART and DUE in the index).
    2. csc_task_with_duration (DTSTART=<base>-01-18, DURATION=PT2H) must NOT appear in the
       same search.  This exercises the DURATION fallback path (no DUE means the server
       must fall back from index-based filtering to a full file check).
    """

    depends_on = {CheckSearch}
    features_to_be_checked = {"search.time-range.todo.strict"}

    def _run_check(self) -> None:
        if not self.feature_checked("search.time-range.todo"):
            self.set_feature("search.time-range.todo.strict", None)
            return

        tasklist = self.checker.tasklist
        base = self.checker.fixture_base_year
        temp_todo = None
        try:
            ## Create a task clearly outside the Jan 9-10 search window.
            ## DTSTART=<base>-03-15 is two months after the range end (Jan 10), so a
            ## correct server must not return it.  Unlike csc_task_future (which was
            ## removed because xandikos returned it erroneously), this task is created
            ## fresh so it is present exactly during this check and then cleaned up.
            temp_todo = tasklist.save_object(
                Todo,
                uid="csc_temp_outside_range",
                summary="temporary task outside search range (should not appear in Jan search)",
                dtstart=datetime(base, 3, 15, 12, 0, 0, tzinfo=utc),
                due=datetime(base, 3, 15, 13, 0, 0, tzinfo=utc),
            )

            results = tasklist.search(
                start=datetime(base, 1, 9, tzinfo=utc),
                end=datetime(base, 1, 10, tzinfo=utc),
                todo=True,
                include_completed=True,
                post_filter=False,
            )
            returned_uids = {r.component.get("UID") for r in results}

            ## Neither the fresh March task nor the pre-existing Jan-18 DURATION task
            ## should appear in a Jan 9-10 search.
            false_positive_uids = returned_uids & {"csc_temp_outside_range", "csc_task_with_duration"}
            if false_positive_uids:
                self.set_feature(
                    "search.time-range.todo.strict",
                    {
                        "support": "broken",
                        "behaviour": f"tasks outside range returned: {sorted(false_positive_uids)}",
                    },
                )
            else:
                self.set_feature("search.time-range.todo.strict")
        except (AuthorizationError, DAVError) as e:
            self.set_feature("search.time-range.todo.strict", {"support": "ungraceful", "behaviour": str(e)})
        finally:
            if temp_todo:
                try:
                    temp_todo.delete()
                except Exception:
                    pass
