# Usage

## Installation

```
make install
```

## Quick start

 `caldav-server-tester --help` is probably the first command you should test.

### Against a CalDAV server you specify directly

```
caldav-server-tester --caldav-url https://example.com/dav \
                     --caldav-username alice \
                     --caldav-password secret
```

The tester will (by default) create a new calendar, populate it with test data, and delete the calendar when it's done.  For servers not supporting calendar creation, specify an existing calendar to use:

```
caldav-server-tester --caldav-url https://example.com/dav \
                     --caldav-username alice \
                     --caldav-password secret \
                     --caldav-calendar "My Test Calendar"
```

The `--caldav-calendar` option takes the display name of an existing calendar on the server.

### Against the caldav test servers

If you have the [caldav](https://github.com/python-caldav/caldav) repository
checked out, `caldav-server-tester` may auto-discover the test server
registry.  The `--name`-parameter will try the test servers first:

```
cd ~/caldav
caldav-server-tester --name radicale
```

### Against a server from the caldav config file

If no URL was explicitly given and `--name` didn't match any test servers, then it will search for a config file (typically `~/.config/caldav/calendar.conf`):

```
caldav-server-tester --config-section myserver
```

Note that the only difference between `--name` and `--config-section` is that `--config-section` will not do any attempt on searching for caldav test servers.

## Output formats

### `--format text` (default)

Human-readable summary.  Without `--verbose`, only features deviating from the CalDAV standard are shown.  With `--verbose`, all checked features are shown.

```
Server: radicale (http://localhost:5232/)
caldav library version: 1.5.0

Feature compatibility (non-verbose: showing only deviations from the standard):
  [no]       search.time-range.alarm
  [quirk]    search.unlimited-time-range
```

Status markers:

| Marker       | Meaning                                                  |
|--------------|----------------------------------------------------------|
| `[ok]`       | Full support                                             |
| `[no]`       | Unsupported (silently ignored by the server)             |
| `[quirk]`    | Supported but needs special client-side handling         |
| `[fragile]`  | Unreliable / intermittent                                |
| `[broken]`   | Server behaves incorrectly                               |
| `[error]`    | Server returns an error (ungraceful failure)             |

### `--format json` / `--format yaml`

Machine-readable output in JSON or YAML.  Useful for storing results or
feeding them into other tools.

```
caldav-server-tester --caldav-url … --format json > results.json
caldav-server-tester --caldav-url … --format yaml > results.yaml
```

### `--format hints`

Outputs the observed features as a Python dict literal, suitable for
copy-pasting into the
[`compatibility_hints.py`](https://github.com/python-caldav/caldav/blob/master/caldav/compatibility_hints.py)
file in the caldav project (or your own config):

```python
{
    'create-calendar': {'support': 'full'},
    'delete-calendar': {'support': 'full'},
    'search.time-range.event': {'support': 'full'},
    'search.time-range.alarm': {'support': 'unsupported'},
    ...
}
```

## Diffing expected vs observed

When you have an existing `compatibility_hints` configuration for a server
and want to see whether the server behaviour has changed:

```
caldav-server-tester --caldav-url … --caldav-features zimbra --diff
```

The `--diff` flag appends a section to the report listing every feature where
the observed support level differs from what the configured hints said to
expect.

## Safety

For servers that supports `MKCALENDAR`, a dedicated calendar will be
created on the server for compatibility testing, and deleted after the
testing (unless `--no-cleanup` is used).

For servers that do not support `MKCALENDAR`, you must specify a calendar
to use via `--caldav-calendar <display-name>`.  All test data will be
deleted from the server after use.  It's best to provide the check
script with a dedicated calendar for the checking, but running the
checks towards your personal calendar should be safe.

Test data is deliberately placed in the year 2000, minimising the chance of
collisions with real calendar entries.  UIDs are all prefixed with `csc_`.

By default the tool **cleans up** all test data after each run (deletes the
test calendar if calendar creation/deletion is supported, or deletes
individual objects by UID otherwise).  Pass `--no-cleanup` to leave the test
data in place for inspection.


## Running individual checks

List available checks:

```
caldav-server-tester --list-checks
```

Run a specific check class:

```
caldav-server-tester --caldav-url … --run-checks CheckSearch
caldav-server-tester --caldav-url … --run-checks CheckSyncToken --run-checks CheckFreeBusyQuery
```

Run checks by feature name:

```
caldav-server-tester --caldav-url … --run-feature search.time-range.alarm
```

Be aware that there are some dependencies, so more checks than what you asked for may be executed.
