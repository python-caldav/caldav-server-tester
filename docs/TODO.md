## Two observations that should be investigated

* When setting `search.time-range.open.start: False`, the whole `search.time-range.open`-tree collapses to False?  This is wrong.  `search.time-range.open` is not an independent feature, but should collapse to unsupported only when `search.time-range.open.*` has been checked and found to be unsupported.

* For Bedework, in the related-to-check - add a new related-to property, save, load and there is an error.  Try to fetch the traceback and investigate.


## Broken xandikos compatibility test

Commit 7c1a38575 breaks xandikos compatibility test:

```
> /home/tobias/caldav-server-tester/src/caldav_server_tester/checks_base.py(74)set_feature()
-> breakpoint()
(Pdb) up
> /home/tobias/caldav-server-tester/src/caldav_server_tester/checks.py(853)_run_check()
-> self.set_feature("search.time-range.todo.old-dates", len(tasks) == 1)
(Pdb) tasks
[Todo(http://localhost:8993/sometestuser/calendars/caldav-server-checker-calendar/csc_simple_task3.ics), Todo(http://localhost:8993/sometestuser/calendars/caldav-server-checker-calendar/csc_task_future.ics)]
(Pdb)
```

While earlier, only the `csc_simple_task3` was returned.

`csc_task_future.ics` was introduced in 7c1a38575.  The comment is also slightly confusing, what does "Task far in the future within year 2000" actually mean?  The due date seems not to be "far in the future" for me, neither compared to the current time nor compared to the dtstart (24 hours duration).  Do we need this task at all?  Most likely one of the existing tasks on the calendar can be used instead.

There are quite some other unexpected results when running the compatibility test towards both xandikos and the other servers, but the one above is the first one I've investigated.

### Done (2026-04-10)

Removed `csc_task_future` from `PrepareCalendar`. The `CheckTodoSearchEdgeCases` open-start
test now uses `end=2000-01-01` and checks that none of `csc_simple_task3` or
`csc_task_with_duration` appear in the results (all our year-2000 tasks start on Jan 8 or later).

Discovered a cascade: `csc_task_future` being returned by xandikos in the Jan 9-10 range
(a xandikos bug) caused `search.time-range.todo.old-dates` to be marked unsupported.
That in turn caused `CheckRecurrenceSearch` to skip its Jan 12-13 precondition check
(the check is gated on `old-dates` support), which falsely showed
`search.recurrences.includes-implicit.todo` as unsupported. Both are now correctly full.

Also sharpened `search.time-range.todo.duration` (added second overlap scenario and a
negative check with `csc_simple_task3`). Updated the xandikos compatibility matrix:
removed the stale `search.time-range.todo.duration: unsupported` entry (xandikos handles
DURATION correctly via its full-file-check fallback, so default "full" applies), and
changed `search.time-range.todo.open-start` from "broken" to "ungraceful" (xandikos returns
500 on open-start searches that involve a DURATION-only VTODO, because the index fallback
path crashes).

## Redundant objects?

Make an overview of all the tasks, events and journals we have on the calendar, compared to all the checks we're doing, maybe some of the events or tasks are redundant?

## Use `PrepareCalendar` class

There is a new class `CheckRelatedTo` that adds a couple of things and checks on them.  The other `save-load.*`-tests are included in the PrepareCalendar class.  Please make things consistent.

## `search.time-range.todo.duration` should be sharpened

This:

```
            found = any(r.component.get("UID") == "csc_task_with_duration" for r in results)
            self.set_feature("search.time-range.todo.duration", found)
```

... causes the test to return "supported" if NO filtering is done.  unknown or unsupported is probably better.

The current test does a search where the start starts within the duration and ends outside the duration.  The opposite should also be tested (start before the duration and end inside the duration).

### Done (2026-04-10)

Added a second overlap scenario [11:00, 13:00] (start before DTSTART, end inside duration)
and a `not_spurious` check: `csc_simple_task3` (DTSTART=Jan 9) must NOT appear in the
Jan-18 range search. If a server returns it, the server is not actually filtering and the
feature is reported as unsupported rather than full.
