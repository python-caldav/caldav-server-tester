# Code Review — 2026-03-13

Covers: `src/caldav_server_tester/checks_base.py`, `checker.py`,
`caldav_server_tester.py`, `checks.py`.

---

## Critical bugs

### 1. Dead / broken assertion logic in `Check.run_check()` (`checks_base.py:103-125`)

Two problems in the block that is supposed to verify every declared feature
was actually checked:

**a) `missing_keys` is clobbered on the very next line after it is computed:**

```python
missing_keys = self.features_to_be_checked - new_keys   # line 103
parent_keys = ()                                         # line 104
...
missing_keys = set()                                     # line 108  ← overwrites!
for missing in missing_keys:                             # iterates empty set → no-op
    ...
assert not missing_keys                                  # always passes trivially
```

The intent was to remove from `missing_keys` any key whose parent prefix
already appears in `keys_after`. As written this is completely dead — missing
features are never detected.

**b) `parent_keys` is a `tuple` but `.add()` is called on it (line 115):**

```python
parent_keys = ()                  # tuple
...
parent_keys.add(feature_)         # AttributeError: 'tuple' object has no attribute 'add'
```

This code is currently unreachable (because `missing_keys` is always empty),
but the latent bug will fire the moment the `missing_keys = set()` on line 108
is removed.

**c) `extra_keys` mutation-while-iterating is latent, not currently firing (lines 120-125):**

```python
extra_keys = new_keys - self.features_to_be_checked
for x in extra_keys:
    for y in parent_keys:           # parent_keys is always () — inner body never runs
        if x.startswith(y):
            extra_keys.remove(x)   # would be RuntimeError if reached
```

Because `parent_keys` is always an empty tuple (the `missing_keys` loop
above never runs to populate it), the inner `for y` body is never entered
and `extra_keys.remove(x)` is never reached. The bug is real but currently
harmless. If the `missing_keys = set()` clobber (issue 1a) is ever fixed,
this will start throwing `RuntimeError` for non-empty `extra_keys`.

---

### 2. Wrong variable in `CheckRecurrenceSearch._run_check()` (`checks.py:1207`)

```python
far_future_recurrence = cal.search(
    start=datetime(2045, 3, 12, tzinfo=utc), ...     # line 1201
)
self.set_feature(
    "search.recurrences.includes-implicit.infinite-scope",
    len(events) == 1                                  # line 1207 — uses `events`, not `far_future_recurrence`!
)
```

`events` at this point holds the February 2000 search result (set at line
1144), which is the same value tested by `implicit_datetime`. The
`far_future_recurrence` variable that was just computed is never used. The
feature effectively measures the same thing as
`search.recurrences.includes-implicit.event`, which makes it useless.

Should be `len(far_future_recurrence) == 1`.

---

## Significant issues

### 3. Global monkey-patch of `Calendar.search` in `ServerQuirkChecker.__init__()` (`checker.py:36-43`)

```python
if not hasattr(Calendar, "_original_search"):
    Calendar._original_search = Calendar.search
    def delayed_search(self, *args, **kwargs):
        time.sleep(delay)
        return Calendar._original_search(self, *args, **kwargs)
    Calendar.search = delayed_search
```

- This patches the *class*, not an instance, so the patch affects every
  `Calendar` in the process — including code paths unrelated to this tester.
- The `delay` value is captured by closure from whichever
  `ServerQuirkChecker` was constructed first. A second instance with a
  different delay will silently use the first one's value.
- The patch persists for the lifetime of the process even after the
  `ServerQuirkChecker` is destroyed.

A safer approach: wrap at the instance level or pass the delay through
another mechanism.

### 4. `missing_keys` / `features_to_be_checked` invariant is never enforced

As shown in issue #1, the assertion that every declared feature was checked
is a no-op. New check classes can silently omit setting features they
declared in `features_to_be_checked` and the runtime will not notice.

### 5. Hardcoded UID list in `cleanup()` is out of sync with `PrepareCalendar` (`checker.py:86-102`)

The fallback cleanup path lists UIDs by hand. `PrepareCalendar` adds
additional objects (e.g. `csc_event_with_alarm`, `csc_yearly_recurring_allday_event`,
`weeklymeeting`, `csc_url_check`, `csc_no_time_range_*`, sync-test events,
timezone-test events) that are *not* in this list. If calendar deletion is
not supported, those objects will be left behind.

To avoid this in the future, we should:

1) have code comments in the prepare-calendar warning that anything added needs to be cleaned up
2) Perhaps have an option for searching for objects with uid starting with `csc_` after cleanup

---

## Minor bugs / typos

### 6. Typo in `checker.py:200`

```python
lines.append(f"Fature support level found: {support}")
```

"Fature" → "Feature".

### 7. `_compute_diff()` called twice when `return_what=str` and `show_diff=True` (`checker.py:156-157, 209-212`)

