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


def _filter_2000(objects):
    """Sometimes the only chance we have to run checks towards some cloud
    service is to run the checks towards some existing important
    calendar.  To reduce the probability of clashes with real calendar
    content we let (almost) all test objects be in year 2000.  The
    work on the checker was initiated in 2025.  It's pretty rare that
    people have calendars with 25 years old data in it, but it could
    happen.  TODO: perhaps we rather should filter by the uid?  TODO:
    RFC2445 is from 1998, we would be even safer if using 1997 rather
    than 2000?
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

    return (x for x in objects if date(2000, 1, 1) <= d(x) <= date(2001, 1, 1))


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
        "delete-calendar",
        "delete-calendar.free-namespace",
    }
    depends_on = {CheckGetCurrentUserPrincipal}

    def _try_make_calendar(self, cal_id, **kwargs):
        """
        Does some attempts on creating and deleting calendars, and sets some
        flags - while others should be set by the caller.
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
            if kwargs.get("name"):
                try:
                    name = "A calendar with this name should not exist"
                    self.checker.principal.calendar(name=name).events()
                    ## Server returned a calendar for a name that cannot exist;
                    ## display-name lookup is unreliable on this server.
                    logging.warning(
                        "Server returned a calendar for a display name that should not exist; "
                        "cannot verify create-calendar.set-displayname"
                    )
                    self.set_feature("create-calendar.set-displayname", False)
                except Exception:
                    ## This is not the exception, this is the normal
                    try:
                        cal2 = self.checker.principal.calendar(name=kwargs["name"])
                        cal2.events()
                        assert cal2.id == cal.id
                        self.set_feature("create-calendar.set-displayname")
                    except Exception:
                        self.set_feature("create-calendar.set-displayname", False)

        except DAVError as e:
            ## calendar creation created an exception.  Maybe the calendar exists?
            ## in any case, return exception
            cal = self.checker.principal.calendar(cal_id=cal_id)
            try:
                cal.events()
            except Exception:
                cal = None
            if not cal:
                ## cal not made and does not exist, exception thrown.
                ## Caller to decide why the calendar was not made
                return (False, e)

        assert cal

        try:
            ## Use DAVObject.delete directly to bypass Calendar.delete()
            ## workarounds - we want to test the server's raw DELETE behavior
            DAVObject.delete(cal)
            try:
                cal = self.checker.principal.calendar(cal_id=cal_id)
                events = cal.events()
            except NotFoundError:
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
                try:
                    cal = self.checker.principal.calendar(cal_id=cal_id)
                    assert cal
                    cal.events()
                    ## Calendar not deleted, but no exception thrown.
                    ## Perhaps it's a "move to thrashbin"-regime on the server
                    self.set_feature(
                        "delete-calendar",
                        {"support": "unknown", "behaviour": "move to trashbin?"},
                    )
                except NotFoundError as e:
                    ## Calendar was deleted, it just took some time.
                    self.set_feature(
                        "delete-calendar",
                        {"support": "fragile", "behaviour": "delayed deletion"},
                    )
                    return (calmade, e)
            return (calmade, None)
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
            return (calmade, None)

    def _run_check(self):
        try:
            cal = self.checker.principal.calendar(cal_id="this_should_not_exist")
            cal.events()
            self.set_feature("create-calendar.auto")
        except (
            NotFoundError,
            AuthorizationError,
        ):  ## robur throws a 403 .. and that's ok
            self.set_feature("create-calendar.auto", False)

        ## Check on "no_default_calendar" flag
        try:
            cals = self.checker.principal.calendars()
            calname = cals[0].get_display_name()
            self.set_feature("get-current-user-principal.has-calendar", True)
        except Exception:
            self.set_feature("get-current-user-principal.has-calendar", False)

        makeret = self._try_make_calendar(name="Yep", cal_id="caldav-server-checker-mkdel-test")
        if makeret[0]:
            ## calendar created
            ## TODO: this is a lie - we haven't really verified this, only on second script run we will be sure
            if self.checker.features_checked.is_supported("delete-calendar"):
                self.set_feature("delete-calendar.free-namespace", True)
            return
        makeret = self._try_make_calendar(name=str(uuid.uuid4()), cal_id="pythoncaldav-test")
        if makeret[0]:
            self.set_feature("create-calendar.set-displayname", True)
            self.set_feature("delete-calendar.free-namespace", False)
            return
        makeret = self._try_make_calendar(cal_id="pythoncaldav-test")
        if makeret[0]:
            self.set_feature("create-calendar.set-displayname", False)
            if self.checker.features_checked.is_supported("delete-calendar"):
                self.set_feature("delete-calendar.free-namespace", True)
            return
        unique_id1 = "testcalendar-" + str(uuid.uuid4())
        makeret = self._try_make_calendar(cal_id=unique_id1, name=str(uuid.uuid4()))
        if makeret[0]:
            self.set_feature("delete-calendar.free-namespace", False)
            self.set_feature("create-calendar.set-displayname", True)
            return
        unique_id = "testcalendar-" + str(uuid.uuid4())
        makeret = self._try_make_calendar(cal_id=unique_id)
        if makeret[0]:
            self.set_feature("create-calendar.set-displayname", False)
            self.set_feature("delete-calendar.free-namespace", False)
            return
        makeret = self._try_make_calendar(cal_id=unique_id, method="mkcol")
        if makeret[0]:
            self.set_feature("create-calendar", {"support": "quirk", "behaviour": "mkcol-required"})
        else:
            self.set_feature("create-calendar", False)


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
        try:
            task_with_dtstart = add_if_not_existing(
                Todo,
                summary="task with a dtstart",
                uid="csc_simple_task1",
                dtstart=date(2000, 1, 7),
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
                    dtstart=date(2000, 1, 7),
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
        try:
            simple_journal = add_if_not_existing(
                Journal,
                summary="simple journal entry",
                uid="csc_simple_journal1",
                dtstart=date(2000, 1, 11),
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
                        dtstart=date(2000, 1, 11),
                    )
                    simple_journal.load()
                    self.set_feature("save-load.journal")
                    self.set_feature("save-load.journal.mixed-calendar", False)
                except Exception:
                    self.set_feature("save-load.journal", "ungraceful")
                    self.checker.cnt -= 1

    def _create_test_events(self, calendar, cal_id, name, add_if_not_existing):
        """Create all the test event/task/journal objects in the calendar."""
        todo_ok = self._prepare_task_calendar(cal_id, name, add_if_not_existing)
        if not todo_ok:
            return False

        self._prepare_journal_calendar(cal_id, name, add_if_not_existing)

        simple_event = add_if_not_existing(
            Event,
            summary="simple event with a start time and an end time",
            uid="csc_simple_event1",
            dtstart=datetime(2000, 1, 1, 12, 0, 0, tzinfo=utc),
            dtend=datetime(2000, 1, 1, 13, 0, 0, tzinfo=utc),
        )
        simple_event.load()
        self.set_feature("save-load.event")

        non_duration_event = add_if_not_existing(
            Event,
            summary="event with a start time but no end time",
            uid="csc_simple_event2",
            dtstart=datetime(2000, 1, 2, 12, 0, 0, tzinfo=utc),
        )

        one_day_event = add_if_not_existing(
            Event,
            summary="event with a start date but no end date",
            uid="csc_simple_event3",
            dtstart=date(2000, 1, 3),
        )

        two_days_event = add_if_not_existing(
            Event,
            summary="event with a start date and end date",
            uid="csc_simple_event4",
            dtstart=date(2000, 1, 4),
            dtend=date(2000, 1, 6),
        )

        event_with_categories = add_if_not_existing(
            Event,
            summary="event with categories",
            uid="csc_event_with_categories",
            categories="hands,feet,head",
            dtstart=datetime(2000, 1, 7, 12, 0, 0),
            dtend=datetime(2000, 1, 7, 13, 0, 0),
        )

        event_with_class = add_if_not_existing(
            Event,
            summary="event with confidential class",
            uid="csc_event_with_class",
            dtstart=datetime(2000, 1, 16, 12, 0, 0, tzinfo=utc),
            dtend=datetime(2000, 1, 16, 13, 0, 0, tzinfo=utc),
            class_="CONFIDENTIAL",
        )

        event_with_duration = add_if_not_existing(
            Event,
            summary="event with duration instead of dtend",
            uid="csc_event_with_duration",
            dtstart=datetime(2000, 1, 17, 12, 0, 0, tzinfo=utc),
            duration=timedelta(hours=1),
        )

        task_with_due = add_if_not_existing(
            Todo,
            summary="task with a due date",
            uid="csc_simple_task2",
            due=date(2000, 1, 8),
        )

        task_with_dtstart_and_due = add_if_not_existing(
            Todo,
            summary="task with a dtstart time and due time",
            uid="csc_simple_task3",
            dtstart=datetime(2000, 1, 9, 12, 0, 0, tzinfo=utc),
            due=datetime(2000, 1, 9, 13, 0, 0, tzinfo=utc),
        )

        ## TODO: there are more variants to be tested - dtstart date and due date,
        ## dtstart and duration, only duration, no time spec at all, ...

        try:
            event_with_alarm = add_if_not_existing(
                Event,
                summary="event with alarm",
                uid="csc_event_with_alarm",
                dtstart=datetime(2000, 1, 10, 8, 0, 0, tzinfo=utc),
                dtend=datetime(2000, 1, 10, 9, 0, 0, tzinfo=utc),
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
            dtstart=datetime(2000, 1, 12, 12, 0, 0, tzinfo=utc),
            dtend=datetime(2000, 1, 12, 13, 0, 0, tzinfo=utc),
        )
        recurring_event.load()
        self.set_feature("save-load.event.recurrences")

        ## All-day (VALUE=DATE) yearly recurring event, used to test
        ## whether the server handles implicit recurrence for all-day events.
        ## DTSTART is 2000-02-01; the second occurrence is 2001-02-01.
        add_if_not_existing(
            Event,
            summary="yearly recurring all-day event",
            uid="csc_yearly_recurring_allday_event",
            rrule={"FREQ": "YEARLY"},
            dtstart=date(2000, 2, 1),
        )

        event_with_rrule_and_count = add_if_not_existing(
            Event,
            """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Example Corp.//CalDAV Client//EN
BEGIN:VEVENT
UID:weeklymeeting
DTSTAMP:20001013T151313Z
DTSTART:20001018T140000Z
DTEND:20001018T150000Z
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
                dtstart=datetime(2000, 1, 12, 12, 0, 0, tzinfo=utc),
                due=datetime(2000, 1, 12, 13, 0, 0, tzinfo=utc),
            )
            recurring_task.load()
            self.set_feature("save-load.todo.recurrences")
        except DAVError:
            self.set_feature("save-load.todo.recurrences", "ungraceful")

        try:
            task_with_rrule_and_count = add_if_not_existing(
                Todo,
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Example Corp.//CalDAV Client//EN
BEGIN:VTODO
UID:csc_recurring_count_task
DTSTAMP:20001013T151313Z
DTSTART:20001016T065500Z
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
                """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//tobixen//Caldav-Server-Tester//en_DK
BEGIN:VEVENT
UID:csc_monthly_recurring_with_exception
DTSTART:20000113T120000Z
DTEND:20000113T130000Z
DTSTAMP:20240429T181103Z
RRULE:FREQ=MONTHLY
SUMMARY:Monthly recurring with exception
END:VEVENT
BEGIN:VEVENT
UID:csc_monthly_recurring_with_exception
RECURRENCE-ID:20000213T120000Z
DTSTART:20000213T120000Z
DTEND:20000213T130000Z
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

        return True

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

    def _run_check(self):
        ## NOTE: Any objects created here with a UID starting with "csc_" will be
        ## cleaned up by the csc_* fallback in checker.cleanup(). For servers that
        ## support calendar deletion, the whole calendar is deleted instead.
        ## If you add new objects here, make sure their UIDs start with "csc_".

        cal_id = "caldav-server-checker-calendar"
        test_cal_info = self.checker.expected_features.is_supported(
            "test-calendar.compatibility-tests", return_type=dict
        )
        name = test_cal_info.get("name", "Calendar for checking server feature support")

        self._find_or_create_calendar(cal_id, name, test_cal_info)
        calendar = self.checker.calendar

        ## TODO: replace this with one search if possible(?)
        ## Some servers (e.g. CCS) reject time-range queries for old dates
        ## (min-date-time restriction), so fall back to empty lists.
        try:
            events_from_2000 = calendar.search(event=True, start=datetime(2000, 1, 1), end=datetime(2001, 1, 1))
        except (AuthorizationError, DAVError):
            events_from_2000 = []
        try:
            tasks_from_2000 = calendar.search(todo=True, start=datetime(2000, 1, 1), end=datetime(2001, 1, 1))
        except (AuthorizationError, DAVError):
            tasks_from_2000 = []
        ## Some servers (e.g. OX) silently return empty for old-date time-range
        ## queries.  Fall back to listing all objects and filtering by date so
        ## existing year-2000 test objects are detected and not re-PUT.
        if not events_from_2000 and not tasks_from_2000:
            try:
                events_from_2000 = calendar.events()
            except (AuthorizationError, DAVError):
                pass
            try:
                tasks_from_2000 = self.checker.tasklist.todos()
            except (AuthorizationError, DAVError):
                pass
        try:
            journals_from_2000 = calendar.journals()
        except (AuthorizationError, DAVError):
            journals_from_2000 = []

        object_by_uid = {}
        self.checker.cnt = 0

        for obj in _filter_2000(events_from_2000 + tasks_from_2000):
            object_by_uid[obj.component["uid"]] = obj
        for obj in journals_from_2000:
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

        ## Delete any stale objects from year 2000 that aren't part of
        ## the current test set (e.g. leftovers from previous test runs)
        for uid, obj in object_by_uid.items():
            logging.warning("Deleting stale year-2000 object with UID %s", uid)
            obj.delete()
        if not self.checker.calendar.events():
            logging.error("Calendar appears empty after PrepareCalendar; subsequent checks may be unreliable")
        ## Not asserting on tasklist.todos() here - on servers with broken
        ## comp-type filtering (e.g. Bedework), todos() returns empty even
        ## though todos were saved successfully (verified via load() above).

        self._check_get_by_url(calendar)


class CheckSearch(Check):
    depends_on = {PrepareCalendar}
    features_to_be_checked = {
        "search.time-range.event",
        "search.time-range.event.old-dates",
        "search.text.category",
        "search.time-range.todo",
        "search.time-range.todo.old-dates",
        "search.comp-type.optional",
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

    def _run_check(self):
        cal = self.checker.calendar
        tasklist = self.checker.tasklist

        ## First, test time-range with recent dates (near-future).
        ## This determines the base search.time-range.event/todo support.
        self._check_time_range_with_recent_data(cal, tasklist)

        ## Then test with old dates (year 2000) for the .old-dates sub-feature.
        ## Some servers (e.g. CCS) enforce min-date-time and reject old dates.
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
        ## search.combined - uses year-2000 dates, so requires old-dates support
        if self.feature_checked("search.text.category") and self.feature_checked("search.time-range.event.old-dates"):
            try:
                events1 = cal.search(
                    category="hands",
                    event=True,
                    start=datetime(2000, 1, 1, 11, 0, 0),
                    end=datetime(2000, 1, 13, 14, 0, 0),
                    post_filter=False,
                )
                events2 = cal.search(
                    category="hands",
                    event=True,
                    start=datetime(2000, 1, 1, 9, 0, 0),
                    end=datetime(2000, 1, 6, 14, 0, 0),
                    post_filter=False,
                )
                self.set_feature("search.combined-is-logical-and", len(events1) == 1 and len(events2) == 0)
            except (AuthorizationError, DAVError):
                self.set_feature("search.combined-is-logical-and", "ungraceful")
        elif self.feature_checked("search.text.category"):
            ## Can't test combined search without old-dates support
            ## (test data is in year 2000)
            self.set_feature("search.combined-is-logical-and", None)

        try:
            if self.feature_checked("search.time-range.todo.old-dates"):
                objects = cal.search(
                    start=datetime(2000, 1, 1, tzinfo=utc),
                    end=datetime(2001, 1, 1, tzinfo=utc),
                    post_filter=False,
                )
            else:
                objects = _filter_2000(cal.search(post_filter=False))
            if len(objects) == 0:
                self.set_feature(
                    "search.comp-type.optional",
                    {
                        "support": "unsupported",
                        "description": "search that does not include comptype yields nothing",
                    },
                )
            elif cal == tasklist and not any(x for x in objects if isinstance(x, Todo)):
                self.set_feature(
                    "search.comp-type.optional",
                    {
                        "support": "fragile",
                        "description": "search that does not include comptype does not yield tasks",
                    },
                )
            elif (
                cal != tasklist
                and len(objects)
                + len(
                    tasklist.search(
                        start=datetime(2000, 1, 1, tzinfo=utc),
                        end=datetime(2001, 1, 1, tzinfo=utc),
                        post_filter=False,
                    )
                )
                == self.checker.cnt
            ):
                self.set_feature(
                    "search.comp-type.optional",
                    {
                        "support": "full",
                        "description": "comp-filter is redundant in search as a calendar can only hold one kind of components",
                    },
                )
            elif len(objects) == self.checker.cnt:
                self.set_feature("search.comp-type.optional")
            else:
                ## TODO ... we need to do more testing on search to conclude certainly on this one.  But at least we get something out.
                self.set_feature(
                    "search.comp-type.optional",
                    {
                        "support": "fragile",
                        "description": "unexpected results from date-search without comp-type",
                    },
                )
        except Exception:
            self.set_feature("search.comp-type.optional", {"support": "ungraceful"})

        ## search.unlimited-time-range: does a REPORT without a time range return all objects
        ## regardless of date?  Uses the year-2000 non-recurring event csc_simple_event1
        ## already placed by PrepareCalendar (so indexing delays don't affect the result)
        ## to detect sliding-window servers (e.g. OX) that hide old non-recurring events.
        ## Uses _request_report_build_resultlist directly to bypass the search.unlimited-time-range
        ## workaround in search.py, so the actual server behaviour is observed.
        try:
            searcher = CalDAVSearcher(comp_class=Event)
            xml, comp_class = searcher.build_search_xml_query()
            _, objects = cal._request_report_build_resultlist(xml, comp_class)
            found = any(o.id == "csc_simple_event1" for o in objects)
            if found:
                self.set_feature("search.unlimited-time-range")
            elif objects:
                ## Server returned some events but missed the old-date non-recurring event:
                ## it uses a sliding time window (broken, not unsupported)
                self.set_feature("search.unlimited-time-range", {"support": "broken"})
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

        ## Check that the alarm event was created successfully
        try:
            obj = cal.object_by_uid("csc_event_with_alarm")
        except Exception:
            self.set_feature("search.time-range.alarm", False)
            return

        ## The alarm event has dtstart 2000-01-10 08:00 and a -15min alarm,
        ## so the alarm triggers at 07:45.

        ## Search that SHOULD find the alarm (07:40-07:55 covers 07:45)
        try:
            events = cal.search(
                event=True,
                alarm_start=datetime(2000, 1, 10, 7, 40, tzinfo=utc),
                alarm_end=datetime(2000, 1, 10, 7, 55, tzinfo=utc),
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
                alarm_start=datetime(2000, 1, 10, 8, 0, tzinfo=utc),
                alarm_end=datetime(2000, 1, 10, 8, 15, tzinfo=utc),
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

        ## Precondition: basic event time-range search must return exactly the
        ## one recurring event in Jan 2000.  On servers with broken comp-type
        ## filtering (e.g. Bedework) this may return extra objects, making
        ## recurrence checks unreliable.  Some servers (e.g. CCS) reject
        ## old date ranges entirely.  Either way, mark all features unsupported.
        try:
            events = cal.search(
                start=datetime(2000, 1, 12, tzinfo=utc),
                end=datetime(2000, 1, 13, tzinfo=utc),
                event=True,
                post_filter=False,
            )
        except (AuthorizationError, DAVError):
            events = []
        if len(events) != 1:
            for feat in self.features_to_be_checked:
                self.set_feature(feat, False)
            return

        if self.checker.features_checked.is_supported("search.time-range.todo.old-dates"):
            todos = tl.search(
                start=datetime(2000, 1, 12, tzinfo=utc),
                end=datetime(2000, 1, 13, tzinfo=utc),
                todo=True,
                include_completed=True,
                post_filter=False,
            )
            if len(todos) != 1:
                logging.warning(
                    "Expected 1 recurring todo in Jan 2000, got %d; skipping recurrence todo checks", len(todos)
                )
                for feat in self.features_to_be_checked:
                    if not self.feature_checked(feat):
                        self.set_feature(feat, False)
                return
        events = cal.search(
            start=datetime(2000, 2, 12, tzinfo=utc),
            end=datetime(2000, 2, 13, tzinfo=utc),
            event=True,
            post_filter=False,
        )
        implicit_datetime = len(events) == 1
        ## Also check all-day (VALUE=DATE) recurring events: the yearly event
        ## (DTSTART;VALUE=DATE:2000-02-01) should be found in 2001-02-01 range.
        try:
            allday_events = cal.search(
                start=datetime(2001, 2, 1, tzinfo=utc),
                end=datetime(2001, 2, 2, tzinfo=utc),
                event=True,
                post_filter=False,
            )
            implicit_allday = len(allday_events) == 1
        except (AuthorizationError, DAVError):
            implicit_allday = implicit_datetime
        if implicit_datetime and not implicit_allday:
            ## Datetime recurring events work but all-day (VALUE=DATE) events do not
            self.set_feature(
                "search.recurrences.includes-implicit.event",
                {"support": "fragile", "behaviour": "broken for all-day (VALUE=DATE) events"},
            )
        else:
            self.set_feature("search.recurrences.includes-implicit.event", implicit_datetime)
        todos1 = tl.search(
            start=datetime(2000, 2, 12, tzinfo=utc),
            end=datetime(2000, 2, 13, tzinfo=utc),
            todo=True,
            include_completed=True,
            post_filter=False,
        )
        self.set_feature("search.recurrences.includes-implicit.todo", len(todos1) == 1)

        if todos1:
            todos2 = tl.search(
                start=datetime(2000, 2, 12, tzinfo=utc),
                end=datetime(2000, 2, 13, tzinfo=utc),
                todo=True,
                post_filter=False,
            )
            self.set_feature("search.recurrences.includes-implicit.todo.pending", len(todos2) == 1)

        exception = cal.search(
            start=datetime(2000, 2, 13, 11, tzinfo=utc),
            end=datetime(2000, 2, 13, 13, tzinfo=utc),
            event=True,
            post_filter=False,
        )
        if len(exception) != 1:
            ## Can't reliably check expansion/exception features
            for feat in self.features_to_be_checked:
                if not self.feature_checked(feat):
                    self.set_feature(feat, False)
            return
        far_future_recurrence = cal.search(
            start=datetime(2045, 3, 12, tzinfo=utc),
            end=datetime(2045, 3, 13, tzinfo=utc),
            event=True,
            post_filter=False,
        )
        self.set_feature("search.recurrences.includes-implicit.infinite-scope", len(far_future_recurrence) == 1)

        ## server-side expansion
        events = cal.search(
            start=datetime(2000, 2, 12, tzinfo=utc),
            end=datetime(2000, 2, 13, tzinfo=utc),
            event=True,
            server_expand=True,
            post_filter=False,
        )
        self.set_feature(
            "search.recurrences.expanded.event",
            len(events) == 1 and events[0].component["dtstart"] == datetime(2000, 2, 12, 12, 0, 0, tzinfo=utc),
        )
        todos = cal.search(
            start=datetime(2000, 2, 12, tzinfo=utc),
            end=datetime(2000, 2, 13, tzinfo=utc),
            todo=True,
            server_expand=True,
            post_filter=False,
        )
        self.set_feature(
            "search.recurrences.expanded.todo",
            len(todos) == 1 and todos[0].component["dtstart"] == datetime(2000, 2, 12, 12, 0, 0, tzinfo=utc),
        )
        exception = cal.search(
            start=datetime(2000, 2, 13, 11, tzinfo=utc),
            end=datetime(2000, 2, 13, 13, tzinfo=utc),
            event=True,
            server_expand=True,
            post_filter=False,
        )
        self.set_feature(
            "search.recurrences.expanded.exception",
            len(exception) == 1
            and exception[0].component["dtstart"] == datetime(2000, 2, 13, 12, 0, 0, tzinfo=utc)
            and exception[0].component["summary"] == "February recurrence with different summary"
            and getattr(exception[0].component.get("RECURRENCE_ID"), "dt", None)
            == datetime(2000, 2, 13, 12, tzinfo=utc),
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
            ## find old events (e.g. OX's sliding window hides year-2000 objects).
            try:
                event1 = cal1.event_by_uid(test_uid)
            except NotFoundError:
                event1 = Event(cal1.client, url=cal1.url.join(test_uid + ".ics"), parent=cal1)
            event1.load()

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
                events_in_cal2 = list(_filter_2000(cal2.events()))

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
                dtstart=datetime(2000, 4, 1, 12, 0, 0, tzinfo=utc),
                dtend=datetime(2000, 4, 1, 13, 0, 0, tzinfo=utc),
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

        try:
            ## Try to perform a simple freebusy query
            ## Use a time range in year 2000 to avoid conflicts with real calendar data
            start = datetime(2000, 1, 1, 0, 0, 0, tzinfo=utc)
            end = datetime(2000, 1, 31, 23, 59, 59, tzinfo=utc)

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

        dtstart = datetime(2000, 1, 9, 0, 0, 0, tzinfo=utc)
        dtend = datetime(2000, 1, 10, 0, 0, 0, tzinfo=utc)

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
        from caldav.elements import cdav as _cdav

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
        probe_uid = "csc-schedule-tag-probe"
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
            "DTSTART:20000101T100000Z\r\n"
            "DTEND:20000101T110000Z\r\n"
            "SUMMARY:caldav-server-tester schedule-tag probe\r\n"
            f"ORGANIZER:{own_address}\r\n"
            f"ATTENDEE;RSVP=TRUE:{own_address}\r\n"
            f"ATTENDEE;RSVP=TRUE:{attendee_address}\r\n"
            "END:VEVENT\r\n"
            "END:VCALENDAR\r\n"
        )

        try:
            event = cal.add_event(probe_ical)

            ## RFC6638 s3.2: server MUST return Schedule-Tag on successful PUT.
            if event.props.get(_cdav.ScheduleTag.tag):
                self.set_feature("scheduling.schedule-tag")
                return

            ## Fallback: GET the resource and check the Schedule-Tag response header.
            event.load()
            tag_from_get = event.props.get(_cdav.ScheduleTag.tag)

            if tag_from_get:
                self.set_feature("scheduling.schedule-tag")
                return

            ## Fallback: try PROPFIND for the schedule-tag DAV property
            tag_from_propfind = event.get_property(_cdav.ScheduleTag(), use_cached=False)
            if tag_from_propfind:
                self.set_feature("scheduling.schedule-tag")
            else:
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

        probe_uid = "csc-inbox-delivery-probe-event"
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

        try:
            ## Create an event with a non-UTC timezone (America/Los_Angeles)
            ## Use unique UID since this test deletes the event
            ## (Nextcloud trashbin bug - see https://github.com/nextcloud/server/issues/30096)
            tz = ZoneInfo("America/Los_Angeles")
            event = cal.save_event(
                summary="Timezone test event",
                dtstart=datetime(2000, 6, 15, 14, 0, 0, tzinfo=tz),
                dtend=datetime(2000, 6, 15, 15, 0, 0, tzinfo=tz),
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
