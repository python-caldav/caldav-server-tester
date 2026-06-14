# Full code review — 2026-06-11

⚠️ This review is AI-generated (Claude Fable 5 via Claude Code) on behalf of tobixen.

## Fix progress tracker

Started 2026-06-14 (Claude Opus 4.8 via Claude Code). Status legend:
✅ done · 🚧 in progress · ⬜ todo · ❌ won't fix (see note).

| # | Finding | Status | Notes |
|---|---------|--------|-------|
| 1 | Stale-fixture deletion can destroy real user data | ✅ | `_delete_stale_fixtures` only deletes `csc_*` UIDs |
| 2 | cleanup() deletes a pre-existing user calendar | ✅ | `calendar_was_created` gates wholesale delete |
| 3 | CLI run lifecycle has no try/finally | ✅ | single `_run_lifecycle` helper runs cleanup+report in `finally`; also de-duplicates C-5's triplicated lifecycle |
| 4 | "Cannot probe" becomes AssertionError | ❌ | **Not a real issue — no code change.** Maintainer was right. Empirically: when the parent (e.g. `search.time-range.todo`) is explicitly set, `run_check`'s parent-collapse drops the unset sub-feature from `missing_keys` so the assert never fires, and `is_supported()` derives the sub-feature to **unsupported** (not unknown). Reverted an earlier "set to unknown" attempt — it was wrong twice (fired a path normal runs don't need, and overrode the correct derived `unsupported`). Added `test_run_check_unset_subfeature_derives_from_parent` to lock the behavior in. |
| 5 | CheckRecurrenceSearch: unwrapped searches abort the run | ❌ | **Already mitigated — no code change.** The generic `except Exception` handler in `run_check` (added in `4a79913`, *after* the review base `61eda96`) catches a DAVError from any unwrapped search, marks the remaining declared features `unknown`, and lets the run continue. "unknown" is the maintainer-preferred outcome for error cases (cf. #9). Per-search `ungraceful` precision is a possible enhancement, not a correctness fix. |
| 6 | UID `weeklymeeting` violates the csc_ cleanup invariant | ✅ | renamed `csc_weeklymeeting`; moved both Oct fixtures into Jan window |
| 7 | --cleanup-only reports success on failure | ✅ | `_purge_csc_objects` now returns `(removed, errors)`, logs warnings; `--cleanup-only` exits non-zero on errors |
| 8 | Explicit --caldav-url path drops extra --config-section accounts | ✅ | shared `_build_extra_clients`/`_close_extra_clients`; URL path treats every `--config-section` as an extra account |
| 9 | Transient errors misclassified as "unsupported" | ✅ | retry once, keep init principal, mark `unknown` + behaviour note + STDERR warning (per maintainer note) |
| 10 | report() collapses feature data before lossless branches read it | ✅ | **Confirmed real** (verified `compact=True` permanently mutates). Compact now runs on a `copy.deepcopy`; hints/verbose keep per-child notes |
| BTC-a | CheckTodoNoDtstartSearch recomputes `_base_year()` | ✅ | fixed in CheckTodoNoDtstartSearch; the `checks.py:3132` variant is intentional (that check has no PrepareCalendar dep — fallback is correct) |
| BTC-b | create-calendar.auto probe catches too few exceptions | ✅ | catches `DAVError` base class so a 500 no longer escapes/aborts the rest of the probe |
| C-1 | Emacs backup files tracked in git | ✅ | already resolved upstream — no `~` files tracked, `.gitignore` has `*~` |
| C-2 | Write-only state (`checker.cnt`, dead class attrs) | ✅ | removed write-only `checker.cnt` bookkeeping and the dead `Check.features_checked = set()` class attr |
| C-3 | Process-global monkey-patch of Calendar.search | ✅ | per-checker reset of the class-level delay (true per-instance wrap impossible — library owns Calendars); validated against Bedework |
| C-4 | Duplicated UIDs/calendar-id literals | ✅ | `TEST_CALENDAR_CAL_ID`, `OLDDATE_EVENT_UID`, `OLDDATE_TASK_UID` constants in checks.py, referenced from checker.py. (`.ics` fallback handled under C-5) |
| C-5 | Repeated idioms worth a helper | ✅ | lifecycle (#3); `url_object()` for `<uid>.ics`; `resolve_cu_address()` for the triplicated scheduling address resolution. Deferred: delete-suppress sweep and except-classify-tuple unification (low value / high churn / heterogeneous per-site risk) |
| C-6 | Wasted round-trips | ⬜ | cleanup |

Scope: the full codebase (not a diff review), ~7,300 lines of Python, with the
bulk in `src/caldav_server_tester/checks.py` (3,839 lines).

Method: 7 parallel finder angles (line-by-line correctness, error-handling /
invariants, cross-file contracts, reuse, simplification, efficiency, altitude),
42 raw candidates, deduplicated and then verified against the actual code.
Line numbers refer to the tree at commit `61eda96`
(branch `feature/fixtures-next-year-window`).

## TL;DR

The standout theme is **fixture cleanup vs. real user data**: two confirmed
paths can silently delete a user's production calendar or its contents when
`--caldav-calendar` points at a real calendar. Second theme: several
rare-but-reachable error paths crash the whole run with no report (and skip
cleanup), because the CLI lifecycle has no try/finally and `run_check` turns
"couldn't probe" into an `AssertionError`.

## Correctness findings (ranked, verified)

### 1. Stale-fixture deletion can destroy real user data

`src/caldav_server_tester/checks.py:1015`

PrepareCalendar's stale-fixture loop deletes any leftover object in
`object_by_uid` without checking the `csc_` UID prefix — including real user
data; journals are added to the dict entirely unfiltered (line 977), and the
empty-search fallback (lines 958-966) pulls in ALL events/todos,
window-filtered only to the whole base year.

**Failure scenario:** Run with `--caldav-calendar 'Work'` against a production
calendar: every real VJOURNAL (`journals_in_window = calendar.journals()`, no
`_filter_fixture_window`), and any real event/task dated in the fixture base
year (next calendar year), survives the `add_if_not_existing` pops and is then
`obj.delete()`'d at line 1017 as a "stale fixture". Silent permanent data loss
with only a log warning.

**Maintainers comments:** The original goal was to make the tester in such a way that it would be possible to run it on a "production calendar" (nothing in sharp production, but at least towards a personal calendar that has been backed up).  I'm not sure if this goal is achievable, but it's worth investigating - and if it is too difficult, then at least the documentation should be very clear on it.

### 2. cleanup() deletes a pre-existing user calendar

`src/caldav_server_tester/checker.py:137`

`cleanup()` decides to delete `self.calendar` based only on
create-calendar/delete-calendar feature support, never tracking whether
PrepareCalendar created the calendar or merely found a pre-existing one via
`--caldav-calendar`.

**Failure scenario:** `--caldav-calendar 'Work'` on a server that supports
MKCALENDAR/DELETE: `_find_or_create_calendar` (checks.py:443-448) binds
`checker.calendar` to the existing production calendar by display name; at end
of run `cleanup(force=True)` executes `self.calendar.delete()`, destroying the
user's entire calendar — contradicting the docstring "Remove anything added by
the PrepareCalendar check".

**Maintainers comments:** Same as for #1 above

### 3. CLI run lifecycle has no try/finally

`src/caldav_server_tester/caldav_server_tester.py:279` (also 174-191, 337-352)

If any check raises, `cleanup(force=True)` is skipped (fixtures leak onto the
server); if cleanup itself raises, `_emit_report` is never reached and the
whole report is lost. The same structure exists in all three paths
(explicit-URL 276-283, `_check_server` 174-191, config-file 337-352).

**Failure scenario:** A long run completes all checks, then the server answers
the calendar DELETE with 500 (the "delayed deletion" behaviour
CheckMakeDeleteCalendar itself documents): cleanup raises, the gathered
results of the entire run are discarded, the user gets a traceback.
Conversely any mid-run check exception leaves `csc_*` fixtures and test
calendars behind despite no `--no-cleanup`.

### 4. "Cannot probe" becomes AssertionError

`src/caldav_server_tester/checks_base.py:119`

`run_check` asserts all declared features were set, but several checks have
legitimate "cannot probe" early returns (CheckTodoNoDtstartSearch on DAVError
at checks.py:1057 and tasklist-None at 1047; CheckPropfindAllprop on principal
None at 117), so an unprobeable feature becomes an AssertionError that aborts
the run.

**Failure scenario:** `caldav-server-tester --run-checks
CheckTodoNoDtstartSearch` against a server whose VTODO REPORT raises DAVError:
nothing sets `search.time-range.todo.no-dtstart` or any parent key ("search"
is never set when only PrepareCalendar deps ran), `missing_keys` is non-empty,
AssertionError crashes the CLI — no report, no cleanup (see finding 3).

**Maintainers note:** "Cannot probe" should cause things to go in "unknown".  If all `search` is unknown, then `search.time-range.todo.no-dtstart` will also be set to "unknown" by the derivation logic in the caldav library.  I haven't seen assert errors - I believe this "issue" is a misunderstanding rather than a real issue.

### 5. CheckRecurrenceSearch: unwrapped searches abort the run

`src/caldav_server_tester/checks.py:2198`

CheckRecurrenceSearch guards only its precondition searches; the later
searches at 2104, 2157, 2198, 2210, 2223, 2248, 2275 are unwrapped — notably
the `server_expand=True` REPORTs, a different request type that servers may
reject even when plain calendar-queries work.

**Failure scenario:** A server that handles calendar-query but answers expand
REPORTs with 400/500: DAVError escapes `_run_check`, propagates through
`check_all`, aborts the whole run with a traceback instead of recording
`search.recurrences.expanded.*` as ungraceful/unsupported.

**Maintainers note:** I haven't seen assert errors - this "issue" may be a misunderstanding rather than a real issue, but it should be investigated.

### 6. UID `weeklymeeting` violates the csc_ cleanup invariant

`src/caldav_server_tester/checks.py:700`

The rrule-and-count fixture uses UID `weeklymeeting`, violating the invariant
stated at lines 921-924 that all fixtures use `csc_*` so the cleanup fallback
can find them; its October DTSTART also falls outside the Jan-Mar
reuse-detection search window.

**Failure scenario:** On any server without delete-calendar support (the
`--caldav-calendar` case), cleanup falls back to `_purge_csc_objects`
(checker.py:100, `uid.startswith('csc_')`) and the "Weekly meeting for three
weeks" event is left in the user's calendar permanently; `--cleanup-only`
never removes it either, and it is re-PUT on every run.

### 7. --cleanup-only reports success on failure

`src/caldav_server_tester/checker.py:105`

`_purge_csc_objects` and `cleanup_test_data` swallow every exception
(`except Exception: pass` at lines 103-106, 116-117, 166-179), so
`--cleanup-only` reports success when listing or deletion actually failed.

**Failure scenario:** `--cleanup-only` against a server where `cal.objects()`
raises 403: all exceptions are silently eaten, `_do_cleanup_only` prints
"Removed 0 caldav-server-tester object(s)." and exits 0; the user believes the
server is clean while every `csc_*` fixture remains, corrupting stale-fixture
detection on the next run.

### 8. Explicit --caldav-url path silently drops extra --config-section accounts

`src/caldav_server_tester/caldav_server_tester.py:271`

The explicit `--caldav-url` path returns at line 283 before the extra-clients
construction at 324-330, so additional `--config-section` accounts are
silently ignored and multi-user scheduling checks degrade to "unknown".

**Failure scenario:** `caldav-server-tester --caldav-url ... --config-section
user2`: `extra_clients` is never built, `ServerQuirkChecker.extra_principals`
stays empty, and CheckSchedulingInboxDelivery / CheckFreeBusyQueryRFC6638 /
CheckScheduleTagStablePartstat all report "only one user configured" with no
warning that the supplied section was dropped.

### 9. Transient errors misclassified as "unsupported"

`src/caldav_server_tester/checks.py:87`

CheckGetCurrentUserPrincipal's broad `except Exception` classifies any
transient failure (503, TLS, ConnectionError) as "get-current-user-principal
unsupported" — even though `checker.__init__` already proved `principal()`
works, so a later failure is by definition transient.

**Failure scenario:** A transient 503 during this one call: principal is set
to None (silently disabling CheckPropfindAllprop, which then trips the
checks_base assert if "propfind" was never set), the report claims the feature
is unsupported, and the CLI exits 0.

**Maintainers note:** It could be that certain features consistently throw what seems like transient failures ... but the correct behaviour here is to possibly retry, and then mark the feature as "unknown" with a note in the behaviour that we may have been hitting a transient problem, and a warning to STDERR about it.

### 10. report() collapses feature data before the lossless branches read it

`src/caldav_server_tester/checker.py:219`

`report()` calls `dotted_feature_set_list(compact=True)` — which the comment
at line 217 admits permanently mutates `_features_checked` — before the
"hints" (line 241) and verbose-text (line 261) branches re-list with
`compact=False`, so those outputs are built from already-collapsed, lossy
data.

**Failure scenario:** `--format hints` with sibling features that collapse
(e.g. all children unsupported but with differing behaviour notes): line 219
collapses them into the parent first, then the `compact=False` listing for
hints returns only the collapsed parent — per-child behaviour annotations
promised by the "include all observed features" comment are gone.

**Maintainers note:** Check my comments on number 4.  It may be that this is intentional.

### Confirmed but below the cut

- `checks.py:1048` — CheckTodoNoDtstartSearch recomputes `_base_year()`
  instead of using `self.checker.fixture_base_year` like every other consumer
  (wrong window if a run crosses New Year's midnight; `checks.py:2980` has yet
  a third variant: `getattr(..., None) or _base_year()`).
- `checks.py:280` — the `create-calendar.auto` probe catches only
  `(NotFoundError, AuthorizationError, ReportError)` while the comment two
  lines down admits some servers answer 500 in ways that would escape and
  abort the run.

## Cleanup / design findings (verified, lower priority)

- **Four emacs backup files are tracked in git**:
  `src/caldav_server_tester/{caldav_server_tester_old.py~, checker.py~,
  checks_base.py~, checks.py~}` (plus `CHANGELOG.md~` etc. in the repo root).
  They ship in the sdist and confuse greps — delete and add `*~` to
  `.gitignore`.
- **Write-only state**: `checker.cnt` (`checks.py:973`,
  incremented/decremented at 984, 529, 545, 666) is never read anywhere;
  `checks.py:1532` even documents that it's unused. The careful `cnt -= 1`
  bookkeeping in error branches is pure noise. Likewise
  `features_checked = set()` at `checks_base.py:15` is a dead class attribute
  that collides with the `checker.features_checked` property name.
- **Process-global monkey-patch**: `checker.py:47-56` patches
  `caldav.collection.Calendar.search` at class level for the search-delay
  quirk and never unpatches; a second `ServerQuirkChecker` for a fast server
  in the same process inherits the previous server's delay (the comment
  acknowledges this, but a per-instance wrapper would remove the hazard).
- **Duplicated knowledge that must stay in sync by hand**: the `csc_olddate_*`
  probe UIDs/dates exist in three places (`checks.py:873-885`,
  `checks.py:1634/1646`, `checker.py:78`); the calendar-id literals
  `caldav-server-checker-calendar` (+`_tasks`/`_journals`) are hardcoded
  independently in `checker.py:157-161` and `checks.py:932/477/518`; the
  `<uid>.ics` direct-URL fallback is re-implemented in ~5 places.
- **Repetition worth a helper**: the `try: obj.delete() / except Exception:
  pass` idiom appears at roughly 20 sites (a `Check._safe_delete()` or
  `contextlib.suppress` would do); the except-classify-as-"ungraceful" pattern
  is repeated ~18 times with drifting exception tuples; calendar-user address
  resolution is triplicated across the three scheduling checks; the CLI run
  lifecycle is triplicated (which is also what makes the missing try/finally a
  three-place fix).
- **Wasted round-trips**: two full `calendar.events()` listings in
  PrepareCalendar (`checks.py:788` just to count one UID, `checks.py:1018`
  just to log a warning); flat `time.sleep(10)`s in `_try_make_calendar`
  (lines 236, 254) instead of polling; the inbox-delivery check polls 30×1s
  with a full inbox listing each time, always exhausting the budget on servers
  that don't deliver.

⚠️ This review is AI-generated (Claude Fable 5 via Claude Code) on behalf of tobixen.
