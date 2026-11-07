"""A time-range search over what a RewritingServer stores, answered as
different servers answer it."""

import re
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock


def _time(block, name, hours):
    """The UTC time and the wall time, read as UTC, of property ``name``,
    ``hours`` ahead of UTC unless it ends in Z"""
    match = re.search(rf"\r\n{name}[^:\r\n]*:(\d{{8}}T\d{{6}})(Z?)\r\n", block)
    wall = datetime.strptime(match[1], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    return wall - timedelta(hours=0 if match[2] else hours), wall


def fake_search(server, offset=9, zoned=9, mode="overlap", inclusive=False):
    """A search reading a bare TZID as ``offset`` hours ahead of UTC, and one
    with its VTIMEZONE as ``zoned`` hours.  ``mode`` is how the range is
    matched: "overlap" as RFC 4791 section 9.9 has it, "dtstart" with only the
    start inside it (Robur), "index" overlapping both the time and the wall
    time read as UTC (Infomaniak).  ``inclusive`` finds an object in a range
    starting at its end (DAViCal, SOGo)."""

    def search(start, end, event=False, todo=False, **kwargs):
        component = "VTODO" if todo else "VEVENT"
        found = []
        for body in server.objects.values():
            if f"BEGIN:{component}" not in body:
                continue
            block = body[body.index(f"BEGIN:{component}") :]
            hours = zoned if "BEGIN:VTIMEZONE" in body else offset
            begin, wall_begin = _time(block, "DTSTART", hours)
            stop, wall_stop = _time(block, "DUE" if todo else "DTEND", hours)
            overlap = (start <= stop if inclusive else start < stop) and end > begin
            if mode == "dtstart":
                matched = start <= begin < end
            elif mode == "index":
                matched = overlap and end > wall_begin and start < wall_stop
            else:
                matched = overlap
            if matched:
                found.append(Mock(component={"UID": re.search(r"\r\nUID:(.*)\r\n", body)[1]}))
        return found

    return search
