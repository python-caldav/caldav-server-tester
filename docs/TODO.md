# Rename / rethink `non-existing-raises-not-found`

(Discussion 2026-06-15, not yet acted on.)

The `non-existing-raises-not-found` feature (default `full`) records whether
looking up a missing resource yields 404/`NotFoundError`; the deviant case
(allegedly Robur) answers 403.  Findings:

* It is effectively a **test-tolerance switch**, not a library feature — nothing
  in `caldav/` reads it; the only consumer is the `_notFound()` helper in
  `tests/test_caldav.py`, which uses it to widen `pytest.raises(NotFoundError)`
  to `pytest.raises(DAVError)`.
* ~~It has **never been verified** by this server-tester.~~  Verified against
  Robur 2026-08-26: it answers **403 for everything** that does not exist below
  `/calendars/` and `/principals/`, a missing calendar *object* included (a raw
  `GET` and a raw `PROPFIND` on a missing `.ics` in an existing calendar both
  give 403; only paths outside those namespaces give a 404, from the web
  server).
* ~~`CheckNonExistingResource` probes a missing **object resource** (an event
  URL), not a missing **calendar collection**.~~  It now probes both; the
  collection axis is the new `non-existing-raises-not-found.collection`
  subfeature.  The two do differ — but not for the reason assumed here:
  `CalendarObjectResource.load()` retries a failed GET as a calendar-multiget
  REPORT against the parent calendar, and Robur reports the missing href with an
  inner 404 there, so the object lookup ends in `NotFoundError` after all, while
  a missing collection (no such fallback) surfaces the 403 as
  `AuthorizationError`.  **Both features therefore measure the client-visible
  exception, not the server's status code** — a probe of the raw status would
  have to bypass `load()`.

Decisions:

* **Do not** fold it into `create-calendar.auto`.  The calendar axis has three
  poles (auto-create / 404 / 403); the object-resource axis has only two
  (404 / 403) — no auto-create analogue — so they are not one axis.
* Proposed rename: **`lookup.not-found`** (clearly a lookup concern, not
  save-load, not calendar-create).  Cross-reference `create-calendar.auto` in
  the descriptions as the related "access a non-existent target" axis.
