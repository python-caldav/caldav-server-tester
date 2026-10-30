# Changelog

## Meta

This file should adhere to [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), but it's manually maintained.  Feel free to comment or make a pull request if something breaks for you.

This project should adhere to [Semantic Versioning](https://semver.org/spec/v2.0.0.html), though some earlier releases may be incompatible with the SemVer standard.

This library is tightly dependent on the CalDAV-library, particularly the `compatibility_hints.py`-file.  This file is not (yet) considered to be part of the "core" business logic in the CalDAV library and can be changed in patch-releases in the CalDAV library - so this library is usually released in lock-steps with the CalDAV-library.  I've considered to bump the version number to be follow the CalDAV version number.

## Unreleased

Needs caldav 3.7.0, which has the features below in its `compatibility_hints.py`, `forces_uid_ics` on `save-load.stable-url`, and repeats a redirected PUT, which Zimbra needs to be graded.

### Changed

- **`save.no-uid-conflict`** is graded by a plain PUT.  403 or 409 with `CALDAV:no-uid-conflict` (or Cyrus' `CALDAV:unique-scheduling-object-resource`) is `full`; another status with it (Xandikos, Stalwart: 412) is `quirk`.  A refusal without it is `quirk` too, with the extra keys `status` and `precondition`, if nothing was stored at the URL and the same PUT with another UID goes through (SOGo: 409).  A bare 403 stays `ungraceful`.

### Added

- **`supported-components.property-exists`** (the former `get-supported-components`, now probed), **`supported-components.honoured`** and **`create-calendar.with-supported-component-types.multiple`** - whether a calendar reports the component set, whether it takes exactly the types it lists (and every type where it lists none, RFC 4791 §5.2.3), and whether a MKCALENDAR naming several types gives a calendar taking all of them.  A calendar that reports `[VTODO]` and still takes a VEVENT is `broken` on `.honoured` (Bedework 3.10, DAViCal, Stalwart); CCS refuses a set of several components the §5.3.1.1 way and is `unsupported` on `.multiple`.
- **`delete.if-match`** - whether a DELETE with an If-Match etag the object no longer has is refused with 412, and one with the current etag goes through.  SOGo refuses a stale If-Match on PUT, but deletes the object on DELETE.
- **`save-load.event.floating`** - whether an event with a floating DTSTART and DTEND is stored floating.  SOGo gives the times TZID=Etc/UTC (tasks it keeps floating).  The probe reads the server's own text: the icalendar library writes a TZID=Etc/UTC time back with a `Z` after it, which RFC 5545 §3.3.5 forbids.
- **`save-load.icalendar.attendee-params`** - whether ATTENDEE parameters are stored as sent.  An added RFC 5545 default (PARTSTAT=NEEDS-ACTION and the like) or SCHEDULE-STATUS is fine; SOGo also adds RSVP=TRUE, where the default is FALSE.
- **`save-load.icalendar.participant`** - whether an RFC 9073 PARTICIPANT inside a VEVENT is stored.  CCS and Bedework 3 refuse the event with 403, robur with 400, GMX and Ox with 500.  See https://github.com/python-caldav/caldav-server-tester/issues/38.
- **`save-load.icalendar.vtimezone`** - whether the VTIMEZONE sent with an event comes back with it.  CCS returns the event's TZID without the VTIMEZONE.  See https://github.com/python-caldav/caldav-server-tester/issues/39.
- **`save-load.icalendar.x-property`**, **`save-load.icalendar.unknown-property`**, **`save-load.todo.attendee`** and **`save-load.todo.completed-without-status`** - whether an X- property (in an event and in a task), a property RFC 5545 does not define (ESTIMATED-DURATION), an ATTENDEE on a task, and COMPLETED on a task without STATUS are kept.  Ox drops all of them (the X- property only from a task); a task with an ATTENDEE is answered 201, then 404.  See https://github.com/python-caldav/caldav-server-tester/issues/40.
- **`delete.missing`** and **`delete.missing.if-match`** - whether a DELETE of an object that does not exist answers 404, and one with If-Match answers as the plain one does, as RFC 9110 §13.2.1 has it.  Baïkal, Davis, Nextcloud, CCS, OX, Robur, Purelymail and Infomaniak answer 412 with If-Match, so a delete of an object someone else already deleted reads as an etag mismatch.
- **`save.etag.matches-stored`** - whether a strong ETag in a PUT answer belongs to what was stored: RFC 4791 §5.3.4 lets a server rewrite the object, but not answer with a strong ETag then.  The behaviour says what the server changed.
- **`sync-token.initial-sync`**, **`sync-token.delete.created-and-deleted`** and **`sync-token.recreated`** - which members a sync-collection report lists: an initial sync with deleted members or the collection itself in it, a member created and deleted between two reports left out, and a re-created member reported twice, or only as removed (Nextcloud, Synology).
- **`multiget.missing-href`** - whether a calendar-multiget answers a missing href with a 404 entry; Baïkal, Nextcloud, DAViCal, Davis and Infomaniak leave it out, Posteo fails the whole multiget with a 404, and an entry claiming the object exists is broken.
- **`multiget.limit`**, **`propfind.limit`** and **`search.limit`** - whether a calendar-multiget, a Depth: 1 PROPFIND or a calendar-query answers every member, however many.  Stalwart stops at its `maxResults` with a 507.  These store as many objects as `CALDAV_SERVER_TESTER_LIMIT_OBJECTS` says, and are not probed without it.
- **`save.no-uid-conflict.if-none-match`** - what `save.no-uid-conflict` asks, for a PUT carrying `If-None-Match: *`, as `save(no_overwrite=True)` sends it.  Bedework answers it with a bare 412, but a plain PUT with 403 and the precondition.
- **`save.no-uid-conflict.href-given`** - whether the precondition names the object holding the UID, as RFC 4791 section 5.3.2.1 says it SHOULD.
- **`search.text.uid`** - whether a calendar-query with a UID filter answers the one object with that UID.  iCloud answers it with 412 (`ungraceful`), so `get_object_by_uid()` and what is built on it fail there.  See https://github.com/python-caldav/caldav/issues/783.

The DELETE, ETag, sync-token, multiget and limit behaviours came from calendaring-sync's server compatibility notes: https://github.com/PyCalendar/calendaring-sync/blob/c40782bc7afe7b350fe63bf27ef13374c280ea8b/docs/reference/server-compatibility.rst

### Fixed

- **`create-calendar.with-supported-component-types`** - back to asking whether a MKCALENDAR naming `VTODO` gives a calendar with that set.  Bedework 5 and SOGo report another set and are `unsupported`; a calendar that reports the set and ignores it is a matter for `.honoured`.
- **`save-load.stable-url`** was probed by storing `<uid>.ics`, which a server that names every object `<uid>.ics` passes.  An object is now also stored under another name.  A server naming it differently is `unsupported`, and `forces_uid_ics: True` is set when the name is `<uid>.ics`, so `get_object_by_uid()` can skip the full scan when a GET of `<uid>.ics` misses.  Zimbra redirects such a PUT to `<uid>.ics`; the name is read from the Location when the client did not follow it, as with the docker image, which redirects to an internal port.  A refused PUT leaves the verdict as before.
- **`calendar-color.name`** on OX App Suite was graded `full`.  OX answers a colour name with a colour the property showed earlier, then with its default, depending on the writes in between, and the probe took the first answer for a normalised colour.  Two answers that differ, both colours the property showed before, are now `fragile`.
- A test calendar left by an earlier run is no longer reused when it is a ghost.  Zimbra 10 relocates a calendar created with a display name, and for a while after the run deletes it the old address still answers as an empty calendar that refuses every PUT with 500, so the next run failed from the start.  A leftover the server does not list, and that refuses a test write, is now created anew; this covers the task and journal calendars too.  A calendar that accepts writes is always reused.

## 1.7.0 - 2026-10-23

This release works with caldav 3.6.0.  1.6.0 was accidentally released without two probes and a grading fix that caldav 3.6.0's server hints were based on.

### Added

- **`save-load.todo.uncomplete`** - whether a completed task can be made pending again: `Todo.uncomplete()` saves it with STATUS:NEEDS-ACTION and without COMPLETED, and the probe loads it back.  GMX keeps it completed.  See https://github.com/python-caldav/caldav/issues/585.
- **`search.time-range.todo.no-dtstart.no-due`** - whether a date search finds a task with neither DTSTART nor DUE, which RFC 4791 §9.9 has overlap every range.  Zimbra, DAViCal and Stalwart do not find one; Zimbra does find a task with DUE only.  See https://github.com/python-caldav/caldav/issues/585.

### Fixed

- **`save-load.event.recurrences.exception`** - only what the server answered is graded.  A refused PUT is still `ungraceful`, now with its status; a PUT without an answer (a dropped connection, a gateway error), a failed listing of the calendar, or the event missing from it are `unknown`, with the reason.  A failed listing used to be graded `ungraceful` and a missing event `unsupported`, so Robur was reported `ungraceful` while storing the event correctly.

## 1.6.0 - 2026-10-23

This release works with caldav 3.6.0, but lacks two probes and a grading fix that caldav 3.6.0's server hints rely on; see 1.7.0.

### Added

- **`save-load.event.recurrences.exception.cancelled`** - whether an exception VEVENT (with RECURRENCE-ID) marked `STATUS:CANCELLED` is stored as such.  caldav's `cancel()` of a single occurrence makes one; Zimbra turns it into an EXDATE on the master instead.
- **`save.if-none-match`** - whether a PUT with `If-None-Match: *` (RFC 9110 §13.1.2) refuses to overwrite an existing object with a 412 and creates a missing one.  `unsupported` where it overwrites (Zimbra compares `*` as a literal etag, SOGo ignores the header), `broken` where it refuses to create.  The `save-load.mutable.if-match-wildcard` probe tried it already, but only noted the outcome in its behaviour text, which it no longer does.
- **`save.no-uid-conflict`** - whether a PUT of an object whose UID another object in the calendar holds is refused (RFC 4791 §5.3.2.1, `CALDAV:no-uid-conflict`).  A refusal without a precondition the caldav library recognises (SOGo's bare 409, Bedework's bare 412, Purelymail's `no-uid-conflict` in the CardDAV namespace) is `ungraceful`.  The caldav library uses this and `save.if-none-match` to skip the UID search on `save(no_overwrite=True)`.

### Changed

- **`calendar-color`** - probed with a hex value (`#FF0000FF`), the format clients use in practice, so a server that takes hex but not colour names is `full` (Zimbra, SOGo, iCloud, OX App Suite).  Colour names are probed as the new `calendar-color.name`, which replaces `calendar-color.hex`.  A server that ignores names, so that its colour stays unchanged, is now `unsupported` there; OX App Suite and iCloud were graded `broken` (read-only) for it before.
- Requires caldav 3.6.0.

## 1.5.0 - 2026-10-16

This release works with caldav 3.5.0.

### Added

- **`sync-token.invalid-token`** - whether a sync token the server never issued is refused with `DAV:valid-sync-token`, as RFC 6578 §3.2 says.  Xandikos 0.4.8 and CCS answer some such tokens with a 500 (https://github.com/jelmer/xandikos/issues/743), and Stalwart and OX with a report and no error.
- **`sync-token.limit`, `sync-token.limit.initial-sync`** - whether a sync-collection report honours DAV:limit (RFC 6578 §3.7), and whether following the checkpoint tokens of the truncated reply reports every change, from an issued token and from an empty one.  Stalwart's checkpoint of a truncated initial sync skips the remaining members, and Stalwart truncates on its own at its maxResults setting (default 2000).  Xandikos and Nextcloud cut the reply without a 507; CCS refuses DAV:limit with a 507, as §3.7 allows.  See https://github.com/pycalendar/calendaring-sync/pull/19.
- **`propfind.principal-depth-1`** - whether a Depth: 1 PROPFIND on the principal URL returns a multistatus.  RFC 3744 does not require a principal to be a collection; GMX answers with a 400.  The caldav library lists the principal at depth 1 when a server gives no `calendar-home-set`.  See https://github.com/python-caldav/caldav/pull/755.
- **`save-load.event.no-dtend`** - whether a VEVENT with neither DTEND nor DURATION is stored.  RFC 5545 §3.6.1 allows one; iCloud refuses it with a 404, or a 403.  See https://github.com/python-caldav/caldav-server-tester/issues/24.

### Changed

- **`save.etag`** - the probe now also GETs the object.  A weak ETag on PUT (which `If-Match` can never match) where a GET gives a strong one that `If-Match` accepts is graded `quirk` with behaviour `weak on PUT, strong on GET` (seen on GMX); it used to pass as `full` with only a note.  A GET ETag differing from a strong PUT ETag is noted.
- Requires caldav 3.5.0.

### Fixed

- **`--diff` and the non-verbose report** - observations (e.g. `get-current-user-principal.has-calendar`) and client-features (e.g. `rate-limit`) carry a value rather than a support level, and were listed as spurious deviations.  `--diff` now skips them like caldav's `FeatureSet.compare()` does, and the report compares their resolved value against the default.
- **`search-cache` delay** - a server with a search-cache delay (Purelymail: 180 s) waited that long before every search, so a run could take hours.  Now a search waits only for what is left of the delay since the last write, and where the server profile sets `filtered-only` (Purelymail), only searches with a time range or a property filter wait at all.  The text-search probes now honour the delay too.
- **`PrepareCalendar`** - stopped at the first VEVENT without an end that the server refused (iCloud), so the fixtures after it were missing and e.g. `search.recurrences.*` and `search.time-range.event.old-dates` came out wrong.  A refused all-day fixture now gets the one-day DTEND RFC 5545 implies, and the timed one is skipped.
- **`calendar-color`, `calendar-order`** - on a server whose PROPPATCH takes a while to show (Infomaniak), the probes waited only twice the delay measured in the same run, so a fast sample made the properties look read-only (`broken`).  They now wait at least the configured `synchronous-write.proppatch` delay.
- **`save-load.event.no-summary`** - a 401 is graded `unknown` rather than `ungraceful`: on a session that is already authenticated it is no answer to the request, and iCloud answers 401 to anything for a while after a run of 429s.

## 1.4.0 - 2026-10-08

This release works with caldav 3.4.0.

### Added

- **`scheduling.calendar-user-address-set.populated`** - set if the principal's `calendar-user-address-set` actually carries an address.  Xandikos 0.4.7 leaves it blank.

- **`auth.www-authenticate`, `auth.www-authenticate.usable-scheme`** - whether a 401 from the server names an authentication scheme, as RFC 7235 §3.1 requires, and whether the schemes it names include one the caldav library implements.  Yahoo Calendar skips this header; a server naming only Negotiate or NTLM is the same dead end one step later, and pinning `auth_type` is the cure for both.  See https://github.com/python-caldav/caldav/issues/713.  Note that a run only reaches this probe on a server it has already authenticated against, so on an affected server it records *why* `auth_type` had to be pinned rather than diagnosing a connection that never came up.

- **`synchronous-write.proppatch`** - how long a PROPPATCH takes to show in a PROPFIND.  Infomaniak answers it at once but keeps serving the old display name or colour for 7-11s, while a PUT is readable immediately.  Needs a caldav library that knows the feature, which then waits for such a server in `set_properties()`.

### Changed

- Requires caldav 3.4, for the `synchronous-write.proppatch`, `.create-calendar` and `.delete-calendar` features.
- **`synchronous-write`, `synchronous-write.create-calendar`, `synchronous-write.delete-calendar`** - a server that creates or deletes calendars asynchronously but answers object writes synchronously (verified: Infomaniak) is now graded `full` on `synchronous-write`, not `unsupported`.  The calendar delays are reported on the two children, as `unsupported` with the slowest measured `delay`, rather than as a `quirk` on `create-calendar` or `delete-calendar` - those two now only say whether the request goes through, so a profile generated by an older version needs its delays moved.  A measured delay is checked against the `delay` the server profile declares on the same child, and the caldav library waits for it from 3.4 on.

### Fixed

- **`calendar-color`, `calendar-order`** - falsely graded `broken` (read-only) on a server applying PROPPATCH asynchronously (verified: Infomaniak): the value was read back before the server showed it.
- **`search.time-range.event.old-dates`, `search.unlimited-time-range`, `search.is-not-defined*`, `search.time-range.alarm`** - falsely reported as failing on a server that ignores the comp-filter (verified: Infomaniak) whenever the test calendar survived an earlier run: the year-2000 probe event was deleted as a stale fixture.

## 1.3.0 - 2026-09-16

This release works with caldav 3.3.1.

Most entries below name the feature key they concern.  A key listed under
**Changed** or **Fixed** may report differently than it did in v1.2.0 on the very
same server, so a re-run with `--diff` against your stored profile is worthwhile.

### Added

New probes, by the feature key they fill in:

- **`url.encode-at.literal.object` / `.collection` / `.principal`, `url.encode-at.encoded`, `url.encode-at.identity`** - whether a path may carry a literal `@`, whether the `%40` spelling resolves, and whether the two spellings are one resource or two (RFC 3986 §2.2 says two; every server probed so far says one).  Asked separately for an object name, a calendar id and the principal path, since a server may route them differently - Stalwart serves an *object* only under `%40` while serving a *calendar* under either spelling.
- **`save.etag`, `save-load.mutable.if-match-wildcard`** - the `ETag` a PUT answers with, replayed in `If-Match`, plus `If-Match: *` and `If-None-Match: *` against an existing and a missing object.  Bedework 5 answers with a percent-encoded etag and refuses that form back; Zimbra, SOGo, Radicale and Bedework each get `If-Match: *` wrong in a different way.
- **`synchronous-write`** (the caldav library's former `write-delay` peculiarity) - how long a write takes to become readable is now measured rather than only read from the profile.  A delay observed on a server configured without one, or one that has outgrown the configured value, is reported.  The configured delay is also honoured during a run: every write on the main connection and on any scheduling account is followed by it, so read-backs see settled data on a server that processes writes asynchronously (verified: Infomaniak).
- **`create-calendar.with-supported-component-types`** - whether a calendar created with a component set really is restricted.  Bedework 5 advertises the restriction and ignores it. It also does not support tasks on a regular calendar, so no calendar a client creates there can hold a task.
- **`save-load.event.no-summary`** - an event without `SUMMARY`, which RFC 5545 §3.6.1 permits and Bedework 5 refuses with `500 missingeventproperty`.
- **`well-known`** - RFC 6764 §5 discovery.  The redirect target is asked for `OPTIONS` and must advertise `calendar-access`, so a login page answering `200` is not mistaken for support.
- **`search.time-range.todo.no-dtstart`** - a `VTODO` with `DUE` but no `DTSTART` in a closed date-range search (Davical, Stalwart and Synology skip it).  Replaces the `vtodo_datesearch_nodtstart_task_is_skipped` flag.
- **`non-existing-raises-not-found.object` / `.collection`** - what a lookup of a missing *object* and of a missing *calendar* actually raises.  The two differ: the library rescues a failed GET with a multiget REPORT, so Robur - which answers 403 for everything that does not exist - still raises `NotFoundError` for an object while a missing calendar surfaces as `AuthorizationError`.
- **`propfind`, `propfind.allprop`, `propfind.allprop.resourcetype`, `propfind.displayname`** - PROPFIND for a named property, for `DAV:allprop`, and whether `DAV:resourcetype` is among what allprop returns (Bedework omits it).  Replaces the `propfind_allprop_failure` flag.
- **`calendar-color`, `calendar-color.hex`, `calendar-order`** - the nonstandard Apple/Mozilla collection properties, probed as a set/get round-trip with both a colour name and a hex value, and told apart from a read-only property.  Whatever was there before is put back, so the probe does not repaint a calendar of yours.  Replaces the `calendar_color` and `calendar_order` flags.
- **`save.duplicate-event`** - two events with identical content but different UIDs in one calendar.  Replaces `duplicates_not_allowed`.
- **`save-load.mutable.attendee-partstat`** - whether a client may change an attendee's `PARTSTAT` with a plain PUT.  OX App Suite answers 403 even with a matching etag and expects scheduling to be used instead.
- **`save-load.event.recurrences.exception.reschedule`** - re-anchoring a series that carries a detached exception, with a matching `If-Match`, and checking that the move survived: a server that accepts the PUT and silently drops it is the data-loss case and is `unsupported`, while OX's `409 Conflict` is `ungraceful`.
- **`save-load.stable-url`, `create-calendar.stable-url`** - whether an object, and the calendar holding it, stay at the URL a client would construct from the UID and the `cal_id`.  OX App Suite relocates both; Zimbra relocates a calendar created with a display name.
- **`search.comp-type`** - a comp-type-specific query returns only objects of that type.  A server that ignores the comp-filter is `broken`.
- **`search.text.comp-type-optional`, `search.time-range.comp-type-optional`** - split out of `search.comp-type.optional`, which conflated three different questions about a query that names no component type.

New probes on keys that already existed:

- **`create-calendar`** - the `empty-207` quirk: a successful `MKCALENDAR` answered with a `207` whose single `DAV:response` carries neither a status nor a propstat (Bedework 5), which only the raw response shows.
- **`delete-calendar`** - deleting a calendar that was re-created on a just-deleted id, which is what a test suite or a user re-adding a calendar does.  Cyrus answers `500` while the previous delete is still settling.
- **`search.recurrences.expanded.event`** - an expanded search answered with one `DAV:response` per instance, all under the same href (Bedework 5), which RFC 4918 §14.24 forbids.  Reported as a quirk.
- **`search.recurrences.expanded.exception`** - the same event with `SEQUENCE` on master and override, as real clients write it.  Stalwart suppresses the overridden occurrence only when `SEQUENCE` is absent, which is reported `fragile`.

Tool behaviour:

- New `--cleanup-only` flag: remove all `csc_*` test data (including the year-2000 probes a sliding window hides) and exit.
- Reusable fixtures now live in the **next calendar year** rather than year 2000, so servers that hide anything outside a window around today (verified: OX App Suite) can still see them.  Two probe objects stay in 2000 to detect that window.
- `--caldav-features` is honoured on the `--name` registry path, and extra `--config-section` accounts on the `--caldav-url` path, so the multi-user scheduling probes no longer silently degrade.

### Changed

- **`delete-calendar`** - a deletion that merely takes a while is now a `quirk` carrying the measured `delay`, not `fragile`; `fragile` is reserved for a deletion that fails and succeeds on a retry (Cyrus).  This matters beyond wording: every gate that asks "can we keep probing?" now asks whether the operation *takes effect*, so a server graded honestly no longer switches off the probes below it.
- **`create-calendar`, `delete-calendar`** - probed with any configured write delay **suspended**, so the workaround no longer hides the quirks the probe exists to find, and the wait for a deletion to take effect scales with the configured delay, replacing a flat 10s sleep.  A calendar left behind by a previous run is read as evidence that a creation which timed out did take effect, rather than as "creation does not work".
- **`create-calendar`** - a `MKCALENDAR` that fails in a way that could plausibly go the other way (5xx, 408, 429) is retried before the answer is believed, and a creation that fails and then succeeds is `fragile`.
- **`delete-calendar`** - a DELETE that raises is checked before it is retried, since a server may report an error and carry the deletion out anyway; where it did, the verdict is `ungraceful`.
- **`search.unlimited-time-range`** - distinguishes a classic sliding window (year-2000 objects hidden) from a window so tight that even next-year fixtures are hidden.

### Fixed

Probes that reported a verdict they had not observed:

- **`search.comp-type.optional`** compared one search against a counter of every object created across all calendars, which it could never reach on a server keeping tasks or journals apart - mislabelling Baikal, Davis, CCS, OX, Zimbra, Bedework and more.  See https://github.com/python-caldav/caldav/issues/681
- **`delete-calendar.free-namespace`** was decided by whether a *previous* run's leftover could be re-created, so a first run had no evidence at all and any transient failure was recorded as "the namespace is not freed".
- **`search.recurrences.expanded.todo`** searched the event calendar, so every server that keeps tasks in a separate collection was reported `unsupported` for a feature it has.  Predates v1.2.0.
- **`search.recurrences.*`** - a query the server *rejects* is `ungraceful`, not `unsupported`; the latter claims the server silently ignored the request, which is the case a client cannot detect.  Three places got this wrong, including one that undid the far-future grading on any server refusing the base-year range.
- **`search.recurrences.*.todo`** are `unknown` where the todo time-range precondition could not be established, rather than `unsupported`.
- **`sync-token.delete`** - a REPORT that fails after a delete (Infomaniak answers `418`) is `ungraceful`; and the other branch measured nothing, recording full support whenever the call did not raise.  A server that answers with an empty multistatus has dropped the deletion silently and is now `unsupported`.
- **`get-current-user-principal`** - a transient 503, TLS or connection error during the re-fetch is `unknown` with a warning, and the principal obtained at connection time is kept, instead of being reported `unsupported` and nulled out.
- **`get-current-user-principal`** - a principal URL the server hands out but then answers 404 for (verified: Infomaniak's sabre/dav, violating RFC 5397) is reported rather than left to raise an unhandled traceback out of the next check that asks for the calendar-home-set.
- **`create-calendar.set-displayname`** - verified by looking the calendar up by `cal_id` and reading its display name, rather than by looking it up by display name, which a leftover calendar of the same name could shadow.
- **`create-calendar`** - a server that creates calendars asynchronously, answering `MKCALENDAR` before the collection can be queried (verified: Infomaniak), was reported as unable to create calendars.  The probe now polls for the new calendar and records a `quirk`, "delayed creation".
- **`save-load.todo`** and its children - a server that can store a `VTODO` nowhere at all (Bedework 5) no longer aborts the run before a single event or journal fixture has been written.  The report held three features instead of ninety.

Tool behaviour:

- **Data-loss protection when `--caldav-calendar` names a real calendar.**  Cleanup deleted the whole calendar whenever the server supported MKCALENDAR/DELETE, even one the user named, and the stale-fixture sweep deleted every leftover object it found rather than only the tool's own `csc_*` fixtures.
- **Probe calendars no longer accumulate.**  A crash, an exception or an asynchronous server-side DELETE could leave throwaway calendars behind, which matters on servers that cap calendars per principal.  Cleanup now ends with a sweep of the tool's own probe namespaces; calendars the user pointed the tool at are spared.
- The run no longer discards the report or skips cleanup when a check raises: cleanup and reporting are in a `finally`, shared by the `--caldav-url`, `--name` and config-file paths.
- `--format hints` and the verbose text report no longer get the data that compact rendering had already collapsed, which dropped per-feature `behaviour` notes.
- The `weeklymeeting` fixture is renamed `csc_weeklymeeting`, so the `csc_*` cleanup fallback no longer leaves it in the user's calendar permanently.

### Documentation

- New [`docs/ROADMAP.md`](docs/ROADMAP.md): what the tool covers today (68 of 76 probeable features, 36 check classes), what the funded period buys measured from v1.2.0 with the progress made in this release ticked off, and what is wanted but unfunded.
- `CONTRIBUTING.md` rewritten for AI-assisted contributions.

### AI-disclaimer

As for the previous releases: this release is predominantly coded with
AI-assistance, and this changelog entry is AI-generated and then reviewed by
hand.  The level of scrutiny is lower than in the caldav library - though the
data-loss protection above was written precisely because the tool is now
expected to survive being pointed at a real calendar.

## 1.2.0 - 2026-04-24

This release works with caldav 3.2.1.

### Added
- New `CheckMutable` check: verifies that the server allows modification of existing calendar objects (`save-load.mutable`). Replaces the old `no_overwrite` compatibility flag.  The problem with immutable events have been observed on Google many years ago - since I don't know any servers with this behaviour (possibly Google, but so far I haven't run this script against Google), the check hasn't really been tested properly.
- RFC 4791 §9.6.5 states that the first occurrence in a server-expanded recurrence set may omit the `RECURRENCE-ID`.  We consider `search.recurrences.expanded.event` to be "supported" in this case, with a text-note in the behaviour-field.  If I remember correct, Cyrus did omit this, but the behaviour got changed shortly after I did this check.

### Fixed
- `--diff` reported expected support as `unknown` for features not explicitly listed in the server profile instead of using the default support level (typically `full`).
- `CheckMakeDeleteCalendar` now tolerates HTTP 500 responses when probing whether a calendar exists (triggered by a server bug; see calendar-cli#114).
- `delete-calendar` and `delete-calendar.free-namespace` are now marked `unknown` instead of causing an assertion failure when `create-calendar` is unsupported (e.g. Posteo).
- Fixed `RECURRENCE_ID` → `RECURRENCE-ID` (underscore vs hyphen) in the expanded exception check; the wrong form caused `component.get()` to always return `None`, making `search.recurrences.expanded.exception` silently always fail.
- Scheduling probe events now use unique UIDs to avoid false `unsupported` results on re-runs against servers (e.g. Zimbra) that remember previously scheduled event UIDs.

### AI-disclaimer

The notes given for release 1.1.0 applies to 1.2.0 as well.  CHANGELOG-entry was AI-generated and then partly rewritten by hand.

## 1.1.0 - 2026-04-24

This release works with caldav 3.2.0.

### Added

* Lots of new test probing the scheduling features.  Those requires multiple user accounts on the server.  This can now be configured.
* Lots of new tests probing edge-cases wrg of date searching, open-ended searches, etc

### AI-disclaimer

This release has been predominantly coded with AI-assistance.  The level of scrutiny done on this tool is a bit less than the level of scrutiny done on the caldav libary.  Then again, I don't expect you to run this checker directly towards some calendars that are also used in production.

## 1.0.1 - 2026-03-19

### Fixed
- `--name radicale` (and other lowercase names) failed to find servers in the caldav test registry after the caldav library renamed its server entries to capitalised names (`Radicale`, `Xandikos`).  The registry lookup is now case-insensitive.
- `--name` registry lookup silently returned nothing when the caldav-server-tester's own `tests/` package shadowed the caldav project's `tests/test_servers` in `sys.modules` or via the CWD entry in `sys.path`.  The registry is now loaded via `importlib` using the explicit file path, bypassing `sys.path` resolution.

### Fixed

* `CheckSearch` now sets `search.combined-is-logical-and` to `None` when category search is unsupported, preventing a spurious `AssertionError` in the post-check consistency validation.
* `run_check` now raises `AssertionError` (instead of silently logging) when a check class fails to set all declared `features_to_be_checked` or sets undeclared features.
* Removed `tests/test_compat_xandikos.py` (redundant with the integration tests in the caldav project's `testCheckCompatibility`).

### Documentation
*  Updated USAGE.md
  * `--format text` section to reflect current multi-line output format and actual support-level values; added `unknown` status
  * added guide for contributing a new server profile to `caldav/compatibility_hints.py`
  * added guide for storing checker results in `~/.config/caldav/calendar.conf` (named profile, inline features, and base+overrides patterns)

## 1.0.0 - 2026-03-15

Considering this tool as "production ready" now - even though it's still lots of corner cases to be tested.

This release corresponds to version 3.0.2 of the caldav library.  It's important to keep those two libraries in sync as the "feature list" is contained in the caldav library.

### Changed
- Minimum required `caldav` library version bumped to 3.0.2.
- Text report now labels extra check information with "Extra check information:" header (rationale: it was a bit confusing with two "descriptions" on one feature).

### Documentation, tests, CI etc
- Added `CONTRIBUTING.md` with contribution guidelines
- Conventional commit message enforcement via `conventional-pre-commit` pre-commit hook
- Link checker CI workflow
- Development status classifier updated to Production/Stable


## 0.2.2 - 2026-03-11

Lots of changes have been done since v0.1.0.  I'm not sure the changelog is complete, I didn't get time to do a proper QA on it.  CalDAV version 3.0 is required.

This was sort of a pre-release of v1.0.0.

(Version 0.2.0/0.2.1 was never published due to problems with the auto-publish workflow)

### Added
- `--config-section` CLI option: select a named section from the caldav config file (passed through to `get_davclient`)
- `--name` now falls back to the caldav config file when the name is not found in the test server registry (instead of raising an error)
- Text report now shows the feature description (from `compatibility_hints.py`) below each feature line
- YAML output format (`--format yaml`)
- Hints output format (`--format hints`): outputs observed features as a Python dict literal suitable for pasting into `compatibility_hints.py`
- `--diff` flag: show diff between configured (expected) and observed features in the report
- `--no-cleanup` flag: skip test data removal after a run
- `--skip-confirmation` / `--yes` / `-y` flag to suppress interactive prompts for external servers
- `report()` now accepts `show_diff=True` and `return_what="yaml"` / `"hints"`

- Expanded search feature coverage with new feature flags:
  - `search.text` - Basic text/summary search
  - `search.text.case-sensitive` - Case-sensitive text matching (default behavior)
  - `search.text.case-insensitive` - Case-insensitive text matching via CalDAVSearcher
  - `search.text.substring` - Substring matching for text searches
  - `search.is-not-defined` - Property filter with is-not-defined operator
  - `search.text.category` - Category search support
  - `search.text.category.substring` - Substring matching for category searches
- `post_filter=False` parameter to all server behavior tests to ensure testing actual server responses
- New `CheckSyncToken` check class for RFC6578 sync-collection reports:
  - Tests for sync token support (full/fragile/unsupported)
  - Detects time-based sync tokens (second-precision, requires sleep(1) between operations)
  - Detects fragile sync tokens (occasionally returns extra content due to race conditions)
  - Tests sync-collection reports after object deletion
- New `CheckAlarmSearch` check class for alarm time-range searches (RFC4791 section 9.9):
  - Tests if server supports searching for events based on when their alarms trigger
  - Verifies correct filtering of alarm times vs event times
- New `CheckPrincipalSearch` check class for principal search operations:
  - Tests basic principal access
  - Tests searching for own principal by display name (`principal-search.by-name.self`)
  - Tests listing all principals (`principal-search.list-all`)
  - Note: Full `principal-search.by-name` testing requires multiple users and is not yet implemented
- New `CheckDuplicateUID` check class for duplicate UID handling:
  - Tests if server allows events with same UID in different calendars (`save.duplicate-uid.cross-calendar`)
  - Detects if duplicates are silently ignored or rejected with errors
  - Verifies events are treated as separate entities when allowed

### Changed
- Improved `search.comp-type.optional` test with additional text search validation

### Fixed
- `create-calendar` feature detection to not incorrectly mark mkcol method as standard calendar creation
- CLI no longer calls `cleanup()` twice (it was called inside `_run_checks_against` and again by the caller)
- CLI now cleans up by default (`force=True`) instead of silently skipping cleanup unless the server was explicitly configured for it
- `cleanup()` no longer raises `AttributeError` when `PrepareCalendar` was never run
- Removed "Not fully implemented yet - TODO" placeholder from the JSON/dict report output
- Fixed broken `missing_keys` / `parent_keys` logic in `Check.run_check()` — declared-feature invariants are now actually enforced, with `logging.error` instead of a trivially-passing assert
- Fixed wrong variable in `CheckRecurrenceSearch`: `infinite-scope` feature now correctly uses `far_future_recurrence` instead of `events`
- Fixed global monkey-patch of `Calendar.search` so the delay value is stored as a class attribute and updated on each `ServerQuirkChecker` construction
- Cleanup now deletes all `csc_*` objects as a fallback when calendar deletion is not supported (not just the hardcoded UID list)
- Fixed missing `set_feature("search.is-not-defined.class", ...)` call in `CheckIsNotDefined`
- Replaced bare `except:` with `except Exception:` throughout to avoid silently swallowing `SystemExit`/`KeyboardInterrupt`
- Replaced production `assert` statements with `logging.error`/`raise` so they are not silenced by `python -O`
- Fixed double `_compute_diff()` call when formatting as plain text with `--diff`
- Fixed typo: "Fature support level found" → "Feature support level found"
- Fixed `type(foo) == date` to use `isinstance` with correct datetime-exclusion semantics in `_filter_2000`
- Decomposed 415-line `PrepareCalendar._run_check` into focused helper methods

## 0.1 - 2025-11-08

This release corresponds with the caldav version 2.1.2

This is the first release, so I shouldn't need to list up changes since the previous release.

This project was initiated in 2023, it was forgotten, I started working a bit on it inside the caldav library in 2024, moved the work into this project in May 2025, and at some point I decided to throw all of the old work away, and start from scratch - to grow the project it's needed with a less chaotic and more organized approach.  I was very close to making a dual release of the caldav library and the caldav-server-tester library just before the summer vacation started, but didn't manage - and then for half a year things were continously happening in my life preventing me to focus on the caldav project.  So this is a very much overdue release.
