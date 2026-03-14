# caldav-server-tester

A command-line tool that probes a CalDAV server and reports which features and
RFC requirements it supports, which ones it handles quirky, and which ones it
gets wrong.

It is a companion to the [caldav](https://github.com/python-caldav/caldav)
Python client library and produces output that is compatible with that
library's `compatibility_hints.py` feature-flag system.

> **Status**: alpha / pre-1.0.  The checks are useful today, but the
> interface may change before the 1.0 release.

## What it checks

- Calendar creation and deletion (`MKCALENDAR`)
- Saving and loading events, tasks, and journals
- Time-range, text, alarm, and recurrence searches (RFC 4791)
- Case sensitivity and substring matching in text searches
- Sync-collection reports (RFC 6578)
- Free-busy queries
- Principal discovery (RFC 5397)
- Duplicate UID handling across calendars
- Timezone support in events

For full usage information, including all CLI options and output formats, see
**[USAGE.md](USAGE.md)**.

## Installation

```
make install
```

(This auto-detects `uv`, `pipx`, or `pip` and does the right thing.)

## Quick example

```
caldav-server-tester --caldav-url https://example.com/dav \
                     --caldav-username alice \
                     --caldav-password secret
```

See [USAGE.md](USAGE.md) for details on output formats (`--format
json/yaml/hints`), the `--diff` flag, running individual checks, and safety
considerations.

## Background

The caldav client library accumulated a large set of compatibility flags to
work around differences between CalDAV server implementations.  Every server
deviates from the RFC in at least some way — some silently ignore unsupported
features, some return errors, some return wrong data.

This tool was created to make it easy to discover and document those
deviations without having to run the full caldav test suite and interpret
failures manually.  It can also alert you when a previously flagged problem
has been fixed in a newer server version.

## Vocabulary

The project is called **caldav-server-tester**, but internally the word
*test* is reserved for code under the `tests/` directory.  Code that probes
server capability is called a **check**.

## License

AGPL-3.0-or-later
