# Usage

## Installation

```
pip install caldav-server-tester
```

Or in development (from a checkout):

```
pip install -e .
```

## Quick start

### Against a CalDAV server you specify directly

```
caldav-server-tester --caldav-url https://example.com/dav \
                     --caldav-username alice \
                     --caldav-password secret
```

You will be prompted to confirm before any data is written to the server.
Use `-y` / `--skip-confirmation` to suppress the prompt (e.g. in CI).

### Against servers from a caldav source checkout

If you have the [caldav](https://github.com/python-caldav/caldav) repository
checked out, `caldav-server-tester` will auto-discover the test server
registry and run against all enabled servers (Radicale, Xandikos, etc.):

```
cd ~/caldav
caldav-server-tester
```

Run against a single named server:

```
caldav-server-tester --name radicale
```

### Against a server from the caldav config file

`caldav-server-tester` falls back to the caldav client config file
(typically `~/.config/calendar.conf`) when no URL or registry is found:

```
caldav-server-tester --name myserver
```

## Options reference

```
Usage: caldav-server-tester [OPTIONS]

Options:
  --version                       Show the version and exit.
  --name TEXT                     Server name (from test registry or config)
  --verbose / --quiet             More output
  --format [text|json|yaml|hints] Output format (default: text)
  --diff                          Show diff between expected and observed
                                  features
  --no-cleanup                    Do not remove test data after run
  -y, --skip-confirmation, --yes  Skip interactive confirmation for external
                                  servers
  --caldav-url URL                Full URL to the caldav server
  --caldav-username USERNAME      Username for the caldav server
  --caldav-password PASSWORD      Password for the caldav server
  --caldav-features FEATURES      Server compatibility features preset
  --run-checks TEXT               Run only specific check(s) (repeatable)
  --help                          Show this message and exit.
```

## Output formats

### `--format text` (default)

Human-readable summary.  Without `--verbose`, only non-full (problem) features
are shown.  With `--verbose`, all checked features are shown.

```
Server: radicale (http://localhost:5232/)
caldav library version: 1.5.0

Feature compatibility (non-verbose: showing only non-full features):
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

Test data is deliberately placed in the year 2000, minimising the chance of
collisions with real calendar entries.  UIDs are all prefixed with `csc_`.

By default the tool **cleans up** all test data after each run (deletes the
test calendar if calendar creation/deletion is supported, or deletes
individual objects by UID otherwise).  Pass `--no-cleanup` to leave the test
data in place for inspection.

For servers that do not support `MKCALENDAR`, the tool will use an existing
calendar.  You will be prompted to confirm before writing any data unless
`--skip-confirmation` is passed.

## Running individual checks

```
caldav-server-tester --caldav-url … --run-checks CheckSearch
caldav-server-tester --caldav-url … --run-checks CheckSyncToken --run-checks CheckFreeBusyQuery
```

Available check classes:

| Class                        | What it tests                                     |
|------------------------------|---------------------------------------------------|
| `CheckGetCurrentUserPrincipal` | RFC 5397 current-user-principal support          |
| `CheckMakeDeleteCalendar`    | Calendar creation / deletion / namespace reuse    |
| `CheckSearch`                | Time-range, component-type, and text searches     |
| `CheckAlarmSearch`           | Alarm time-range searches (RFC 4791 §9.9)         |
| `CheckRecurrenceSearch`      | Recurrence expansion and exception handling       |
| `CheckCaseSensitiveSearch`   | Case sensitivity of text searches                 |
| `CheckSubstringSearch`       | Substring matching in text searches               |
| `CheckIsNotDefined`          | `is-not-defined` property filter                  |
| `CheckPrincipalSearch`       | Principal discovery and search                    |
| `CheckDuplicateUID`          | Cross-calendar duplicate UID handling             |
| `CheckSyncToken`             | RFC 6578 sync-collection reports                  |
| `CheckFreeBusyQuery`         | Free-busy query support                           |
| `CheckTimezone`              | Event timezone support                            |
