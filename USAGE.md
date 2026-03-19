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

Each feature is reported as a block with up to three lines:

```
Server: radicale (http://localhost:5232/)
caldav library version: 1.5.0

Feature compatibility (non-verbose: showing only deviations from the standard):

## search.time-range.alarm
Feature support level found: unsupported

## search.unlimited-time-range
Feature support level found: quirk
Extra check information:
  behaviour=accepts-but-ignores-end-date
Description of the feature: Whether the server supports CalDAV REPORT search without an end date in a time-range filter
```

The **"Extra check information"** line describes the *specific behaviour observed* during testing — for example, what the server actually did when a feature was exercised (e.g. `behaviour=delayed-deletion`, `behaviour=mkcol-required`).  This is distinct from the **"Description of the feature"** line, which gives the general definition of what the feature covers.

Support levels:

| Value          | Meaning                                                            |
|----------------|--------------------------------------------------------------------|
| `full`         | Full, standard-compliant support                                   |
| `unsupported`  | Not supported (server silently ignores or rejects the operation)   |
| `quirk`        | Supported but requires special client-side handling                |
| `fragile`      | Unreliable or intermittent behaviour                               |
| `broken`       | Server behaves incorrectly (wrong results, data loss, etc.)        |
| `unknown`      | Could not be determined (e.g. preconditions for the test not met)  |

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

## Contributing a server profile to the caldav library

If your CalDAV server is not yet listed in
[`caldav/compatibility_hints.py`](https://github.com/python-caldav/caldav/blob/master/caldav/compatibility_hints.py),
you can use this tool to produce a ready-made profile and submit it upstream.

**Step 1 — run the tester and capture the hints output:**

```
caldav-server-tester --caldav-url https://example.com/dav \
                     --caldav-username alice \
                     --caldav-password secret \
                     --format hints > myserver_hints.py
```

The output is a Python dict literal containing every feature the tester
observed, e.g.:

```python
{
    'create-calendar': {'support': 'full'},
    'search.time-range.alarm': {'support': 'unsupported'},
    'search.unlimited-time-range': {'support': 'quirk', 'behaviour': 'accepts-but-ignores-end-date'},
    ...
}
```

**Step 2 — add the profile to `compatibility_hints.py`:**

In a fork of the [caldav repository](https://github.com/python-caldav/caldav),
open `caldav/compatibility_hints.py` and add a module-level variable near the
other server profiles (look for variables like `radicale`, `baikal`, `xandikos`):

```python
myserver = {
    'search.time-range.alarm': {'support': 'unsupported'},
    'search.unlimited-time-range': {'support': 'quirk', 'behaviour': 'accepts-but-ignores-end-date'},
    # ... paste the non-full entries from the hints output
}
```

It is conventional to strip entries where `support` is `full` (the default),
keeping only deviations.  Add a short comment above the dict describing the
server and the version it was tested against.

**Step 3 — open a pull request** against `python-caldav/caldav` on GitHub.
Include the raw `--format text --verbose` output as supporting evidence in the
PR description so maintainers can verify the findings.

## Diffing expected vs observed

When you have an existing `compatibility_hints` configuration for a server
and want to see whether the server behaviour has changed:

```
caldav-server-tester --caldav-url … --caldav-features zimbra --diff
```

The `--diff` flag appends a section to the report listing every feature where
the observed support level differs from what the configured hints said to
expect.

## Storing results in your caldav config file

The `~/.config/caldav/calendar.conf` (YAML or JSON) supports a `features` key
in each section that tells the caldav client library which workarounds to
apply.  There are two ways to populate it.

### Using a named profile

If your server already has a profile in `caldav/compatibility_hints.py` (e.g.
`radicale`, `baikal`, `xandikos`, `synology`, …), simply name it:

```yaml
myserver:
    caldav_url: https://example.com/dav
    caldav_username: alice
    caldav_password: secret
    features: radicale
```

### Using inline features from the tester

If there is no profile for your server yet, run the tester and copy the
`features` block from the YAML output directly into your config:

```
caldav-server-tester --caldav-url https://example.com/dav \
                     --caldav-username alice \
                     --caldav-password secret \
                     --format yaml
```

The output contains a `features:` mapping.  Paste it under your config section:

```yaml
myserver:
    caldav_url: https://example.com/dav
    caldav_username: alice
    caldav_password: secret
    features:
        search.time-range.alarm:
            support: unsupported
        search.unlimited-time-range:
            support: quirk
            behaviour: accepts-but-ignores-end-date
```

### Extending a named profile with local overrides

If your server is close to a known profile but differs on a few features, use
the `base` key to inherit that profile and then override only what differs:

```yaml
myserver:
    caldav_url: https://example.com/dav
    caldav_username: alice
    caldav_password: secret
    features:
        base: radicale
        search.time-range.alarm:
            support: full
```

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