When formatting as plain text the method builds a `ret` dict (including
`ret["diff"]` at line 157) that is never used for the text path. Then
`_compute_diff()` is called again at line 209. Either remove the
`ret["diff"]` assignment for the `str` path or reuse the already-computed
value.

### 8. Redundant `import logging` inside `CheckMakeDeleteCalendar._try_make_calendar()` (`checks.py:110`)

`logging` is already imported at the top of the module (line 1).

### 9. Double assignment of `features_to_be_checked` in `PrepareCalendar` (`checks.py:256-258`)

```python
features_to_be_checked = set()       # line 256 — immediately overwritten
depends_on = {CheckMakeDeleteCalendar}
features_to_be_checked = {           # line 258 — the real value
    "save-load.event.recurrences",
    ...
}
```

The first assignment is dead code.

### 10. Missing `set_feature` for `search.is-not-defined.class` in `CheckIsNotDefined` (`checks.py:984-1001`)

`class_works` is computed and used in the summary `results` dict (line
1033) that drives the overall `search.is-not-defined` feature value, but
there is no corresponding `self.set_feature("search.is-not-defined.class",
...)` call — unlike the analogous `.category` and `.dtend` sub-features.
This means the class sub-feature is not individually observable.

---

## Style / quality concerns

### 11. Pervasive bare `except:` clauses

Many places catch all exceptions including `SystemExit` and
`KeyboardInterrupt`, e.g. `checks.py:61, 93, 117, 124, 211, 287, 374, 410`
and others. These should be at minimum `except Exception:`.

### 12. `type(foo) == date` instead of `isinstance` (`checks.py:30`)

```python
asdate = lambda foo: foo if type(foo) == date else foo.date()
```

- `type(x) == date` is False for `datetime` (a subclass of `date`), which is
  arguably intentional here, but it should be commented if so.
- Assigning a `lambda` to a name is a PEP 8 anti-pattern; use `def`.

### 13. Misleading TODO comment in `caldav_server_tester.py:108-110`

```python
## TODO: this looks somewhat convoluted ... wouldn't it be better with this:
#return_what = output_format if output_format in ("json", "yaml", "hints" else str
```

The commented-out line has a syntax error (missing closing paren). If the
intent is to show the improved form, fix the syntax; if the idea was
abandoned, remove the comment.

### 14. `assert` used for runtime validation in non-test code

`checks_base.py` uses `assert` to validate runtime invariants (lines 64,
117, 125) and `checks.py` uses them in logic paths (e.g. line 1143). These
are silently skipped when Python runs with `-O`. Replace with explicit
`if ... raise` checks where the invariant matters.

### 15. `PrepareCalendar._run_check()` is ~415 lines long (`checks.py:272-687`)

It creates all test fixtures, handles fallbacks for tasks and journals,
manages three separate calendar handles, and sets a dozen features. Consider
splitting into focused helpers (e.g. `_prepare_task_calendar`,
`_prepare_journal_calendar`, `_create_test_events`).

---

## Summary table

| # | Severity | File | Location | Issue |
|---|----------|------|----------|-------|
| 1a | Critical | `checks_base.py` | 103–117 | `missing_keys` overwritten; assertion always passes |
| 1b | Critical | `checks_base.py` | 115 | `tuple.add()` — latent `AttributeError` |
| 1c | Medium | `checks_base.py` | 120–125 | Latent `RuntimeError` (set modified during iteration) — currently dead because `parent_keys` is always empty |
| 2 | Critical | `checks.py` | 1207 | `infinite-scope` uses wrong variable (`events` vs `far_future_recurrence`) |
| 3 | High | `checker.py` | 36–43 | Global monkey-patch of `Calendar.search` leaks across process |
| 4 | High | `checks_base.py` | 100–125 | Feature-check invariant never enforced |
| 5 | High | `checker.py` | 86–102 | Cleanup UID list incomplete vs objects `PrepareCalendar` creates |
| 6 | Low | `checker.py` | 200 | Typo: "Fature" |
| 7 | Low | `checker.py` | 156–157, 209 | `_compute_diff()` called twice for text format |
| 8 | Low | `checks.py` | 110 | Redundant `import logging` inside method |
| 9 | Low | `checks.py` | 256 | Dead first assignment of `features_to_be_checked` |
| 10 | Medium | `checks.py` | 984–1001 | Missing `set_feature` for `search.is-not-defined.class` sub-feature |
| 11 | Medium | `checks.py` | many | Bare `except:` should be `except Exception:` |
| 12 | Low | `checks.py` | 30 | `type(x) == date` and `lambda` assigned to name |
| 13 | Low | `caldav_server_tester.py` | 108–110 | Misleading TODO with syntax-error example |
| 14 | Medium | `checks_base.py`, `checks.py` | various | `assert` used for production runtime checks |
| 15 | Low | `checks.py` | 272–687 | `PrepareCalendar._run_check` too long, needs decomposition |
