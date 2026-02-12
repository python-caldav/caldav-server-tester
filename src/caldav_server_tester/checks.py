import logging
import re
import time
import uuid
from datetime import timezone
from datetime import datetime
from datetime import date
from datetime import timedelta

from caldav.compatibility_hints import FeatureSet
from caldav.lib.error import NotFoundError, AuthorizationError, ReportError, DAVError
from caldav.calendarobjectresource import Event, Todo, Journal
from caldav.collection import Principal
from caldav.davobject import DAVObject
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
    asdate = lambda foo: foo if type(foo) == date else foo.date()

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


## WORK IN PROGRESS

## TODO: We need some collector framework that can collect all checks,
## build a dependency graph and mapping from a feature to the relevant
## check.


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
        except:
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
        except:
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
                    breakpoint()  ## TODO - do something better here
                except:
                    ## This is not the exception, this is the normal
                    try:
                        cal2 = self.checker.principal.calendar(name=kwargs["name"])
                        cal2.events()
                        assert cal2.id == cal.id
                        self.set_feature("create-calendar.set-displayname")
                    except:
                        self.set_feature("create-calendar.set-displayname", False)

        except DAVError as e:
            ## calendar creation created an exception.  Maybe the calendar exists?
            ## in any case, return exception
            cal = self.checker.principal.calendar(cal_id=cal_id)
            try:
                cal.events()
            except:
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
            if not cal or self.checker.features_checked.is_supported('create-calendar.auto'):
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
            events = cals[0].events()
            self.set_feature("get-current-user-principal.has-calendar", True)
        except:
            self.set_feature("get-current-user-principal.has-calendar", False)

        makeret = self._try_make_calendar(
            name="Yep", cal_id="caldav-server-checker-mkdel-test"
        )
        if makeret[0]:
            ## calendar created
            ## TODO: this is a lie - we haven't really verified this, only on second script run we will be sure
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
        makeret = self._try_make_calendar(cal_id=unique_id, method='mkcol')
        if makeret[0]:
            self.set_feature("create-calendar", {
                "support": "quirk",
                "behaviour": "mkcol-required"})
        else:
            self.set_feature("create-calendar", False)