* Re-tag Robur as `ungraceful` (server throws a different error) rather than
  `unsupported` per the taxonomy — still open.  The probe and the Robur profile
  both say `unsupported` for the collection axis, matching the feature
  description ("recorded as 'unsupported' rather than 'broken'"), though
  `ungraceful` arguably fits the taxonomy better; both resolve to False, so
  nothing behaves differently either way.  The other half ("drop the speculative
  Robur entry back to the default until the server can actually be probed") is
  done: the server was probed, and the parent is back at its `full` default.
* Renaming touches both repos: feature definition + Robur dict + `_notFound()`
  in caldav, and the 3 refs in `caldav-server-tester/.../checks.py`.  They must
  land together or the renamed key silently falls back to its default.

Related: if a `lookup.*` namespace is introduced, consider moving
`save-load.get-by-url` → **`lookup.get-by-url`** for consistency (it is also a
lookup concern — GET on an object-resource URL returns 200 vs 404).  Caveats:

* Unlike `non-existing-raises-not-found`, this one *is* consumed by library
  logic (the UID-based fallback when GET-by-URL is unsupported), so the rename is
  more invasive.
* References to update: definition + two commented-out server entries +
  prose cross-refs in caldav (`compatibility_hints.py`), and 2-3 refs in
  `caldav-server-tester/.../checks.py`.  Again both repos must land together.

Open question: is `lookup` the right umbrella, and what else belongs in it?
(`lookup.not-found`, `lookup.get-by-url`, possibly `save-load.stable-url`?)

Note the separate `url.*` namespace that now exists (`url.encode-at`): that one
is about how a URL is *spelled* rather than what a lookup returns, so it is
probably not a `lookup.*` candidate — but if `lookup.get-by-url` happens, the
two namespaces sit next to each other and the split should be stated explicitly
in the descriptions.

---

# Optimistic read-back with retry, instead of a configured delay

(Discussion 2026-09-08, not acted on.)

The `delay` of `synchronous-write` is a flat sleep after every write, and the
alternative considered was: continue immediately whenever the read-back meets
expectations, and wait-and-reprobe only when it does not.  That is the better
shape in principle - it costs nothing on a synchronous server and adapts to a
slow one without anybody having to measure it first - and it is what the
calendar create/delete probes already do locally.

Two things stopped it from being done here:

* It needs a retry contract for **every** read-back in `checks.py`, not just the
  calendar lifecycle - hundreds of `events()` / `search()` / `load()` sites.
  Retrofitting that is a redesign, not a change.
* Checks that expect an **absence** cannot use it.  `CheckNonExistingResource`
  and every negative search assertion cannot tell "not yet" from "correctly
  absent", so they would have to wait the full timeout on every run - putting
  the arbitrary delay on the *conformant* servers rather than the broken ones.

What was done instead: the delays are measured and reported (`CheckSynchronousWrite`,
and the `delay` key on `create-calendar` / `delete-calendar`), an observation
that outgrows the configured value is warned about, and the configured value
stays something a human writes into the profile.  Read-back retry remains the
right answer for the save-load side if the redesign is ever budgeted.

# Possibly everything below is utterly obsoleted

## Two observations that should be investigated

* When setting `search.time-range.open.start: False`, the whole `search.time-range.open`-tree collapses to False?  This is wrong.  `search.time-range.open` is not an independent feature, but should collapse to unsupported only when `search.time-range.open.*` has been checked and found to be unsupported.

* For Bedework, in the related-to-check - add a new related-to property, save, load and there is an error.  Try to fetch the traceback and investigate.  (Bedework 5.0.0, 2026-09-12: the first `RELATED-TO` line survives a save/load round-trip and every later one is stripped - the same behaviour `bedework_3_10_3` records, so this is not a 3.x leftover.)

* ~~`create-calendar`'s `empty-207` behaviour is recorded in `bedework_5_0_0` but cannot be **probed**~~ (done 2026-09-12: the probe keeps the raw `MKCALENDAR`/`MKCOL` responses through `ServerQuirkChecker.record_responses()` and grades a lone status-less `DAV:response` as the quirk, so Bedework 5 now reports `quirk`/`empty-207` rather than `full`).


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

## A broad `except DAVError` turns a transient failure into a permanent verdict

(Clean-context review 2026-09-05, filed rather than fixed.)

`DAVError` is the base of every error the caldav library raises, so every
`except (AuthorizationError, DAVError)` in `checks.py` also catches a 500, a
dropped connection and a `RateLimitError`.  Roughly 25 handlers then record a
verdict from it — `False`/`unsupported` in most of them, `ungraceful` in the
ones corrected in September 2026 — and either way a server having a bad minute
gets an authoritative claim written about it.

That contradicts what the project settled on elsewhere: `fix: an unprobed
feature is unknown, not unsupported`, and the `url.encode-at` probe, which
records `unknown` for a 5xx precisely so that "a momentary outage must not
become a permanent claim about the server's error handling".

The fix is not local — narrowing two handlers while the other two dozen keep
the broad catch buys nothing.  What it wants is one helper that classifies a
caught `DAVError`: 4xx is evidence (`ungraceful`, or `unsupported` where the
server answered rather than refused), while 5xx, a lost connection and
rate-limiting are `unknown`.  Then every handler in the file goes through it.
Worth its own round, and it will move recorded verdicts on servers that were
merely unwell during a run.

`refusal_status()` (with `graded_refusal()`, from the `save-load.event.no-dtend`
work) is a start on that helper, and two gaps were found reviewing the
October 2026 merges:

- `CheckPropfindAllprop._check_principal_depth1` still grades any
  `DAVError` and any non-2xx (502, 503, 429, a stale-session 401) as
  `unsupported`; it should go through `refusal_status()` and record
  `unknown` when that returns None.
- `refusal_status()` tells a 401 from a 403 only by `exc.reason ==
  "Unauthorized"`, the server's free-text reason phrase (caldav puts
  "None given" there when there is none).  The robust fix is upstream:
  caldav keeping the HTTP status on `AuthorizationError`.

## `save-load.todo` on a server that stores a VTODO nowhere

(Clean-context review 2026-09-14, filed rather than fixed.)

