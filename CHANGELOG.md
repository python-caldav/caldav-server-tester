# Changelog

## Meta

This file should adhere to [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), but it's manually maintained.  Feel free to comment or make a pull request if something breaks for you.

This project should adhere to [Semantic Versioning](https://semver.org/spec/v2.0.0.html), though some earlier releases may be incompatible with the SemVer standard.

This library is tightly dependent on the CalDAV-library, particularly the `compatibility_hints.py`-file.  This file is not (yet) considered to be part of the "core" business logic in the CalDAV library and can be changed in patch-releases in the CalDAV library - so this library is usually released in lock-steps with the CalDAV-library.  I've considered to bump the version number to be follow the CalDAV version number.

## [Unreleased]

### Fixed
- The `search.comp-type.optional` probe compared a single comp-type-less search against the global `cnt` object counter.  `cnt` aggregates the objects created across the event/task/journal calendars (and may include objects that failed to save), so on every server that stores journals or tasks in a *separate* calendar — because `save-load.journal.mixed-calendar` (or the task equivalent) is unsupported — the comp-type-less search on the main calendar could never reach `cnt`, and fully-working servers (Baikal, Davis, CCS, Ox, Zimbra, Bedework, …) were mislabelled `fragile`/`ungraceful`.  The probe now compares the comp-type-less query against the comp-type-*specific* queries (events + todos + journals) across every distinct calendar it populated.  See https://github.com/python-caldav/caldav/issues/681
- `CheckMakeDeleteCalendar` verified `create-calendar.set-displayname` by looking the freshly created calendar up by its display name and asserting the id matched.  Display names are not unique, so a leftover calendar with the same name (e.g. on a server that doesn't free the namespace, or polluted by a previous test run) would shadow the probe calendar and make the feature be wrongly reported as `unsupported`.  The probe now looks the calendar up by `cal_id` and checks its display name directly.

## [1.2.0] - 2026-04-24

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

## [1.1.0] - 2026-04-24

This release works with caldav 3.2.0.

### Added

* Lots of new test probing the scheduling features.  Those requires multiple user accounts on the server.  This can now be configured.
* Lots of new tests probing edge-cases wrg of date searching, open-ended searches, etc

### AI-disclaimer

This release has been predominantly coded with AI-assistance.  The level of scrutiny done on this tool is a bit less than the level of scrutiny done on the caldav libary.  Then again, I don't expect you to run this checker directly towards some calendars that are also used in production.

## [1.0.1] - 2026-03-19

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

## [1.0.0] - 2026-03-15

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


## [0.2.2] - 2026-03-11

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

## [0.1] - [2025-11-08]

This release corresponds with the caldav version 2.1.2

This is the first release, so I shouldn't need to list up changes since the previous release.

This project was initiated in 2023, it was forgotten, I started working a bit on it inside the caldav library in 2024, moved the work into this project in May 2025, and at some point I decided to throw all of the old work away, and start from scratch - to grow the project it's needed with a less chaotic and more organized approach.  I was very close to making a dual release of the caldav library and the caldav-server-tester library just before the summer vacation started, but didn't manage - and then for half a year things were continously happening in my life preventing me to focus on the caldav project.  So this is a very much overdue release.