class PrepareCalendar(Check):
    """
    This "check" doesn't check anything, but ensures the calendar has some known events
    """

    features_to_be_checked = set()
    depends_on = {CheckMakeDeleteCalendar}
    features_to_be_checked = {
        "save-load.event.recurrences",
        "save-load.event.recurrences.count",
        "save-load.todo.recurrences",
        "save-load.todo.recurrences.count",
        "save-load.event",
        "save-load.todo",
        "save-load.todo.mixed-calendar",
        "save-load.journal",
        "save-load.journal.mixed-calendar",
        "save-load.get-by-url",
    }

    def _run_check(self):
        ## Find or create a calendar
        cal_id = "caldav-server-checker-calendar"
        test_cal_info = self.checker.expected_features.is_supported('test-calendar.compatibility-tests', return_type=dict)
        name = test_cal_info.get('name', "Calendar for checking server feature support")
        try:
            if 'name' in test_cal_info:
                calendar = self.checker.principal.calendar(name=name)
            else:
                calendar = self.checker.principal.calendar(cal_id=cal_id)
            calendar.events()
        except:
            assert self.checker.features_checked.is_supported("create-calendar") ## Otherwise we can't test
            calendar = self.checker.principal.make_calendar(cal_id=cal_id, name=name)
                
        self.checker.calendar = calendar
        self.checker.tasklist = calendar
        self.checker.journallist = calendar

        ## Check if GET requests to calendar object URLs work
        url_check_event = None
        url_check_uid = "csc_url_check_" + str(uuid.uuid4())[:8]
        try:
            url_check_event = calendar.save_event(
                summary="url-check event",
                uid=url_check_uid,
                dtstart=datetime(2000, 1, 15, 12, 0, 0, tzinfo=utc),
                dtend=datetime(2000, 1, 15, 13, 0, 0, tzinfo=utc),
            )
            r = self.client.request(str(url_check_event.url))
            if r.status == 404:
                self.set_feature("save-load.get-by-url", False)
            else:
                self.set_feature("save-load.get-by-url")
        except NotFoundError:
            self.set_feature("save-load.get-by-url", False)
        except Exception:
            ## UID conflict or other server error - can't determine get-by-url support
            self.set_feature("save-load.get-by-url", None)
        finally:
            if url_check_event:
                try:
                    url_check_event.delete()
                except Exception:
                    pass

        ## TODO: replace this with one search if possible(?)
        ## Some servers (e.g. CCS) reject time-range queries for old dates
        ## (min-date-time restriction), so fall back to empty lists.
        try:
            events_from_2000 = calendar.search(
                event=True, start=datetime(2000, 1, 1), end=datetime(2001, 1, 1)
            )
        except (AuthorizationError, DAVError):
            events_from_2000 = []
        try:
            tasks_from_2000 = calendar.search(
                todo=True, start=datetime(2000, 1, 1), end=datetime(2001, 1, 1)
            )
        except (AuthorizationError, DAVError):
            tasks_from_2000 = []
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
            return cal.save_object(*largs, **kwargs)

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
        except:
            try:
                tasklist = self.checker.principal.calendar(cal_id=f"{cal_id}_tasks")
                tasklist.todos()
            except:
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
            except DAVError as e: ## exception e for debugging purposes
                self.set_feature("save-load.todo", 'ungraceful')
                return

            task_with_dtstart.load()
            self.set_feature("save-load.todo")
            self.set_feature("save-load.todo.mixed-calendar", False)

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
        except:
            try:
                journallist = self.checker.principal.calendar(
                    cal_id=f"{cal_id}_journals"
                )
                journallist.journals()
            except:
                try:
                    journallist = self.checker.principal.make_calendar(
                        cal_id=f"{cal_id}_journals",
                        name=f"{name} - journals",
                        supported_calendar_component_set=["VJOURNAL"],
                    )
                except:
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
                except:
                    self.set_feature("save-load.journal", "ungraceful")
                    self.checker.cnt -= 1

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

        event_with_rrule_and_count = add_if_not_existing(Event, """BEGIN:VCALENDAR
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
END:VCALENDAR""")
        event_with_rrule_and_count.load()
        component = event_with_rrule_and_count.component
        rrule = component.get('RRULE', None)
        count = rrule and rrule.get('COUNT')
        self.set_feature("save-load.event.recurrences.count", count==[3])

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

        task_with_rrule_and_count = add_if_not_existing(Todo, """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Example Corp.//CalDAV Client//EN
BEGIN:VTODO
UID:takeoutthethrash
DTSTAMP:20001013T151313Z
DTSTART:20001016T065500Z
STATUS:NEEDS-ACTION
DURATION:PT10M
SUMMARY:Weekly task to be done three times
RRULE:FREQ=WEEKLY;BYDAY=MO;COUNT=3
CATEGORIES:CHORE
PRIORITY:3
END:VTODO
END:VCALENDAR""")
        task_with_rrule_and_count.load()
        component = task_with_rrule_and_count.component
        rrule = component.get('RRULE', None)
        count = rrule and rrule.get('COUNT')
        self.set_feature("save-load.todo.recurrences.count", count==[3])

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

        ## Delete any stale objects from year 2000 that aren't part of
        ## the current test set (e.g. leftovers from previous test runs)
        for uid, obj in object_by_uid.items():
            logging.warning("Deleting stale year-2000 object with UID %s", uid)
            obj.delete()
        assert self.checker.calendar.events()
        ## Not asserting on tasklist.todos() here - on servers with broken
        ## comp-type filtering (e.g. Bedework), todos() returns empty even
        ## though todos were saved successfully (verified via load() above).