`_prepare_task_calendar` records `save-load.todo` as `ungraceful` whenever the
first task fixture cannot be saved.  That was written for a failed save on an
otherwise working server; on Bedework 5, where no collection a client can
create accepts a VTODO at all, "it works, but the client has to catch an error"
is the wrong claim, and the `bedework_5_0_0` profile says `unknown`.  Decide
whether such a server is `unsupported` (with a behaviour note) and tell the two
cases apart in the probe.

## Probe calendars deleted by `cal_id`

(Clean-context review 2026-09-14, filed rather than fixed.)

`CheckSupportedComponentSet._delete_probe_calendars` deletes
`principal.calendar(cal_id=...)` for both its probe calendars (`CAL_ID` and
`CAL_ID_MULTIPLE`), although the check itself notes that the
`cal_id` is not the collection's address on a server that relocates it.  The
probe calendar has no display name either, so a prefix sweep may miss it too.
No server is known to leak one - OX, the relocating server, also answers at the
`cal_id` address - but deleting the calendar objects `make_calendar` returned
would not depend on that.

## A fragile retry window can hide an asynchronous delete

(Clean-context review 2026-09-15, filed rather than fixed.)

`asynchronous_delay()` ignores the `delay` of a fragile verdict, since there it
is a retry window rather than a write delay.  But `record_worse` replaces the
whole node: when the ordinary delete probe records `quirk` with a real
asynchronous `delay` and the re-created-calendar probe then records `fragile`
(Cyrus's shape), the asynchronous delay is gone, `CheckSynchronousWrite` sees
none and grades `synchronous-write` `full`.  No server is known to be both.
The fix is to record a fragile verdict's retry window under a key of its own
(`retry-window`), keep `delay` for asynchronous processing only, and have
`record_worse` carry a displaced `delay` over.

# One standard for placeholder URLs in fixtures and docstrings

Test fixtures and docstrings invent a host whenever they need a URL, and every
new invention is a link the `lychee` pre-push hook tries to resolve and then
fails on.  `.lycheeignore` has two such hosts in it already
(`https://host/`, `https://x/dav/`) and grew two more in September 2026
(`http://dav/`, `https://h/`) — one of them only after a push was refused with
the commits already approved.  Loopback URLs no longer count: the pre-push hook
and the CI link check both run with `--exclude-loopback` since a
`http://localhost/` fixture refused another push.

Standardise on `example.com`, which RFC 2606 §3 reserves for exactly this and
which link checkers know not to chase, and the ignore list stops growing.
`.invalid` (RFC 2606 §2) would also work and reads more explicitly as
unresolvable, but `example.com` is the more familiar convention and the point is
to have *one*.

The work is a sweep of `tests/` and the docstrings in
`src/caldav_server_tester/`, plus dropping the placeholder entries from
`.lycheeignore`.  Nothing depends on the current hostnames — they are never
connected to — so it is a rename and a test run.

# A probe for a feature key the installed caldav lacks aborts the whole run

(Clean-context review 2026-09-18, filed rather than fixed.)

`Check.run_check` ends by asserting that every key in `features_to_be_checked`
was recorded.  `FeatureSet.set_feature` warns and records nothing for a key that
has no `FEATURES` entry, so a probe written against a caldav that has not been
released yet does not merely abstain: the `AssertionError` is re-raised out of
`run_check`, `ServerQuirkChecker.check_all()` dies on it, and every check
ordered after the new one never runs.  A user on the pinned caldav loses most of
the report and gets an assertion instead of a verdict.

This is a normal state of affairs between releases — the practice is to develop
on corresponding branches in both repositories and to release caldav first, so
this window is expected — but the failure mode should be "this one probe could
not run", not "the report stops here".  Guards in the individual checks were
considered and rejected as too intrusive; the fix belongs in `run_check`, which
can tell a key that is absent from `FeatureSet.FEATURES` from one a probe
forgot to set.

# `auth.www-authenticate` and CheckWellKnown duplicate the raw-HTTP escape hatch

(Clean-context review 2026-09-18, filed rather than fixed.)

Both probes bypass `client.request()` and go at the session directly, because
they need a request the library would not make.  `CheckWWWAuthenticate` threads
the client's proxy, timeout, TLS settings and headers through to it;
`CheckWellKnown` hardcodes `timeout=10` and ignores `proxy`,
`ssl_verify_cert` and `ssl_cert` altogether, so a `/.well-known/caldav` probe
against a self-signed test server or through a proxy fails for reasons that have
nothing to do with the server's discovery support.

A `Check._raw_request(method, url, **kwargs)` in `checks_base.py` applying the
client's connection settings would serve both, and fixing well-known's
config-blindness is the part that changes a verdict on a real server — which is
why it wants a run against the fleet rather than being folded into an unrelated
commit.

# Leftovers found on Infomaniak, 2026-10-05

(Found while cleaning the Infomaniak test account, not yet investigated.)

## `Calendar.delete()` on a URL with a literal `@` deletes nothing

Infomaniak lists the url.encode-at probe calendar under a literal `@`
(`.../csc_encode_at_cal@example.com/`), twice.  `Calendar.delete()` on that
URL raised nothing and removed nothing; a raw DELETE to the `%40` spelling
answered 204 and removed it, the literal spelling 404.  So a delete that
404s on a server-handed URL is being swallowed somewhere - a caldav bug, if
it reproduces.

## The url.encode-at probe leaks its calendar

`csc_encode_at_cal@example.com` was left behind on Infomaniak, so the
probe's cleanup misses it.  Probably the item above: cleanup deletes by the
literal URL the server listed.

## Async test calendars leaked with `delete-calendar` fragile

Every `pythoncaldav-async-*` calendar was left on the server, emptied but
not deleted - the shape of `Calendar._async_delete()` with a fragile
`delete-calendar` whose retry loop never sees a `NotFoundError`, which ends
in wiping the objects instead of deleting the calendar.  A fresh async
create-and-delete worked (gone in 2.2s), so the leftovers may predate the
2026-10-05 profile change; confirm on the next Infomaniak run.  This belongs
in caldav, not here.

## Consolidation of delays

There are several kinds of delay, some handled in the caldav library and
others only in test code:

* Asynchronous writes, all under `synchronous-write` (graded
  `unsupported` with a `delay`; each child inherits from the parent):
  * `synchronous-write` itself - object writes; the tester and the
    caldav test suite sleep after every write, the library does nothing
  * `.create-calendar`, `.delete-calendar`, `.proppatch` - the library
    polls after MKCALENDAR, a calendar DELETE or a PROPPATCH until the
    change shows, for up to `delay` seconds (one shared helper)
* The Cyrus calendar delete bug - `delete-calendar` `fragile`: deleting a
  calendar re-created on a just-deleted id answers 500 for ~1s
  (https://github.com/cyrusimap/cyrus-imapd/issues/6383); the library
  retries the DELETE.
* `search-cache` - writes are synchronous, but a REPORT may take a while
  to show new content.  Uses `behaviour: delay` rather than a support
  level; arbitrary sleeps before searches in the tester and the test suite.
* `rate-limit` - client-side throttling, not a server delay.

The asynchronous writes were consolidated on 2026-10-06; the Cyrus bug,
`search-cache` and `rate-limit` stay as they are for now.

Left over: during a run the library waits as long as the tester has
*observed*, and one measurement of a varying delay is too tight a bound -
on Infomaniak later calendar creations log "did not show within 6s".
CheckCalendarProperties gives the library twice the measured PROPPATCH
delay; the same margin for all `synchronous-write` children, applied in one
place, would cover creation and deletion too.

# Lookup by UID: two missing probes

(Measured 2026-10-07 with caldav master `5d165da1`, no server hints, raw
`calendar-query` REPORTs against a calendar holding four events.)

## Is an object name other than `<uid>.ics` allowed?

`Calendar.get_object_by_uid()` only ever searches, so it does not depend on
this - but a client that builds `<cal>/<uid>.ics` from a UID, or a test that
stores under an arbitrary name, does.  `save-load.stable-url` (probed in
`PrepareCalendar`) asks whether the server keeps the URL the client stored
under; that probe stores under `<uid>.ics`, so a server that accepts only
that name passes it.  It also stores under a non-UID name now, and records
`forces_uid_ics` when the server names the object `<uid>.ics` anyway.

* Zimbra: a PUT to `some-other-name.ics` answers `302` to `<uid>.ics` (on the
  internal port 8443, so caldav following the redirect gets "connection
  refused" from the docker image) and **stores nothing**.
* Bedework 3 and 5: `201`; the object lives under the given name and
  `<uid>.ics` is 404.

SOGo and Purelymail keep the name too.  Suggested sub-feature
`save-load.stable-url.arbitrary-name`: `full` when the name is kept;
`unsupported` (with a `behaviour` naming the redirect) for Zimbra.  The
Zimbra profile's comment "Zimbra keeps the name the object was stored
under" holds only for `<uid>.ics` names, and should say so.

## `search.text.by-uid` independently of `search.text`

`get_object_by_uid()` is a text search on `UID`, yet servers differ on UID
and other text properties:

| server    | UID exact | UID substring | SUMMARY exact/substring | non-match |
|-----------|-----------|---------------|-------------------------|-----------|
| Zimbra    | all 4     | all 4         | all 4                   | all 4     |
| Bedework3 | 1 (after the ~3 s search-cache delay; 0 before) | 0 | 0 | 0 |
| Bedework5 | 1         | 0             | 0                       | 0         |
| SOGo      | 1         | 1             | all 4 (filter ignored)  | 0 on UID, all 4 on SUMMARY |
| Purelymail | 1 (after the ~180 s index delay; 0 before) | 1 | 1 | 0 |

Robur could not be measured on 2026-10-07: every PUT answered 500.

* Zimbra ignores every `text-match`; its `search.text: unsupported` hint is
  right.  `get_object_by_uid()` still works only because the library
  post-filters (`o.id == uid`) - i.e. it downloads the whole calendar per
  lookup.
* SOGo's profile sets no `search.text`, only `search.text.case-sensitive`
  and `search.text.case-insensitive` as unsupported.  Measured, text search
  works on UID, substring included, but a SUMMARY filter is ignored.
* Purelymail does full text search once its index catches up; the old
  claim in caldav's `search.py` that it lacks text search mistook the
  index delay for missing support (corrected in caldav 3.4.0).  An empty
  `_hacks="insist"` result is retried without the text filters, but only
  with a feature profile: with `features="purelymail"` (its `search-cache`
  delay) `get_object_by_uid()` works right after a save, without features
  it raises `NotFoundError` until the index catches up.
* Bedework matches an exact UID but nothing else (no substring, no SUMMARY),
  so `search.text: unsupported` hides that UID lookup works server-side.  It
  costs: with that hint, caldav's `CalDAVSearcher` drops *all* text filters,
  UID included, and every UID lookup fetches the whole calendar.
  Without hints, `get_object_by_uid()` on Bedework 3 right after a save raises
  `NotFoundError` - the search-cache delay, not text search.

Probe `search.text.by-uid` (exact match) on its own, and let the library
consult it rather than `search.text` when deciding whether a UID search can be
trusted to the server.  Its substring behaviour belongs under
`search.text.substring`, which is `unsupported` on Bedework (no substring
match anywhere; the missing SUMMARY match is `search.text`).

# `sync-token.limit`: a cut without a 507 is only seen at exactly `limit` members

`CheckSyncTokenLimit._follow` notes "the reply is cut at DAV:limit without a
507" only when the first page holds exactly `limit` members once the
collection's own entry is dropped.  Two shapes slip past it:

* a server that lists the collection among its members and counts that entry
  toward the limit, then cuts without a 507 (`limit - 1` members);
* a server whose own cap is below the requested limit and that cuts without a
  507.

If nothing is lost on such a server, it is graded `unsupported` ("DAV:limit is
ignored") rather than `fragile`.  No server has been seen doing either.  Fix:
pass `len(expected)` from `_grade` into `_follow` (the changes for the token
run, the live members for the initial sync), and treat a first page without a
507 as cut when it holds fewer members than that.  This also covers today's
`== limit` test.  Found in review, 2026-10-14.

# DAViCal ignores `<C:expand>` when `schedule-tag` is requested

A calendar-query asking for `<C:calendar-data><C:expand .../></C:calendar-data>`
plus `<D:getetag/><C:schedule-tag/>` gets the unexpanded master (with its
RRULE) back from DAViCal, and a 404 propstat for `schedule-tag`.  Without
`schedule-tag` the same query is expanded.  Seen 2026-10-11 against the
DAViCal docker server (port 8805), with a plain yearly all-day event.

The caldav library works around it in
https://github.com/python-caldav/caldav/pull/751 (commit 45e7bee5) by
leaving `schedule-tag` out of every REPORT that carries `<C:expand>`, on
every server.

To do: a check that sends that REPORT with and without `schedule-tag` and
compares the results, recorded as a feature (say
`search.recurrences.expanded.with-schedule-tag`).  Optionally, have the
library then request `schedule-tag` in expanding searches on servers where
the feature is supported.

## Two Zimbra oddities seen through plann

Found 2026-10-19 while trying a Zimbra task list with plann (caldav 3.5.0,
a production Zimbra, not the podman one):

* **Category search matches substrings.**  `plann select --todo --category
  R` returned a task with category `RL`, and `--category do` matched `doc`.
  Zimbra ignores `text-match`, so this is the library's (or plann's)
  client-side post-filter.  RFC 4791 text-match is a substring match by
  default, but a category is a list of whole values; on xandikos
  `--category RL` does not match `rl_tobias`.  Find out which layer does the
  substring match, probe `search.text.category` exact versus substring
  on the servers, and make the post-filter agree with what the servers do.
* **A delete selecting 65 tasks by `--uid` deleted nothing**, silently:
  `plann --config-section work-tasks select --todo --include-completed
  --uid U1 ... --uid U65 delete` printed no `Deleting` line.  The same
  UIDs deleted one at a time worked, and a two-`--uid` select works on
  xandikos.  Reproduce: is it the number of UIDs, Zimbra's UID search (it
  ignores text-match, so each lookup fetches the whole list), or plann's
  handling of several UIDs?

# Expired sync tokens: worth a probe at all?

(Noted 2026-10-24, from calendaring-sync's evidence branch.  Undecided.)

RFC 6578 section 3.2 has a server refuse a token whose change history it
discarded, with `DAV:valid-sync-token`.  `sync-token.invalid-token` only
covers tokens the server never issued; a token that was valid and expired is
not probed, and a client that keeps tokens for days is the one that meets it.

Stalwart can be made to expire tokens: calendaring-sync shrank
`DataRetention.maxChangesHistory` to 1 and then ran an `AccountMaintenance`
purge (https://github.com/PyCalendar/calendaring-sync/tree/evidence, under
`sync-design/servers/stalwart_admin.py`).

Against it:

* The purge is an admin JMAP call.  The checker is a client; it would need a
  server-side hook, i.e. a Stalwart special case, and the checker shouldn't
  grow many of those.
* A low `maxChangesHistory` in caldav's shared Stalwart container expires
  tokens under caldav's own sync tests whenever a purge runs.

If it is done, the generic shape is a hook in the test-server definition
("expire the change history now"), with the probe `unknown` wherever no hook
is defined; Stalwart would be the only server with one.

# Bedework refuses TZIDs that are tzdata link names

Bedework answers 403 `unknownTimezone` for a TZID that is a tzdata link name
(Europe/Oslo, Europe/Stockholm, Europe/Amsterdam, Europe/Kiev), even with a
matching VTIMEZONE in the object.  Canonical names (Europe/Berlin,
Europe/Paris, America/New_York) are accepted.  Seen 2026-10-26 on the
Bedework3 docker server.

No probe or hint covers it.  The `save-load.icalendar.vtimezone` probe
already sidesteps it by using Europe/Berlin, which is deliberate; only
Bedework 3 was tried, Bedework 5 was not.  A probe would save an event with a
link-name TZID (with and without VTIMEZONE) next to a canonical-name control,
and grade the refusal `ungraceful`; it belongs with that probe
(https://github.com/python-caldav/caldav-server-tester/issues/39).

# `delete.if-match: broken` has no consumer

The hint is set for Zimbra, SOGo, Bedework 3 and 5, and Posteo, but nothing
acts on it: caldav's `delete(if_match=)` does not read it, and neither, as far
as known, does https://github.com/PyCalendar/calendaring.  `broken` means the
server deletes whatever the etag says, so skipping the header would change
nothing.  Decide whether `delete(if_match=True)` should warn or raise when the
hint says `broken`, or emulate the check with a GET and an etag compare
before the DELETE (racy, but closer to what was asked for).
