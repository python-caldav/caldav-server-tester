# Changelog

## Meta

This file should adhere to [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), but it's manually maintained.  Feel free to comment or make a pull request if something breaks for you.

This project should adhere to [Semantic Versioning](https://semver.org/spec/v2.0.0.html), though some earlier releases may be incompatible with the SemVer standard.

## [1.0.1] - 2026-03-19

### Fixed
- `--name radicale` (and other lowercase names) failed to find servers in the caldav test registry after the caldav library renamed its server entries to capitalised names (`Radicale`, `Xandikos`).  The registry lookup is now case-insensitive.
- `--name` registry lookup silently returned nothing when the caldav-server-tester's own `tests/` package shadowed the caldav project's `tests/test_servers` in `sys.modules` or via the CWD entry in `sys.path`.  The registry is now loaded via `importlib` using the explicit file path, bypassing `sys.path` resolution.

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
