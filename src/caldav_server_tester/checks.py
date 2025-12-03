import re
import time
import uuid
from datetime import timezone
from datetime import datetime
from datetime import date
from datetime import timedelta
from zoneinfo import ZoneInfo

from caldav.compatibility_hints import FeatureSet
from caldav.lib.error import NotFoundError, AuthorizationError, ReportError, DAVError
from caldav.calendarobjectresource import Event, Todo, Journal
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
        return date(1980)

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
            ## the caller takes care of setting quirk flag if mkcol
            ## (todo - does this make sense?  Actually the whole _try_make_calendar looks messy to me and should probably be refactored)
            if kwargs.get('method', 'mkcalendar') != 'mkcol':
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
            cal.delete()
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
                cal.delete()
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
    """This "check" was not supposed to check anything, only ensure
    that the calendar has some known events and tasks.  However, as
    some calendars don't even supports saving and loading all kind of
    component types, checks that it's possible to save/load those have
    been thrown in here.

    TODO: can the logic behind save-load.* be consolidated and moved
    into the add_if_not_existing?
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
        "search.comp-type"
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

        ## TODO: replace this with one search if possible(?)
        events_from_2000 = calendar.search(
            event=True, start=datetime(2000, 1, 1), end=datetime(2001, 1, 1)
        )
        tasks_from_2000 = calendar.search(
            todo=True, start=datetime(2000, 1, 1), end=datetime(2001, 1, 1)
        )

        object_by_uid = {}

        self.checker.cnt = 0

        for obj in _filter_2000(events_from_2000 + tasks_from_2000):
            object_by_uid[obj.component["uid"]] = obj

        def add_if_not_existing(*largs, **kwargs):
            self.checker.cnt += 1
            cal = self.checker.tasklist if largs[0] == Todo else self.checker.calendar
            if "uid" in kwargs:
                uid = kwargs["uid"]
            elif not kwargs:
                uid = re.search("UID:(.*)\n", largs[1]).group(1)
            if uid in object_by_uid:
                return object_by_uid.pop(uid)
            ret = cal.save_object(*largs, **kwargs)
            
            return ret

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
                ## include_completed=True will disable lots of complex filtering
                ## logic
                tasklist.todos(include_completed=True)
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

        ## TODO: those three lines are OK for bedework, we will
        ## need to investigate more if the assert breaks on other
        ## servers.
        if not self.checker.tasklist.todos(include_completed=True):
            self.set_feature('search.comp-type', "broken")
        else:
            self.set_feature('search.comp-type')
        assert self.checker.tasklist.todos(include_completed=True)

        simple_event = add_if_not_existing(
            Event,
            summary="Simple event with a start time and an end time",
            uid="csc_simple_event1",
            dtstart=datetime(2000, 1, 1, 12, 0, 0, tzinfo=utc),
            dtend=datetime(2000, 1, 1, 13, 0, 0, tzinfo=utc),
        )
        simple_event.load()
        self.set_feature("save-load.event")

                                                    
        if not self.checker.features_checked.is_supported("save-load.todo.mixed-calendar"):
            try:
                journals = self.checker.principal.make_calendar(
                    cal_id=f"{cal_id}_journals",
                    name=f"{name} - journals",
                    supported_calendar_component_set=["VJOURNAL"])
            except:
                journals = self.checker.calendar
        else:
            journals = self.checker.calendar
        self.checker.journals = journals
        try:
            j = journals.add_journal(
                summary="journal test",
                dtstart=datetime(2000, 6, 1),
                description="This is a journal entry",
                uid="csc_journal_1")
            j.load()
            self.set_feature("save-load.journal")
        except NotFoundError as e:
            self.set_feature("save-load.journal", 'unsupported')
        except DAVError as e:
            self.set_feature("save-load.journal", 'ungraceful')

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
UID:csc_weeklymeeting
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
UID:csc_task_with_count
DTSTAMP:20001013T151313Z
DTSTART:20001016T065500Z
STATUS:NEEDS-ACTION
DURATION:PT10M
SUMMARY:Weekly task to be done three times
RRULE:FREQ=WEEKLY;COUNT=3
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

        simple_event = add_if_not_existing(
            Event,
            description="Simple event without a summary",
             uid="csc_simple_event_no_summary",
            dtstart=datetime(2000, 3, 1, 12, 0, 0, tzinfo=utc),
            dtend=datetime(2000, 3, 1, 13, 0, 0, tzinfo=utc),
        )

        ## No more existing IDs in the calendar from 2000 ... otherwise,
        ## more work is needed to ensure those won't pollute the tests nor be
        ## deleted by accident
        assert not object_by_uid
        assert self.checker.calendar.events()
        assert self.checker.tasklist.todos(include_completed=True)


class SearchMixIn:
    ## Boilerplate
    def search_find_set(self, cal_or_searcher, feature, num_expected=None, not_so_fast=False, min_num_expected=None, max_num_expected=None, **search_args):
        try:
            if num_expected is not None:
                min_num_expected = num_expected
                max_num_expected = num_expected
            if min_num_expected is None:
                min_num_expected=1
            if max_num_expected is None:
                max_num_expected=65536
            results = cal_or_searcher.search(**search_args, post_filter=False)
            cnt = len(results)
            is_good = cnt >= min_num_expected and cnt <= max_num_expected
            if not not_so_fast or not is_good:
                self.set_feature(feature, is_good)
            return is_good
        except ReportError:
            self.set_feature(feature, "ungraceful")
            return False

class CheckSearch(Check, SearchMixIn):
    depends_on = {PrepareCalendar}
    features_to_be_checked = {
        "search.time-range.event",
        "search.time-range.todo",
        "search.text",
        "search.text.case-sensitive",
        "search.text.case-insensitive",
        "search.text.substring",
        "search.text.by-uid",
        "search.is-not-defined",
        "search.text.category",
        "search.text.category.substring",
        "search.comp-type-optional",
        "search.combined-is-logical-and",
    }  ## TODO: there are still lots of corner cases to be considered, particularly wrg of time-range searches

    def _run_check(self):
        cal = self.checker.calendar
        tasklist = self.checker.tasklist

        self.search_find_set(
            cal, "search.time-range.event", 1, 
            start=datetime(2000, 1, 1, tzinfo=utc),
            end=datetime(2000, 1, 2, tzinfo=utc),
            event=True,
        )
        self.search_find_set(
            tasklist, "search.time-range.todo", 1,
            start=datetime(2000, 1, 9, tzinfo=utc),
            end=datetime(2000, 1, 10, tzinfo=utc),
            todo=True,
            include_completed=True,
        )

        ## summary search
        self.search_find_set(
            cal, "search.text", 1,
            summary="Simple event with a start time and an end time",
            event=True)

        ## summary search is by default case sensitive
        ## As for now, we'll skip this test if text search was
        ## already found not to be working.
        ## TODO: instead, we should do two searches here, one with correct
        ## casing and one without, and ensure the first one returns 1 element.
        if self.checker.features_checked.is_supported("search.text"):
            self.search_find_set(
                cal, "search.text.case-sensitive", 0,
                summary="simple event with a start time and an end time",
                event=True)
        else:
            self.set_feature("search.text.case-sensitive", False)

        ## summary search, case insensitive
        searcher = CalDAVSearcher(event=True)
        searcher.add_property_filter('summary', "simple event with a start time and an end time", case_sensitive=False)
        self.search_find_set(
            searcher, "search.text.case-insensitive", 1, calendar=cal)

        ## "is not defined"-search
        searcher = CalDAVSearcher(event=True)
        searcher.add_property_filter('summary', None, operator="undef")
        ## bedeworks does not support much - but it supports seaching for events without summary set!
        ## The unit tests still breaks, because it doesn't support searching for events without category
        no_summary_found = self.search_find_set(
            searcher, "search.is-not-defined", 1, calendar=cal, not_so_fast=True)
        if no_summary_found:
            found = cal.search(no_categories=True)
            if len(found) < 3 or any(x.component.categories for x in found):
                self.set_feature("search.is-not-defined", "fragile")
            else:
                self.set_feature("search.is-not-defined")

        ## summary search, substring
        ## The RFC says that TextMatch is a subetext search
        self.search_find_set(
            cal, "search.text.substring", 1,
            summary="Simple event with a start time and",
            event=True)

        ## TODO - we may be testing the wrong thing here!
        ## 1) if search.text is not supported because the server yields nothing, then AS FOR NOW cal.object_by_uid will raise a NotFoundError.  This will change when https://github.com/python-caldav/caldav/issues/586 is fixed
        ## 2) if search.text is not supported because the server gives everything, then .object_by_uid will find the right thing through client-side filtering
        
        ## TODO - what we really should do:
        
        ## 1) Send the XML-query to the server as given in he examples in the RFC, shortcutting all logic in cal.object_by_uid, cal.search etc
        ## 2) Unless there exist servers with fragile text searching that supports search for uid, then probably the whole feature and check should be yanked
        try:
            event = cal.event_by_uid("csc_simple_event1")
            if event and str(event.component['uid']) == "csc_simple_event1":
                self.set_feature("search.text.by-uid")
            else:
                self.set_feature("search.text.by-uid", "broken")
        except NotFoundError:
            self.set_feature("search.text.by-uid", "unsupported")
        except DAVError:
            self.set_feature("search.text.by-uid", "ungraceful")

        ## search.text.category
        self.search_find_set(
            cal, "search.text.category", 1,
            category="hands", event=True)

        ## search.combined
        if self.feature_checked("search.text.category"):
            events1 = cal.search(category="hands", event=True, start=datetime(2000, 1, 1, 11, 0, 0), end=datetime(2000, 1, 13, 14, 0, 0), post_filter=False)
            events2 = cal.search(category="hands", event=True, start=datetime(2000, 1, 1, 9, 0, 0), end=datetime(2000, 1, 6, 14, 0, 0), post_filter=False)
            self.set_feature("search.combined-is-logical-and", len(events1) == 1 and len(events2) == 0)
            self.search_find_set(
                cal, "search.text.category.substring", 1,
                category="eet",
                event=True)
        ## TODO: the try/except below may be too wide
        try:
            summary = "Simple event with a start time and"
            ## Text search with and without comptype
            tswc = cal.search(summary=summary, event=True, post_filter=False)
            tswoc = cal.search(summary=summary, post_filter=False)
            ## Testing if search without comp-type filter returns both events and tasks
            if self.feature_checked("search.time-range.todo"):
                objects = cal.search(
                    start=datetime(2000, 1, 1, tzinfo=utc),
                    end=datetime(2001, 1, 1, tzinfo=utc),
                    post_filter=False,
                )
            else:
                objects = list(_filter_2000(cal.search(post_filter=False)))
            if len(objects) == 0 and not tswoc:
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
                    ## Also search tasklist without comp-type to see if we get all objects
                    tasklist.search(
                        start=datetime(2000, 1, 1, tzinfo=utc),
                        end=datetime(2001, 1, 1, tzinfo=utc),
                        post_filter=False,
                    )
                )
                == self.checker.cnt and
                (tswoc or not tswc)
            ):
                self.set_feature(
                    "search.comp-type-optional",
                    {
                        "support": "full",
                        "description": "comp-filter is redundant in search as a calendar can only hold one kind of components",
                    },
                )
            elif len(objects) == self.checker.cnt and (tswoc or not tswc):
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
        except DAVError:
            self.set_feature("search.comp-type-optional", {"support": "ungraceful"})


class CheckRecurrenceSearch(Check, SearchMixIn):
    depends_on = {CheckSearch}
    features_to_be_checked = {
        "search.time-range.accurate",
        "search.recurrences.includes-implicit.todo",
        "search.recurrences.includes-implicit.todo.pending",
        "search.recurrences.includes-implicit.event",
        "search.recurrences.includes-implicit.infinite-scope",
        "search.recurrences.expanded.todo",
        "search.recurrences.expanded.event",
        "search.recurrences.expanded.exception",
    }

    def _run_check(self) -> None:

        cal = self.checker.calendar
        tl = self.checker.tasklist
        events = cal.search(
            start=datetime(2000, 1, 12, tzinfo=utc),
            end=datetime(2000, 1, 13, tzinfo=utc),
            event=True,
            post_filter=False,
        )
        ## This is a basic sanity check - there should be at least one event
        ## (the monthly recurring event with dtstart 2000-01-12)
        ## Some servers may incorrectly return additional events
        assert len(events) >= 1
        if self.checker.features_checked.is_supported("search.time-range.todo"):
            todos = tl.search(
                start=datetime(2000, 1, 12, tzinfo=utc),
                end=datetime(2000, 1, 13, tzinfo=utc),
                todo=True,
                include_completed=True,
                post_filter=False,
            )
            ## Basic sanity check - should find at least the recurring task
            assert len(todos) >= 1
        events = cal.search(
            start=datetime(2000, 2, 12, tzinfo=utc),
            end=datetime(2000, 2, 13, tzinfo=utc),
            event=True,
            post_filter=False,
        )

        ## Check if server returns accurate time-range results
        ## Some servers return events that fall outside the requested time range
        ## Expected: only csc_monthly_recurring_event (recurrence at 2000-02-12 12:00)
        ## Buggy behavior: also returns csc_monthly_recurring_with_exception (2000-02-13 12:00, outside range)
        ##                 and possibly csc_weeklymeeting (no February recurrence at all)
        self.set_feature("search.time-range.accurate", len(events) <= 1)
        self.set_feature("search.recurrences.includes-implicit.event", len(events) >= 1)
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
        ## Xandikos version 0.2.12 breaks here for me.
        ## It didn't break earlier.
        ## Everything is exactly the same here.  Same data on the server, same query
        ## There must be some local state in xandikos causing some bug to happen
        ## If the server has accurate time-range searches, we expect exactly 1 result
        ## Otherwise, we just check that we got at least one result
        if self.feature_checked("search.time-range.accurate"):
            assert len(exception) == 1
        else:
            assert len(exception) >= 1
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


class CheckPrincipalSearch(Check):
    """Checks support for principal search operations

    Tests those capabilities:
    - principal-search.by-name.self: Search for own principal by name
    - principal-search.list-all: List all principals without filter

    TODO: principal-search.by-name (general name search) is not tested
    as it requires setting up another user with a known name.  What
    we're really testing is principal-search.by-name.self, and then we
    assume principal-search.by-name is the same.

    TODO: if get-current-user-principal is not supported, we cannot
    test the rest, and we assume they are broken

    """

    depends_on = { CheckGetCurrentUserPrincipal }
    features_to_be_checked = {
        "principal-search.list-all",
        "principal-search.by-name",
    }

    def _run_check(self) -> None:
        client = self.client

        if not self.checker.features_checked.is_supported("get-current-user-principal"):
            ## if we cannot get the current user principal, then we cannot perform the
            ## search for principals.  Assume searching for principals does not work.
            ## Arguably, the get-current-user-principal feature
            ## could have been renamed to principal-search.
            self.set_feature("principal-search", False)
            return
        
        ## Try to get the current principal first
        principal = client.principal()

        ## Search for own principal by name
        try:
            my_name = principal.get_display_name()
            if my_name:
                my_principals = client.principals(name=my_name)
                if isinstance(my_principals, list) and len(my_principals) == 1:
                    if my_principals[0].url == principal.url:
                        self.set_feature("principal-search.by-name", True)
                    else:
                        self.set_feature("principal-search.by-name", {
                            "support": "fragile",
                            "behaviour": "Returns wrong principal"
                        })
                elif len(my_principals) == 0:
                    self.set_feature("principal-search.by-name", {
                        "support": "unsupported",
                        "behaviour": "Search by own name returns nothing"
                    })
                else:
                    self.set_feature("principal-search.by-name", {
                        "support": "fragile",
                        "behaviour": f"Returns {len(my_principals)} principals instead of 1"
                    })
            else:
                ## No display name, can't test
                self.set_feature("principal-search.by-name", {
                    "support": "unknown",
                    "behaviour": "No display name available to test"
                })
        except (ReportError, DAVError, AuthorizationError) as e:
            self.set_feature("principal-search.by-name", {
                "support": "ungraceful",
                "behaviour": f"Search by name failed: {e}"
            })

        ## List all principals
        try:
            all_principals = client.principals()
            ## Some servers return empty list, some return principals
            ## We know there exists at least one principal (self)
            if isinstance(all_principals, list) and len(all_principals)>0:
                self.set_feature("principal-search.list-all", True)
            else:
                self.set_feature("principal-search.list-all", {
                    "support": "unsupported",
                    "behaviour": "principals() didn't return a list with at least one element"
                })
        except (ReportError, DAVError, AuthorizationError) as e:
            self.set_feature("principal-search.list-all", {
                "support": "ungraceful",
                "behaviour": f"List all principals failed: {e}"
            })


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
            ## Get existing event from first calendar (created by PrepareCalendar)
            event1 = cal1.event_by_uid(test_uid)
            event1.load()

        
            ## Get the event data for reuse in cal2
            event_ical = event1.data

            
            ## Create second calendar
            cal2 = self.client.principal().make_calendar(name=cal2_name)


            try:
                ## Try to save event with same UID to second calendar
                event2 = cal2.save_object(Event, event_ical)


                ## Check if the event actually exists in cal2
                events_in_cal2 = list(_filter_2000(cal2.events()))

                ## Check if event still exists in cal1 (Zimbra moves it instead of copying)
                events_in_cal1 = list(_filter_2000(cal1.search(uid=test_uid, event=True, post_filter=False)))
                event_was_moved = len(events_in_cal1) == 0

                if len(events_in_cal2) == 0:
                    ## Server silently ignored the duplicate
                    self.set_feature("save.duplicate-uid.cross-calendar", {
                        "support": "unsupported",
                        "behaviour": "silently-ignored"
                    })
                elif len(events_in_cal2) == 1 and event_was_moved:
                    ## Server moved the event instead of creating a duplicate (Zimbra behavior)
                    self.set_feature("save.duplicate-uid.cross-calendar", {
                        "support": "unsupported",
                        "behaviour": "moved-instead-of-copied"
                    })
                    ## Move event back to cal1 to avoid breaking other tests
                    try:
                        event2.load()
                        cal1.save_object(Event, event2.data)
                        event2.delete()
                    except Exception:
                        pass
                elif len(events_in_cal2) == 1:
                    assert events_in_cal2[0].component['uid'] == test_uid
                    ## Server accepted the duplicate
                    ## Verify they are treated as separate entities.
                    event1 = cal1.event_by_uid(test_uid)
                    event1.load()

                    ## Store original summary to check later
                    original_summary = str(event1.icalendar_instance.walk('vevent')[0].get('summary', ''))

                    ## Modify event in cal2 and verify cal1's event is unchanged
                    event2.icalendar_instance.walk('vevent')[0]['summary'] = "Modified in Cal2"
                    event2.save()

                    event1.load()
                    current_summary = str(event1.icalendar_instance.walk('vevent')[0].get('summary', ''))
                    if current_summary == original_summary:
                        self.set_feature("save.duplicate-uid.cross-calendar", True)
                    else:
                        self.set_feature("save.duplicate-uid.cross-calendar", {
                            "support": "fragile",
                            "behaviour": "Modifying duplicate in one calendar affects the other"
                        })
                else:
                    self.set_feature("save.duplicate-uid.cross-calendar", {
                        "support": "fragile",
                        "behaviour": f"Unexpected: {len(events_in_cal2)} events in cal2"
                    })

            except (DAVError, AuthorizationError) as e:

                ## Server rejected the duplicate with an error
                self.set_feature("save.duplicate-uid.cross-calendar", {
                    "support": "ungraceful",
                    "behaviour": f"Server error: {type(e).__name__}"
                })
            finally:
                ## Cleanup
                try:
                    cal2.delete()
                except Exception:
                    pass

        finally:
            ## No need to cleanup test event - it's owned by PrepareCalendar
            pass




class CheckAlarmSearch(Check):
    """
    Checks support for time-range searches on alarms (RFC4791 section 9.9)
    """

    depends_on = {PrepareCalendar}
    features_to_be_checked = {"search.time-range.alarm"}

    def _run_check(self) -> None:
        cal = self.checker.calendar

        ## Create an event with an alarm
        test_uid = "csc_alarm_test_event"
        test_event = None

        ## Clean up any leftover from previous run
        try:
            events = _filter_2000(cal.search(
                start=datetime(2000, 5, 1, tzinfo=utc),
                end=datetime(2000, 5, 2, tzinfo=utc),
                event=True,
                post_filter=False,
            ))
            for evt in events:
                if evt.component.get("uid") == test_uid:
                    evt.delete()
                    break
        except:
            pass

        try:
            ## Create event with alarm
            ## Event at 08:00, alarm at 07:45 (15 minutes before)
            test_event = cal.save_object(
                Event,
                summary="Alarm test event",
                uid=test_uid,
                dtstart=datetime(2000, 5, 1, 8, 0, 0, tzinfo=utc),
                dtend=datetime(2000, 5, 1, 9, 0, 0, tzinfo=utc),
                alarm_trigger=timedelta(minutes=-15),
                alarm_action="AUDIO",
            )

            ## Search for alarms after the event start (should find nothing)
            events_after = cal.search(
                event=True,
                alarm_start=datetime(2000, 5, 1, 8, 1, tzinfo=utc),
                alarm_end=datetime(2000, 5, 1, 8, 7, tzinfo=utc),
                post_filter=False,
            )

            ## Search for alarms around the alarm time (should find the event)
            events_alarm_time = cal.search(
                event=True,
                alarm_start=datetime(2000, 5, 1, 7, 40, tzinfo=utc),
                alarm_end=datetime(2000, 5, 1, 7, 55, tzinfo=utc),
                post_filter=False,
            )

            ## Check results
            if len(events_after) == 0 and len(events_alarm_time) == 1:
                self.set_feature("search.time-range.alarm", True)
            else:
                self.set_feature("search.time-range.alarm", False)

        except (ReportError, DAVError) as e:
            ## Some servers don't support alarm searches at all
            self.set_feature("search.time-range.alarm", {
                "support": "unsupported",
                "behaviour": f"alarm search failed: {e}"
            })
        finally:
            ## Clean up
            if test_event is not None:
                try:
                    test_event.delete()
                except:
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
        try:
            my_objects = cal.objects()
            sync_token = my_objects.sync_token

            if not sync_token or sync_token == "":
                self.set_feature("sync-token", False)
                return

            ## Initially assume full support
            sync_support = "full"
            sync_behaviour = None
        except (ReportError, DAVError, AttributeError):
            self.set_feature("sync-token", False)
            return

        ## Clean up any leftover test event from previous failed run
        test_uid = "csc_sync_test_event_1"
        try:
            events = _filter_2000(cal.search(
                start=datetime(2000, 4, 1, tzinfo=utc),
                end=datetime(2000, 4, 2, tzinfo=utc),
                event=True,
                post_filter=False,
            ))
            for evt in events:
                if evt.component.get("uid") == test_uid:
                    evt.delete()
                    break
        except:
            pass

        ## Test 2 & 3: Check for time-based and fragile sync tokens
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
            my_objects = cal.objects()
            sync_token1 = my_objects.sync_token

            ## Immediately check for changes (should be none)
            my_changed_objects = cal.objects_by_sync_token(sync_token=sync_token1)
            immediate_count = len(list(my_changed_objects))

            if immediate_count > 0:
                ## Fragile sync tokens return extra content
                sync_support = "fragile"

            ## Test for time-based sync tokens
            ## Modify the event within the same second
            test_event.icalendar_instance.subcomponents[0]["SUMMARY"] = "Modified immediately"
            test_event.save()

            ## Check for changes immediately (time-based tokens need sleep(1))
            my_changed_objects = cal.objects_by_sync_token(sync_token=sync_token1)
            changed_count_no_sleep = len(list(my_changed_objects))

            if changed_count_no_sleep == 0:
                ## Might be time-based, wait a second and try again
                time.sleep(1)
                test_event.icalendar_instance.subcomponents[0]["SUMMARY"] = "Modified after sleep"
                test_event.save()
                time.sleep(1)

                my_changed_objects = cal.objects_by_sync_token(sync_token=sync_token1)
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
                my_changed_objects = cal.objects_by_sync_token(sync_token=sync_token2)
                deleted_count = len(list(my_changed_objects))

                ## If we get here without exception, deletion is supported
                self.set_feature("sync-token.delete", True)
            except DAVError as e:
                ## Some servers (like sabre-based) return "418 I'm a teapot" or other errors
                self.set_feature("sync-token.delete", {
                    "support": "unsupported",
                    "behaviour": f"sync fails after deletion: {e}"
                })
        finally:
            ## Ensure cleanup even if an exception occurred
            if test_event is not None:
                try:
                    test_event.delete()
                except:
                    pass


class CheckFreeBusyQuery(Check):
    """
    Checks support for RFC4791 free/busy-query REPORT

    Tests if the server supports free/busy queries as specified in RFC4791 section 7.10.
    The free/busy query allows clients to retrieve free/busy information for a time range.
    """

    depends_on = {PrepareCalendar}
    features_to_be_checked = {
        "freebusy-query.rfc4791",
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
            if freebusy and hasattr(freebusy, 'vobject_instance'):
                self.set_feature("freebusy-query.rfc4791", True)
            else:
                self.set_feature("freebusy-query.rfc4791", {
                    "support": "unsupported",
                    "behaviour": "freebusy query returned invalid or empty response"
                })
        except (ReportError, DAVError, NotFoundError) as e:
            ## Server doesn't support freebusy queries
            ## Common responses: 500 Internal Server Error, 501 Not Implemented
            self.set_feature("freebusy-query.rfc4791", {
                "support": "ungraceful",
                "behaviour": f"freebusy query failed: {e}"
            })
        except Exception as e:
            ## Unexpected error
            self.set_feature("freebusy-query.rfc4791", {
                "support": "broken",
                "behaviour": f"unexpected error during freebusy query: {e}"
            })


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
            tz = ZoneInfo("America/Los_Angeles")
            event = cal.save_event(
                summary="Timezone test event",
                dtstart=datetime(2000, 6, 15, 14, 0, 0, tzinfo=tz),
                dtend=datetime(2000, 6, 15, 15, 0, 0, tzinfo=tz),
                uid="csc_timezone_test_event",
            )
            
            ## Try to load the event back
            event.load()
            
            ## Verify the event was saved correctly
            if event.vobject_instance:
                self.set_feature("save-load.event.timezone")
                ## Clean up
                try:
                    event.delete()
                except:
                    pass
            else:
                self.set_feature("save-load.event.timezone", {
                    "support": "broken",
                    "behaviour": "Event with timezone was saved but could not be loaded"
                })
        except AuthorizationError as e:
            ## Server rejected the event with a 403 Forbidden
            ## This is the specific issue reported in GitHub #372
            self.set_feature("save-load.event.timezone", {
                "support": "unsupported",
                "behaviour": f"Server rejected event with timezone (403 Forbidden): {e}"
            })
        except DAVError as e:
            ## Other DAV error (e.g., 400 Bad Request, 500 Internal Server Error)
            self.set_feature("save-load.event.timezone", {
                "support": "ungraceful",
                "behaviour": f"Server error when saving event with timezone: {e}"
            })
        except Exception as e:
            ## Unexpected error
            self.set_feature("save-load.event.timezone", {
                "support": "broken",
                "behaviour": f"Unexpected error during timezone test: {e}"
            })