class CheckSearch(Check):
    depends_on = {PrepareCalendar}
    features_to_be_checked = {
        "search.time-range.event",
        "search.time-range.event.old-dates",
        "search.text.category",
        "search.time-range.todo",
        "search.time-range.todo.old-dates",
        "search.comp-type-optional",
        "search.combined-is-logical-and",
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
            )
            self.set_feature("search.time-range.todo.old-dates", len(tasks) == 1)
        except (AuthorizationError, DAVError):
            self.set_feature("search.time-range.todo.old-dates", "ungraceful")

        ## search.text.category
        try:
            events = cal.search(category="hands", event=True)
            self.set_feature("search.text.category", len(events) == 1)
        except (ReportError, AuthorizationError, DAVError):
            self.set_feature("search.text.category", "ungraceful")
        ## search.combined - uses year-2000 dates, so requires old-dates support
        if self.feature_checked("search.text.category") and self.feature_checked("search.time-range.event.old-dates"):
            try:
                events1 = cal.search(category="hands", event=True, start=datetime(2000, 1, 1, 11, 0, 0), end=datetime(2000, 1, 13, 14, 0, 0))
                events2 = cal.search(category="hands", event=True, start=datetime(2000, 1, 1, 9, 0, 0), end=datetime(2000, 1, 6, 14, 0, 0))
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
                )
            else:
                objects = _filter_2000(cal.search())
            if len(objects) == 0:
                self.set_feature(
                    "search.comp-type-optional",
                    {
                        "support": "unsupported",
                        "description": "search that does not include comptype yields nothing",
                    },
                )
            elif cal == tasklist and not any(x for x in objects if isinstance(x, Todo)):
                self.set_feature(
                    "search.comp-type-optional",
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
                    )
                )
                == self.checker.cnt
            ):
                self.set_feature(
                    "search.comp-type-optional",
                    {
                        "support": "full",
                        "description": "comp-filter is redundant in search as a calendar can only hold one kind of components",
                    },
                )
            elif len(objects) == self.checker.cnt:
                self.set_feature("search.comp-type-optional")
            else:
                ## TODO ... we need to do more testing on search to conclude certainly on this one.  But at least we get something out.
                self.set_feature(
                    "search.comp-type-optional",
                    {
                        "support": "fragile",
                        "description": "unexpected results from date-search without comp-type",
                    },
                )
        except:
            self.set_feature("search.comp-type-optional", {"support": "ungraceful"})


class CheckAlarmSearch(Check):
    depends_on = {PrepareCalendar}
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

        if self.checker.features_checked.is_supported("search.time-range.todo"):
            todos = tl.search(
                start=datetime(2000, 1, 12, tzinfo=utc),
                end=datetime(2000, 1, 13, tzinfo=utc),
                todo=True,
                include_completed=True,
                post_filter=False,
            )
            assert len(todos) == 1
        events = cal.search(
            start=datetime(2000, 2, 12, tzinfo=utc),
            end=datetime(2000, 2, 13, tzinfo=utc),
            event=True,
            post_filter=False,
        )
        self.set_feature("search.recurrences.includes-implicit.event", len(events) == 1)
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
        self.set_feature(
            "search.recurrences.includes-implicit.infinite-scope", len(events) == 1
        )

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
            len(events) == 1
            and events[0].component["dtstart"]
            == datetime(2000, 2, 12, 12, 0, 0, tzinfo=utc),
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
            len(todos) == 1
            and todos[0].component["dtstart"]
            == datetime(2000, 2, 12, 12, 0, 0, tzinfo=utc),
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
            and exception[0].component["dtstart"]
            == datetime(2000, 2, 13, 12, 0, 0, tzinfo=utc)
            and exception[0].component["summary"]
            == "February recurrence with different summary"
            and getattr(exception[0].component.get('RECURRENCE_ID'), 'dt', None) == datetime(2000, 2, 13, 12, tzinfo=utc)
        )


class CheckCaseSensitiveSearch(Check):
    """
    Checks if the server supports case-sensitive and case-insensitive text searches.

    RFC4791 section 9.7.5 specifies that i;ascii-casemap MUST be the default collation,
    and section 7.5 says servers are REQUIRED to support i;octet (case-sensitive).
    """
    depends_on = {PrepareCalendar}
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
                "search.text.case-sensitive",
                len(results_sensitive) == 0 and len(results_sensitive_match) >= 1
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

            self.set_feature(
                "search.text.case-insensitive",
                len(results_insensitive) >= 1
            )
        except (ReportError, DAVError):
            self.set_feature("search.text.case-insensitive", "ungraceful")


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
        except:
            search_name = None
        if not search_name:
            search_name = getattr(self.client, 'username', None)

        any_search_worked = False
        any_ungraceful = False

        ## Use search_principals (v3.0+) or principals (v2.x) method
        _search_principals = getattr(self.client, 'search_principals', None) or getattr(self.client, 'principals', None)
        if not _search_principals:
            self.set_feature("principal-search", False)
            self.set_feature("principal-search.by-name.self", False)
            self.set_feature("principal-search.list-all", False)
            return

        ## principal-search.by-name.self: search for own principal by name
        if search_name:
            try:
                results = _search_principals(name=search_name)
                found_self = any(
                    isinstance(r, Principal)
                    for r in results
                )
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
