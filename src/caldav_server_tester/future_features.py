"""Feature definitions the tester probes before a caldav release has them.

caldav's ``compatibility_hints.py`` changes only with a caldav release, so a
new probe would otherwise have to wait for one.  The definitions here are
added to ``caldav.compatibility_hints.FeatureSet.FEATURES`` when the package
is imported.  Where caldav already defines a feature, as caldav's master
branch may, caldav's definition wins.

This changes caldav's class for the whole process, not only for the tester.

Once the caldav release that defines a feature is the floor in
``pyproject.toml``, delete it here (see ``docs/RELEASE-HOWTO.md``).
"""

from caldav.compatibility_hints import FeatureSet

FUTURE_FEATURES: dict[str, dict] = {
    "move": {
        "description": "WebDAV MOVE (RFC 4918 section 9.9) of a calendar object into another calendar of the same principal: the object ends up in the target only.  RFC 4918 says every WebDAV resource MUST support MOVE.  Without it, a client moves by copying and deleting, which fails where save.duplicate-uid.cross-calendar is not supported.  See https://github.com/python-caldav/caldav/issues/841",
        ## Not derived from move.overwrite and move.if-match: a server ignoring them still moves.
        "default": {"support": "full"},
    },
    "move.overwrite": {
        "description": "A MOVE with 'Overwrite: F' onto an object that already exists at the destination fails with 412 and changes nothing, as RFC 4918 section 10.6 requires.  Where unsupported, the MOVE replaces the object at the destination."
    },
    "move.if-match": {
        "description": "A MOVE with an If-Match the object no longer has fails with 412 and moves nothing (RFC 9110 section 13.1.1).  Where unsupported, a MOVE cannot guard against an object changed since it was read."
    },
    ## caldav PR 848 (https://github.com/python-caldav/caldav/pull/848)
    "save-load.todo.status": {
        "description": "Each STATUS RFC 5545 section 3.8.1.11 gives a task (NEEDS-ACTION, IN-PROCESS, COMPLETED, CANCELLED) is stored as sent.  'unsupported' when one is dropped or stored as another, 'ungraceful' when a task with one is refused, the worse of the two where both apply; the extra keys say which.  Zimbra refuses CANCELLED.  The statuses RFC 10051 adds are save-load.todo.status.rfc10051.",
        "default": {"support": "full"},
        "extra_keys": {
            "values": "The statuses stored as sent.",
            "map": "A status, to the one stored instead.",
            "refused": "The statuses a task is refused with.",
        },
        "links": ["https://datatracker.ietf.org/doc/html/rfc5545#section-3.8.1.11"],
    },
    "save-load.todo.status.rfc10051": {
        "description": "The statuses RFC 10051 adds for a task, PENDING and FAILED, are stored as sent.  Graded and keyed as save-load.todo.status.  A profile stating save-load.todo.status states this one too: without it, this feature would answer with the parent's node, values and all.  A server not advertising RFC 10051 (supported-task-modes) need not keep them, and the behaviour says so: Ox drops both, Zimbra stores both as NEEDS-ACTION, and neither advertises it.  'unknown' by default, as for supported-task-modes: an optional extension is not assumed on a server not probed.",
        "default": {"support": "unknown"},
        "extra_keys": {
            "values": "The statuses stored as sent.",
            "map": "A status, to the one stored instead.",
            "refused": "The statuses a task is refused with.",
        },
        "links": ["https://datatracker.ietf.org/doc/html/rfc10051"],
    },
    "save-load.todo.priority": {
        "description": "Each PRIORITY from 0 to 9 (RFC 5545 section 3.8.1.9) is stored as sent.  PRIORITY:0 means no priority given, so a server dropping it changes nothing.  'unsupported' when one is dropped or stored as another, 'ungraceful' when a task with one is refused, the worse of the two where both apply; the extra keys say which, with the priorities as integers.  Ox keeps 1, 5 and 9 only: it stores 2 as 1, 0 and 3 to 6 as 5, and 7 and 8 as 9.",
        "default": {"support": "full"},
        "extra_keys": {
            "values": "The priorities stored as sent.",
            "map": "A priority, to the one stored instead.",
            "refused": "The priorities a task is refused with.",
        },
        "links": ["https://datatracker.ietf.org/doc/html/rfc5545#section-3.8.1.9"],
    },
    "supported-task-modes": {
        "description": "The server advertises the CALDAV:supported-task-mode-set property (RFC 10051 section 13.1) on the calendar home or a calendar, which is how it says it supports RFC 10051.  Without it a client must assume the server does not.  'unsupported' when neither has it (Ox, Zimbra); the extra key values lists the TASK-MODE values advertised.  'unknown' by default: few servers advertise it, but one not probed is not known to lack it.",
        "default": {"support": "unknown"},
        "extra_keys": {"values": "The TASK-MODE values the property lists."},
        "links": ["https://datatracker.ietf.org/doc/html/rfc10051#section-13.1"],
    },
    "save-load.icalendar.x-parameter": {
        "description": "An X- parameter (RFC 5545 section 3.2) on a standard property is stored with it, in an event and in a task.  'unsupported' when it is dropped (Ox and Zimbra drop SUMMARY's); the behaviour names the first component it was dropped from.",
        "default": {"support": "full"},
        "links": ["https://datatracker.ietf.org/doc/html/rfc5545#section-3.2"],
    },
    "multiget.deleted": {
        "description": "A calendar-multiget naming an object that was deleted answers it as missing, as a GET does.  'broken' when it still returns the object's data after the DELETE succeeded and a GET answers 404, so a client fetching by multiget sees deleted objects.",
        "default": {"support": "full"},
    },
    ## caldav PR 850 (https://github.com/python-caldav/caldav/pull/850)
    "save-load.icalendar.olson-tzid": {
        "description": "An object whose TZID names a time zone of the tz database (\"Olson\" names, such as Europe/Berlin) is placed in that time zone without a VTIMEZONE for it.  RFC 5545 section 3.6.5 requires the VTIMEZONE, so the default is 'unsupported', and a client should send one; where 'full', it may leave the VTIMEZONE out and keep the object small.  The probe stores an event with TZID=Asia/Tokyo and no VTIMEZONE and judges the server's reading by time-range searches in UTC.  'unsupported' when the time is read as UTC or another zone, 'ungraceful' when the event is refused.  A server reading the TZID as floating, in a time zone of its own, is caught unless it runs in Tokyo time (then 'full') or in UTC (then the behaviour says 'read as UTC').  The behaviour notes whether the server adds a VTIMEZONE of its own.  A client of the server may still misread a bare TZID where the server does not: Synology's web UI was reported to read it as UTC (https://github.com/python-caldav/caldav/issues/849).  See also save-load.icalendar.vtimezone, whether a VTIMEZONE sent is kept.",
        "default": {"support": "unsupported"},
        "links": ["https://datatracker.ietf.org/doc/html/rfc5545#section-3.6.5"],
    },
}


def register() -> list[str]:
    """Add the definitions caldav lacks to its feature tree; returns their names."""
    added = [name for name in FUTURE_FEATURES if name not in FeatureSet.FEATURES]
    for name in added:
        FeatureSet.FEATURES[name] = dict(FUTURE_FEATURES[name])
    if added:
        ## find_feature() and feature_tree() cache the tree: a parent looked up
        ## before now would never list its new child.
        for definition in FeatureSet.FEATURES.values():
            definition.pop("subfeatures", None)
        if "_feature_tree" in vars(FeatureSet):
            del FeatureSet._feature_tree
    return added
