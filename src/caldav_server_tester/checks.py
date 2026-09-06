import logging
import re
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import NamedTuple
from urllib.parse import quote, unquote, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from caldav.calendarobjectresource import Event, Journal, Todo, _quote_uid
from caldav.collection import Principal
from caldav.davobject import DAVObject
from caldav.elements import ical
from caldav.lib.error import AuthorizationError, DAVError, NotFoundError, PutError, ReportError
from caldav.lib.python_utilities import to_local
from caldav.search import CalDAVSearcher

from .checks_base import Check

utc = timezone.utc

## cal_id under which PrepareCalendar creates its test calendar (and, with the
## _tasks/_journals suffixes, the dedicated task/journal calendars).  Single
## source of truth: checker.cleanup_test_data() needs the same id to find
## leftovers without re-running the checks.
TEST_CALENDAR_CAL_ID = "caldav-server-checker-calendar"

## UIDs of the two probe objects PrepareCalendar deliberately PUTs far in the
## past (year 2000) to detect sliding-window servers.  Referenced by the
## old-date / unlimited-time-range checks here and by the cleanup fallback in
## checker.py (ServerQuirkChecker.HIDDEN_PROBE_UIDS).
OLDDATE_EVENT_UID = "csc_olddate_event"
OLDDATE_TASK_UID = "csc_olddate_task"

## UIDs of the url.encode-at probe objects.  The '@' has to be in the UID rather
## than relying on one in the username: it is the only way to guarantee the
## object's path contains an '@' on every server.  PrepareCalendar._check_encode_at
## PUTs these objects itself rather than going through save_object(), which would
## quote the '@' away.  Both keep the csc_ prefix so the cleanup sweep finds them.
##
## The second one exists only for the identity axis of the probe: it is PUT at
## the '%40' spelling of the *first* one's path, so that re-reading the literal
## path says whether the two spellings named one resource or two.  It carries a
## UID of its own rather than reusing the first, because a server that enforces
## UID uniqueness within a collection would reject the duplicate and the probe
## would learn nothing.  It is removed again as soon as the question is answered.
ENCODE_AT_UID = "csc_encode_at@example.com"
ENCODE_AT_ALIAS_UID = "csc_encode_at_alias@example.com"

## The collection axis of the same probe.  ``@`` in a *calendar id* rather than
## in an object name: the ownCloud/Nextcloud shape the feature exists for is a
## calendar-home-set (/remote.php/dav/calendars/user@example.com/), not an
## object file name, and a server may route a username-bearing collection
## segment quite differently from an .ics.  The probe calendars are created and
## deleted within the run; the csc_ prefix keeps them inside
## checker.PROBE_CALENDAR_PREFIXES so a sweep finds any that survive a crash.
##
## The object PUT inside them carries no '@' of its own - it is there only to
## give the two collections something to hold apart, and an '@' in its name too
## would confound the two axes.
ENCODE_AT_CAL_ID = "csc_encode_at_cal@example.com"
ENCODE_AT_IN_COLLECTION_UID = "csc_encode_at_in_collection"

## Every UID the encode-at probes PUT.  ``_resolved_uid`` matches the body
## against all of them: which one came back is the whole signal, and a UID it
## does not know about would read as "this URL reached nothing".
ENCODE_AT_PROBE_UIDS = (ENCODE_AT_UID, ENCODE_AT_ALIAS_UID, ENCODE_AT_IN_COLLECTION_UID)

## Bodies for the requests the encode-at probes issue directly rather than
## through the library, which would normalise the spelling they are about.
## Deliberately no DAV:displayname: a display name relocates the collection on
## some servers (Zimbra, OX), and this probe is about the URL it was asked for.
ENCODE_AT_MKCALENDAR_BODY = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<C:mkcalendar xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">'
    "<D:set><D:prop/></D:set>"
    "</C:mkcalendar>"
)
ENCODE_AT_MKCOL_BODY = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<D:mkcol xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">'
    "<D:set><D:prop><D:resourcetype><D:collection/><C:calendar/></D:resourcetype></D:prop></D:set>"
    "</D:mkcol>"
)
ENCODE_AT_PROPFIND_BODY = (
    '<?xml version="1.0" encoding="utf-8"?><D:propfind xmlns:D="DAV:"><D:prop><D:displayname/></D:prop></D:propfind>'
)


class EncodeAtObservation(NamedTuple):
    """What one axis of the ``url.encode-at`` probe managed to observe.

    Three axes ask the same question of three different things a path can
    embed an ``@``: an object name, a calendar id, and the username inside a
    principal or calendar-home-set path.  They share the three subfeatures
    rather than getting keys of their own - the client acts on one verdict per
    subfeature, and inventing ``url.encode-at.identity.collection`` before any
    server has been seen to differ between the axes would be modelling a
    distinction nobody has observed.

    A field left ``None`` is a question this axis could not answer, and stays
    out of the merge entirely: an axis that saw nothing must not outvote one
    that saw something.  ``behaviour`` is recorded either way, so a profile
    read by a human says what happened even where nothing could be graded.
    """

    axis: str
    behaviour: str
    literal: str | None = None
    encoded: str | None = None
    identity: str | None = None
    key: str = ""


def _object_axis(behaviour, literal=None, encoded=None, identity=None) -> EncodeAtObservation:
    """An observation about an '@' in an object name."""
    return EncodeAtObservation("object paths", behaviour, literal, encoded, identity, "object")


def _collection_axis(behaviour, literal=None, encoded=None, identity=None) -> EncodeAtObservation:
    """An observation about an '@' in a calendar id."""
    return EncodeAtObservation("calendar paths", behaviour, literal, encoded, identity, "collection")


def _principal_axis(behaviour, literal=None, encoded=None, identity=None) -> EncodeAtObservation:
    """An observation about an '@' in the username, as a server-given path spells it."""
    return EncodeAtObservation("principal paths", behaviour, literal, encoded, identity, "principal")


def url_object(cal, uid, obj_class=None):
    """Construct a calendar object bound to its direct ``<uid>.ics`` URL.

    Several checks reach an object by the URL the client PUT it at
    (``cal.url`` joined with ``uid`` + ``.ics``) instead of via search/REPORT —
    e.g. to load objects a sliding window hides from listings, or to probe a
    non-existent resource.  This centralises that construction so the ``.ics``
    URL convention lives in exactly one place.  ``obj_class`` defaults to
    ``Event``, resolved at call time so tests can patch ``checks.Event``.
    """
    if obj_class is None:
        obj_class = Event
    ## Quote the UID exactly as the library does when it PUTs an object
    ## (_generate_url -> _quote_uid), or this "direct URL" addresses a resource
    ## that is not there for any UID containing a character needing escaping.
    return obj_class(cal.client, url=cal.url.join(_quote_uid(uid) + ".ics"), parent=cal)


def resolve_cu_address(principal):
    """Resolve a principal's calendar-user address for the scheduling probes.

    Prefers the calendar-user-address-set value (``get_vcal_address``); falls
    back to a ``mailto:`` built from the account username when that looks like an
    email address.  Returns ``None`` when neither yields a usable address.  The
    three RFC6638 scheduling checks all need this same resolution for the
    attendee principal.
    """
    try:
        return principal.get_vcal_address()
    except Exception:
        username = getattr(principal.client, "username", None)
        return "mailto:" + username if username and "@" in str(username) else None


def _base_year(now=None):
    """The calendar year the reusable test fixtures live in: *next* year.

    Historically all fixtures were placed in year 2000 to avoid clashing with
    real calendar content when the only available calendar is a production one.
    That backfires on servers that restrict the search window: OX App Suite, for
    instance, only exposes objects within roughly ``[now - 1 month, now + 18
    months]`` and silently hides everything outside it (verified empirically) -
    so the year-2000 fixtures are invisible to search/REPORT there and every
    fixture-dependent check misfires.

    Placing the fixtures in the *next calendar year* keeps them in the
    near future: at most ~14 months ahead (a January run), comfortably inside
    OX's window, and always in the future so OX's past-edge never hides a fresh
    fixture.  The base year is stable within a calendar year (it only changes on
    Jan 1), which keeps the ``--no-cleanup`` reuse feature effective.

    A few checks deliberately keep year-2000 *probe* objects to detect exactly
    this sliding-window behaviour (see ``csc_olddate_*`` and
    ``search.unlimited-time-range`` / ``search.time-range.*.old-dates``).
    """
    now = now or datetime.now(tz=utc)
    return now.year + 1


def _filter_fixture_window(objects, base_year):
    """Keep only objects whose (start/due/end) date falls in the fixture year.

    The fixtures all live within ``base_year`` (see :func:`_base_year`); this
    filters a calendar listing down to the reusable test set, excluding both
    unrelated real content and the year-2000 old-date probes.
    """

    def asdate(foo):
        ## datetime is a subclass of date, so we use exact type check to exclude datetime
        return foo if isinstance(foo, date) and not isinstance(foo, datetime) else foo.date()

    def dt(obj):
        """a datetime from the object, if applicable, otherwise 1980"""
        x = obj.component
        if "dtstart" in x:
            return x.start
        if "due" in x or "dtend" in x:
            return x.end
        return date(1980, 1, 1)

    def d(obj):
        return asdate(dt(obj))

    return (x for x in objects if date(base_year, 1, 1) <= d(x) <= date(base_year + 1, 1, 1))


class CheckGetCurrentUserPrincipal(Check):
    """
    Checks support for get-current-user-principal
    """

    features_to_be_checked = {"get-current-user-principal"}
    depends_on = set()

    def _run_check(self):
        ## ServerQuirkChecker.__init__ already fetched a working principal, so a
        ## failure when we re-fetch here is almost certainly transient (503, TLS,
        ## ConnectionError), not "unsupported".  Retry once; if it still fails,
        ## keep the principal we already have (so downstream checks aren't
        ## crippled by a momentary blip), mark the feature "unknown" with a note,
        ## and warn on STDERR.
        last_exc = None
        for _attempt in range(2):
            try:
                self.checker.principal = self.client.principal()
                last_exc = None
                break
            except AssertionError:
                raise
            except Exception as exc:
                last_exc = exc
        if last_exc is not None:
            logging.warning(
                "current-user-principal lookup failed (%s); the initial connection succeeded, "
                "so this is most likely a transient error — marking get-current-user-principal "
                "as unknown rather than unsupported.",
                last_exc,
            )
            self.set_feature(
                "get-current-user-principal",
                {"support": "unknown", "behaviour": f"lookup failed, possibly a transient error: {last_exc}"},
            )
            return self.checker.principal

        ## Verify the URL the server returned is actually reachable.
        ## Some servers (observed on Infomaniak/kSuite sabre/dav 4.3.1) advertise
        ## a principal URL via current-user-principal but that URL itself 404s,
        ## making the feature useless.
        principal_url_broken = False
        try:
            response = self.client.propfind(
                self.checker.principal.url,
                props='<?xml version="1.0"?><D:propfind xmlns:D="DAV:"><D:prop><D:resourcetype/></D:prop></D:propfind>',
            )
            if response.status == 404:
                principal_url_broken = True
        except NotFoundError:
            principal_url_broken = True
        if principal_url_broken:
            logging.warning(
                "Server returned current-user-principal URL %s but PROPFIND on it returns 404. "
                "This is a server bug that violates RFC5397 — the advertised principal URL does not exist. "
                "All checks that require a working principal will be skipped.",
                self.checker.principal.url,
            )
            self.set_feature(
                "get-current-user-principal",
                {"support": "broken", "behaviour": "server returns an inaccessible principal URL"},
            )
            self.checker.principal = None
            return self.checker.principal

        self.set_feature("get-current-user-principal")
        return self.checker.principal


class CheckWellKnown(Check):
    """Check that /.well-known/caldav discovery works per RFC 6764 section 5.

    Makes a GET to https://host/.well-known/caldav (without following redirects)
    and verifies the server responds with a 3xx redirect or 200 OK.

    Marked unknown for localhost/test servers where well-known is not applicable.
    """

    features_to_be_checked = {"well-known"}
    depends_on = set()

    def _run_check(self) -> None:
        import urllib.parse

        parsed = urllib.parse.urlparse(str(self.client.url))
        host = parsed.hostname or ""

        if host in ("localhost", "127.0.0.1", "::1") or not host:
            self.set_feature("well-known", {"support": "unknown", "behaviour": "localhost — well-known not applicable"})
            return

        scheme = parsed.scheme or "https"
        port = parsed.port
        if port and ((scheme == "https" and port != 443) or (scheme == "http" and port != 80)):
            base = f"{scheme}://{host}:{port}"
        else:
            base = f"{scheme}://{host}"
        well_known_url = f"{base}/.well-known/caldav"

        try:
            response = self.client.session.get(well_known_url, allow_redirects=False, timeout=10)
        except Exception as e:
            self.set_feature("well-known", {"support": "unknown", "behaviour": f"request failed: {e}"})
            return

        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("Location", "")
            self.set_feature("well-known", {"support": "full", "behaviour": f"redirects to {location}"})
        elif response.status_code == 200:
            self.set_feature(
                "well-known", {"support": "full", "behaviour": "returns 200 OK directly at /.well-known/caldav"}
            )
        elif response.status_code == 404:
            self.set_feature("well-known", False)
        else:
            self.set_feature(
                "well-known",
                {"support": "unknown", "behaviour": f"unexpected status {response.status_code}"},
            )


class CheckPropfindAllprop(Check):
    """
    Checks the PROPFIND method on three independent levels:
      * propfind                       - a PROPFIND for a named property returns
                                         a multistatus response
      * propfind.allprop               - an <D:allprop/> PROPFIND returns a
                                         multistatus response
      * propfind.allprop.resourcetype  - that allprop response includes the
                                         DAV:resourcetype live property

    RFC4918 section 9.1 lists resourcetype among the live properties an allprop
    request should return; a few servers (e.g. Bedework) omit it.  The three are
    probed separately so that a server which merely omits resourcetype is not
    mistaken for one that does not support PROPFIND at all.  Replaces the old
    'propfind_allprop_failure' flag.
    """

    depends_on = {CheckGetCurrentUserPrincipal}
    features_to_be_checked = {"propfind", "propfind.allprop", "propfind.allprop.resourcetype"}

    XML_HEAD = '<?xml version="1.0" encoding="UTF-8"?>'

    def _run_check(self) -> None:
        principal = getattr(self.checker, "principal", None)
        ## Fall back to the root URL when the principal URL is broken/unavailable.
        ## We know root supports PROPFIND because that is where current-user-principal
        ## was fetched from (or the auth check happened).
        url = principal.url if principal is not None else self.client.url

        ## propfind: a PROPFIND for a named property returns a multistatus
        named = self._propfind(
            url,
            f'{self.XML_HEAD}<D:propfind xmlns:D="DAV:"><D:prop><D:resourcetype/></D:prop></D:propfind>',
        )
        self.set_feature("propfind", named is not None and "multistatus" in named)

        ## propfind.allprop: an <D:allprop/> PROPFIND returns a multistatus
        allprop = self._propfind(
            url,
            f'{self.XML_HEAD}<D:propfind xmlns:D="DAV:"><D:allprop/></D:propfind>',
        )
        if allprop is None:
            self.set_feature("propfind.allprop", False)
            self.set_feature("propfind.allprop.resourcetype", False)
            return
        self.set_feature("propfind.allprop", "multistatus" in allprop)
        ## propfind.allprop.resourcetype: that allprop response includes resourcetype
        self.set_feature("propfind.allprop.resourcetype", "resourcetype" in allprop)

    def _propfind(self, url, body):
        """Return the decoded raw response of a PROPFIND, or None on error."""
        try:
            response = self.client.propfind(url, props=body)
        except (DAVError, AuthorizationError):
            return None
        return to_local(response.raw)


class CheckMakeDeleteCalendar(Check):
    """
    Checks (relatively) thoroughly that it's possible to create a calendar and delete it
    """

    features_to_be_checked = {
        "get-current-user-principal.has-calendar",
        "create-calendar.auto",
        "create-calendar",
        "create-calendar.set-displayname",
        "create-calendar.stable-url",
        "propfind.displayname",
        "delete-calendar",
        "delete-calendar.free-namespace",
    }
    depends_on = {CheckGetCurrentUserPrincipal}

    ## The cal_id the make/delete probe uses.  Prefixed so the cleanup sweep
    ## recognises it (see checker.PROBE_CALENDAR_PREFIXES).
    MKDEL_CAL_ID = "caldav-server-checker-mkdel-test"

    @staticmethod
    def _calendar_is_accessible(cal) -> bool:
        """Probe whether a calendar is accessible by calling events().

        Returns True if events() succeeds, False if the server returns any DAV
        error (404 Not Found, 403 Forbidden, 500 Internal Server Error, etc.).
        """
        try:
            cal.events()
            return True
        except DAVError:
            return False

    def _poll_calendar_accessible(self, cal_id, timeout=10):
        """Poll until the calendar at ``cal_id`` answers ``events()``.

        Returns ``(cal_or_None, waited_seconds)``.  Some servers
        (Infomaniak/SabreDAV) create calendars *asynchronously*: MKCALENDAR
        returns before the collection is queryable, 404ing for a few seconds.
        Anything that creates a calendar and immediately uses it must wait here,
        or it both mis-probes the calendar AND leaks it (it does get created
        server-side; we just never wait around to use or delete it).
        """
        cal = self.checker.principal.calendar(cal_id=cal_id)
        waited = 0
        while not self._calendar_is_accessible(cal) and waited < timeout:
            time.sleep(1)
            waited += 1
            cal = self.checker.principal.calendar(cal_id=cal_id)
        return (cal if self._calendar_is_accessible(cal) else None), waited

    def _try_make_calendar(self, cal_id, **kwargs):
        """
        Does some attempts on creating and deleting calendars, and sets some
        flags - while others should be set by the caller.

        Note: this probe deliberately does NOT set a display name.  The
        display-name behaviour (and its coupling to the calendar URL on some
        servers) is checked separately and deterministically in
        _check_set_displayname(), so that a display-name-triggered calendar
        relocation cannot pollute the create/delete/free-namespace detection
        here.
        """
        calmade = False

        ## In case calendar already exists ... wipe it first
        try:
            self.checker.principal.calendar(cal_id=cal_id).delete()
        except Exception:
            pass

        ## create the calendar
        try:
            cal = self.checker.principal.make_calendar(cal_id=cal_id, **kwargs)
        except DAVError:
            ## calendar creation created an exception.  Maybe the calendar exists?
            cal = self.checker.principal.calendar(cal_id=cal_id)
            if not self._calendar_is_accessible(cal):
                cal = None
            if not cal:
                ## cal not made and does not exist, exception thrown.
                return False
        else:
            ## MKCALENDAR returned without error, but we must be sure the
            ## collection is actually usable - poll for it (handles async creation,
            ## see _poll_calendar_accessible) rather than concluding creation
            ## failed on the first 404.
            cal, waited = self._poll_calendar_accessible(cal_id)
            if cal is None:
                return False
            calmade = True
            if waited:
                ## Supported, but the client must poll/wait for the calendar to
                ## materialise — a quirk (is_supported(bool) stays True so the
                ## downstream checks still run), not plain "full".
                self.set_feature(
                    "create-calendar",
                    {"support": "quirk", "behaviour": f"delayed creation (not queryable until ~{waited}s)"},
                )
            else:
                self.set_feature("create-calendar")

        assert cal

        try:
            ## Use DAVObject.delete directly to bypass Calendar.delete()
            ## workarounds - we want to test the server's raw DELETE behavior
            DAVObject.delete(cal)
            cal = self.checker.principal.calendar(cal_id=cal_id)
            if not self._calendar_is_accessible(cal):
                cal = None
            ## Delete throw no exceptions, but was the calendar deleted?
            if not cal or self.checker.features_checked.is_supported("create-calendar.auto"):
                self.set_feature("delete-calendar")
                ## Calendar probably deleted OK.
                ## (in the case of non_existing_calendar_found, we should add
                ## some events to the calendar, delete the calendar and make
                ## sure no events are found on a new calendar with same ID)
            else:
                ## Calendar not deleted yet.  The server may delete it
                ## asynchronously, so poll for up to ~10s rather than flatly
                ## sleeping the whole time — most async deletes finish well under
                ## that, and the final verdict is the same either way.
                cal = self.checker.principal.calendar(cal_id=cal_id)
                waited = 0
                while self._calendar_is_accessible(cal) and waited < 10:
                    time.sleep(1)
                    waited += 1
                    cal = self.checker.principal.calendar(cal_id=cal_id)
                if self._calendar_is_accessible(cal):
                    ## Calendar not deleted, but no exception thrown.
                    ## Perhaps it's a "move to thrashbin"-regime on the server
                    self.set_feature(
                        "delete-calendar",
                        {"support": "unknown", "behaviour": "move to trashbin?"},
                    )
                else:
                    ## Calendar was deleted, it just took some time.
                    self.set_feature(
                        "delete-calendar",
                        {"support": "fragile", "behaviour": "delayed deletion"},
                    )
                    return calmade
            return calmade
        except DAVError as e:
            time.sleep(10)
            try:
                DAVObject.delete(cal)
                self.set_feature(
                    "delete-calendar",
                    {
                        "support": "fragile",
                        "behaviour": "deleting a recently created calendar causes exception",
                    },
                )
            except DAVError as e2:
                self.set_feature("delete-calendar", False)
            return calmade

    def _run_check(self):
        self._probe_make_delete()
        ## The set-displayname behaviour can only be probed by creating a
        ## calendar with a name, so it is gated on create-calendar; when that is
        ## unsupported, the two set-displayname* features collapse under the
        ## create-calendar status.  But propfind.displayname is an ordinary
        ## PROPFIND that works on any existing calendar and has no checked parent
        ## to collapse under, so we must always probe it - against the freshly
        ## created probe calendar when we can, against an existing one otherwise.
        if self.checker.features_checked.is_supported("create-calendar"):
            self._check_set_displayname()
        else:
            existing = self._find_existing_calendar()
            if existing is not None:
                self._probe_propfind_displayname(existing)
            else:
                self.set_feature(
                    "propfind.displayname",
                    {"support": "unknown", "behaviour": "no calendar available to probe"},
                )

    def _probe_free_namespace(self, cal_id, timeout=5, method=None):
        """Did the DELETE free up the calendar id for re-use?

        Called with a ``cal_id`` that ``_try_make_calendar`` has just created and
        deleted: re-creating it now answers the question inside this run.  A
        server that keeps the name reserved - Nextcloud moves the calendar to a
        trashbin that has to be emptied by hand - refuses that MKCALENDAR while
        still accepting a fresh id, and that difference is the whole signal.  So
        a failure is only reported as "not freed" when a fresh id succeeds; when
        neither works the server is refusing to create calendars for some
        unrelated reason and the feature is left unknown rather than blamed on
        the namespace.

        ``method`` is whatever created the calendar in the first place, and both
        the retry and the fresh-id control have to use it: a server that only
        answers MKCOL would otherwise be asked with MKCALENDAR here, fail for
        that reason alone, and be recorded as keeping the name reserved.
        """
        kwargs = {} if method is None else {"method": method}
        feature = "delete-calendar.free-namespace"
        if not self.checker.features_checked.is_supported("delete-calendar"):
            self.set_feature(feature, {"support": "unknown", "behaviour": "cannot test, delete-calendar not supported"})
            return

        waited = 0
        while True:
            try:
                self.checker.principal.make_calendar(cal_id=cal_id, **kwargs)
            except DAVError as e:
                last_error = e
            else:
                ## Freed - immediately, or after a short wait on a server that
                ## processes the delete asynchronously.  Either way the client
                ## can re-use the id.
                self.set_feature(feature, True)
                self._discard_calendar(cal_id)
                return
            if waited >= timeout:
                break
            time.sleep(1)
            waited += 1

        fresh_id = "testcalendar-" + str(uuid.uuid4())
        try:
            self.checker.principal.make_calendar(cal_id=fresh_id, **kwargs)
        except DAVError:
            self.set_feature(
                feature,
                {
                    "support": "unknown",
                    "behaviour": f"cannot test, creating a calendar failed for both the deleted and a fresh cal_id ({last_error})",
                },
            )
        else:
            self._discard_calendar(fresh_id)
            self.set_feature(feature, False)

    def _discard_calendar(self, cal_id):
        """Throw away a calendar that was created only to answer a probe."""
        try:
            DAVObject.delete(self.checker.principal.calendar(cal_id=cal_id))
        except Exception:
            pass

    def _probe_make_delete(self):
        try:
            cal = self.checker.principal.calendar(cal_id="this_should_not_exist")
            cal.events()
            self.set_feature("create-calendar.auto")
        except DAVError:
            ## Any DAV-level error (404 NotFound, 403 robur, ReportError, or the
            ## 500 some servers return) just means the non-existent calendar was
            ## not auto-created.  Catch the DAVError base class so a 500 doesn't
            ## escape and abort the rest of the make/delete probing below.
            self.set_feature("create-calendar.auto", False)

        ## Check on "no_default_calendar" flag
        try:
            cals = self.checker.principal.calendars()
            calname = cals[0].get_display_name()
            self.set_feature("get-current-user-principal.has-calendar", True)
        except Exception:
            self.set_feature("get-current-user-principal.has-calendar", False)

        _unknown_del = {"support": "unknown", "behaviour": "cannot test, delete-calendar not supported"}
        ## _try_make_calendar creates the fixed cal_id and deletes it again, so
        ## re-creating that same id afterwards is what tells us whether the
        ## delete freed the namespace - see _probe_free_namespace.  (This used to
        ## be inferred from whether the fixed id could be created at all, i.e.
        ## from whether a *previous* run's leftover had been freed: no evidence
        ## at all on a first run, and any transient MKCALENDAR failure was
        ## recorded as "the namespace is not freed on delete".)
        makeret = self._try_make_calendar(cal_id=self.MKDEL_CAL_ID)
        if makeret:
            self._probe_free_namespace(self.MKDEL_CAL_ID)
            return
        ## The fixed cal_id could not be created at all; a fresh unique cal_id
        ## tells us whether calendar creation works in the first place.
        unique_id = "testcalendar-" + str(uuid.uuid4())
        makeret = self._try_make_calendar(cal_id=unique_id)
        if makeret:
            ## Creation works, so the fixed id is what the server objects to.
            ## Do NOT ask the namespace question about that id: this run never
            ## created it, so it never deleted it either, and re-creating it
            ## now only reports whether a *previous* run's leftover has been
            ## freed - the very inference this probe exists to stop making.
            ## The fresh id, on the other hand, was just created and deleted by
            ## _try_make_calendar, so it can answer the question properly.
            self._probe_free_namespace(unique_id)
            return
        makeret = self._try_make_calendar(cal_id=unique_id, method="mkcol")
        if makeret:
            self.set_feature("create-calendar", {"support": "quirk", "behaviour": "mkcol-required"})
            ## The fixed cal_id has only ever been tried with MKCALENDAR, which
            ## on this server fails by construction - so its failure says
            ## nothing about the namespace.  Create and delete it with MKCOL
            ## and ask the question properly.
            if not self.checker.features_checked.is_supported("delete-calendar"):
                self.set_feature("delete-calendar.free-namespace", _unknown_del)
            elif self._try_make_calendar(cal_id=self.MKDEL_CAL_ID, method="mkcol"):
                self._probe_free_namespace(self.MKDEL_CAL_ID, method="mkcol")
            else:
                self.set_feature(
                    "delete-calendar.free-namespace",
                    {
                        "support": "unknown",
                        "behaviour": "cannot test, the probe cal_id could not be created with MKCOL either",
                    },
                )
        else:
            self.set_feature("create-calendar", False)
            self.set_feature(
                "delete-calendar", {"support": "unknown", "behaviour": "cannot test, create-calendar not supported"}
            )
            self.set_feature(
                "delete-calendar.free-namespace",
                {"support": "unknown", "behaviour": "cannot test, create-calendar not supported"},
            )

    def _displayname_verdict_is_measurable(self):
        """Whether a "did the display name stick?" verdict means anything here.

        The probe answers that question by reading DAV:displayname back off the
        server's calendar listing.  On a server that does not serve the property
        (propfind.displayname unsupported or broken) no name can ever match, so
        a "did not stick" reading says nothing about set-displayname support -
        it only restates that the property is unreadable.
        """
        return self.checker._features_checked.is_supported("propfind.displayname", str) in ("full", "quirk")

    def _probe_propfind_displayname(self, cal):
        """Probe ``propfind.displayname`` against an existing calendar collection.

        Reading the ``DAV:displayname`` property is an ordinary PROPFIND on any
        collection - it does not require us to have created the calendar - so we
        can determine this even on servers that refuse calendar creation.
        """
        try:
            fetched = cal.get_display_name()
        except Exception:
            self.set_feature("propfind.displayname", False)
            return
        if fetched is not None:
            self.set_feature("propfind.displayname", True)
        else:
            self.set_feature(
                "propfind.displayname",
                {"support": "broken", "behaviour": "PROPFIND returns no displayname value"},
            )

    def _find_existing_calendar(self):
        """Return some existing calendar to probe read-only properties against.

        Prefer a user-configured test calendar (``--caldav-calendar``, exposed as
        the ``test-calendar.compatibility-tests`` name); otherwise fall back to
        the first calendar the principal exposes.  Returns None if none is found.
        """
        cal_info = self.checker.expected_features.is_supported("test-calendar.compatibility-tests", dict)
        name = cal_info.get("name") if isinstance(cal_info, dict) else None
        if name:
            try:
                cal = self.checker.principal.calendar(name=name)
                cal.get_display_name()  ## force a request so a missing calendar raises
                return cal
            except Exception:
                pass
        try:
            cals = self.checker.principal.calendars()
        except Exception:
            return None
        return cals[0] if cals else None

    def _check_set_displayname(self):
        """Deterministically probe display-name support and calendar-URL stability.

        Three things are checked:

        * ``create-calendar.set-displayname`` - does a display name given at
          creation time stick?
        * ``create-calendar.stable-url`` - after the calendar is created, is it
          still addressable at the *requested* cal_id URL, or did the server
          assign a different canonical URL?  We create with a UNIQUE display name,
          look the calendar up by that name to get its canonical URL, and compare
          the canonical URL's final path segment to the requested cal_id:

            - segment == cal_id  -> URL stable      (supported)
            - segment != cal_id  -> URL not stable  (unsupported)

          The same server-agnostic comparison covers both known offenders with no
          special-casing: Zimbra relocates the collection to a display-name-derived
          path, and OX hands out an opaque ``cal://0/NNN`` (base64-segment)
          canonical URL.  In both cases the canonical segment differs from the
          requested cal_id, so both report ``unsupported`` and the consuming
          library is expected to adopt the canonical URL after creation.
        * ``propfind.displayname`` - can DAV:displayname be read back via PROPFIND?

        Zimbra quirks deliberately NOT probed here (each could merit its own
        dedicated probe one day; mirrored in the ``zimbra`` entry of caldav's
        ``compatibility_hints.py``):

        * The calendar URL only becomes unstable when a display name is supplied
          at creation - a nameless MKCALENDAR stays put at the requested cal_id.
          That is *why* this probe must create *with* a name to observe the
          instability at all.
        * Even when the calendar *collection* is reachable at the requested cal_id
          (Zimbra keeps a collection-level alias there, answering PROPFIND/REPORT),
          child object resources under it are NOT: a GET on
          ``.../<cal_id>/<uid>.ics`` 404s while the object is only retrievable
          under the canonical relocated URL.  So collection reachability at the
          cal_id is a misleading signal for URL stability - we compare canonical
          URLs instead, and the library must re-point to the canonical URL so that
          later object operations resolve.
        """
        cal_id = "caldav-server-checker-displayname-test"
        unique_name = "csc-displayname-" + str(uuid.uuid4())

        def _segment(cal):
            return str(cal.url).rstrip("/").rsplit("/", 1)[-1]

        def _find_by_displayname(name):
            try:
                cals = self.checker.principal.calendars()
            except Exception:
                return None
            for c in cals:
                try:
                    if c.get_display_name() == name:
                        return c
                except Exception:
                    pass
            return None

        def _cleanup():
            try:
                self.checker.principal.calendar(cal_id=cal_id).delete()
            except Exception:
                pass
            leftover = _find_by_displayname(unique_name)
            if leftover is not None:
                try:
                    leftover.delete()
                except Exception:
                    pass

        ## clean slate
        _cleanup()

        def _fallback_displayname(reason):
            ## We can't probe the set-displayname behaviour (which requires
            ## creating a usable calendar with a name) - those two features
            ## collapse under the parent create-calendar status.  But reading
            ## DAV:displayname is an ordinary PROPFIND, so we can still probe
            ## propfind.displayname against an existing calendar.
            ## (propfind.displayname has no checked parent, so it must be set
            ## explicitly either way, or the post-check assertion in run_check()
            ## trips on it.)
            unknown = {"support": "unknown", "behaviour": reason}
            self.set_feature("create-calendar.set-displayname", unknown)
            self.set_feature("create-calendar.stable-url", unknown)
            existing = self._find_existing_calendar()
            if existing is not None:
                self._probe_propfind_displayname(existing)
            else:
                self.set_feature("propfind.displayname", unknown)

        try:
            self.checker.principal.make_calendar(cal_id=cal_id, name=unique_name)
        except DAVError:
            ## Couldn't create the probe calendar (e.g. a server that needs mkcol
            ## or refuses a name= at creation).
            _fallback_displayname("could not create probe calendar")
            return

        ## Wait for the calendar to materialise before reading anything off it
        ## (creation may be async, see _poll_calendar_accessible).  On Zimbra/OX
        ## the requested cal_id resolves as a collection-level alias, so this poll
        ## returns quickly even when the *canonical* URL is elsewhere.
        probe_cal, _ = self._poll_calendar_accessible(cal_id)

        ## Locate the calendar by the unique display name to discover its
        ## *canonical* URL - this is where the server really put it, and the only
        ## reliable signal for URL stability (the cal_id alias is not, see the
        ## docstring's Zimbra quirks).
        located = _find_by_displayname(unique_name)

        if located is None:
            ## The name was not found under any calendar.  Either creation never
            ## materialised, or the display name did not stick.
            if probe_cal is None:
                _fallback_displayname("probe calendar did not become queryable")
                _cleanup()
                return
            ## cal_id is reachable but the name is not retained: set-displayname
            ## is unsupported.  No relocation was triggered (Zimbra/OX only move
            ## the URL when a name sticks), so the calendar sits at the requested
            ## cal_id - the URL is stable.
            self._probe_propfind_displayname(probe_cal)
            if self._displayname_verdict_is_measurable():
                self.set_feature("create-calendar.set-displayname", False)
            else:
                ## The name could not be read back at all, so "it did not stick"
                ## is not something this probe actually observed.
                self.set_feature(
                    "create-calendar.set-displayname",
                    {
                        "support": "unknown",
                        "behaviour": "DAV:displayname is not readable on this server, so the name could not be verified",
                    },
                )
            self.set_feature("create-calendar.stable-url", True)
            _cleanup()
            return

        try:
            ## The name stuck.  Read DAV:displayname off the located calendar and
            ## decide stability purely by comparing its canonical URL segment to
            ## the requested cal_id - identical handling for stable servers,
            ## Zimbra (display-name-derived segment) and OX (opaque base64 segment).
            self._probe_propfind_displayname(located)
            self.set_feature("create-calendar.set-displayname", True)
            if _segment(located) == cal_id:
                self.set_feature("create-calendar.stable-url", True)
            else:
                self.set_feature(
                    "create-calendar.stable-url",
                    {
                        "support": "unsupported",
                        "behaviour": (
                            f"the created calendar's canonical URL segment is "
                            f"{_segment(located)!r}, not the requested {cal_id!r}; "
                            "clients must adopt the canonical URL after creation"
                        ),
                    },
                )
        finally:
            _cleanup()


class PrepareCalendar(Check):
    """
    This "check" doesn't check anything, but ensures the calendar has some known events
    """

    depends_on = {CheckMakeDeleteCalendar}
    features_to_be_checked = {
        "save-load.event.recurrences",
        "save-load.event.recurrences.count",
        "save-load.event.recurrences.exception",
        "save-load.todo.recurrences",
        "save-load.todo.recurrences.count",
        "save-load.event",
        "save-load.todo",
        "save-load.todo.mixed-calendar",
        "save-load.journal",
        "save-load.journal.mixed-calendar",
        "save-load.get-by-url",
        "save-load.stable-url",
        "url.encode-at.identity",
        "url.encode-at.literal.object",
        "url.encode-at.literal.collection",
        "url.encode-at.literal.principal",
        "url.encode-at.encoded",
    }

    def _find_or_create_calendar(self, cal_id, name, test_cal_info):
        """Find or create the main test calendar; set up self.checker.calendar.

        Records ``checker.calendar_was_created`` so cleanup() knows whether it may
        delete the whole calendar.  A calendar found by user-supplied display name
        (``--caldav-calendar``) is a real user calendar we must never delete; a
        calendar found under our own ``cal_id`` is a leftover from a previous run
        that we own, and a freshly created calendar is ours too.
        """
        user_specified = "name" in test_cal_info
        try:
            if user_specified:
                calendar = self.checker.principal.calendar(name=name)
            else:
                calendar = self.checker.principal.calendar(cal_id=cal_id)
            ## At least one out of those two will raise NotFoundError if calendar doesn't exist
            calendar.get_display_name()
            calendar.events()
            ## Found an existing calendar: we own it (safe to delete) only when it
            ## lives under our cal_id, not when the user pointed us at it by name.
            self.checker.calendar_was_created = not user_specified
        except Exception:
            if not self.checker.features_checked.is_supported("create-calendar"):
                raise RuntimeError(
                    "Server does not support calendar creation and no existing test calendar was found. "
                    "Specify a calendar to use with --caldav-calendar <display-name>."
                )
            calendar = self.checker.principal.make_calendar(cal_id=cal_id, name=name)
            self.checker.calendar_was_created = True
            ## Some servers (Infomaniak/SabreDAV) create calendars asynchronously:
            ## the collection 404s for a few seconds after MKCALENDAR returns.
            ## Wait for it to materialise before the fixture-loading checks start
            ## using it (CheckMakeDeleteCalendar already detected this and marked
            ## create-calendar as a "delayed creation" quirk).
            waited = 0
            while waited < 10:
                try:
                    calendar.events()
                    break
                except DAVError:
                    time.sleep(1)
                    waited += 1

        self.checker.calendar = calendar
        self.checker.tasklist = calendar
        self.checker.journallist = calendar

    def _prepare_task_calendar(self, cal_id, name, add_if_not_existing):
        """Handle task calendar setup and save-load.todo / save-load.todo.mixed-calendar features."""
        base = self.checker.fixture_base_year
        try:
            task_with_dtstart = add_if_not_existing(
                Todo,
                summary="task with a dtstart",
                uid="csc_simple_task1",
                dtstart=date(base, 1, 7),
            )
            task_with_dtstart.load()
            self.set_feature("save-load.todo")
            self.set_feature("save-load.todo.mixed-calendar")
        except Exception:
            try:
                tasklist = self.checker.principal.calendar(cal_id=f"{cal_id}_tasks")
                tasklist.todos()
            except Exception:
                tasklist = self.checker.principal.make_calendar(
                    cal_id=f"{cal_id}_tasks",
                    name=f"{name} - tasks",
                    supported_calendar_component_set=["VTODO"],
                )
            self.checker.tasklist = tasklist
            try:
                task_with_dtstart = add_if_not_existing(
                    Todo,
                    summary="task with a dtstart",
                    uid="csc_simple_task1",
                    dtstart=date(base, 1, 7),
                )
            except DAVError as e:  ## exception e for debugging purposes
                self.set_feature("save-load.todo", "ungraceful")
                return False

            task_with_dtstart.load()
            self.set_feature("save-load.todo")
            self.set_feature("save-load.todo.mixed-calendar", False)
        return True

    def _prepare_journal_calendar(self, cal_id, name, add_if_not_existing):
        """Handle journal calendar setup and save-load.journal / save-load.journal.mixed-calendar features."""
        base = self.checker.fixture_base_year
        try:
            simple_journal = add_if_not_existing(
                Journal,
                summary="simple journal entry",
                uid="csc_simple_journal1",
                dtstart=date(base, 1, 11),
            )
            simple_journal.load()
            self.set_feature("save-load.journal")
            self.set_feature("save-load.journal.mixed-calendar")
        except Exception:
            journallist = None
            try:
                journallist = self.checker.principal.calendar(cal_id=f"{cal_id}_journals")
                journallist.journals()
            except Exception:
                try:
                    journallist = self.checker.principal.make_calendar(
                        cal_id=f"{cal_id}_journals",
                        name=f"{name} - journals",
                        supported_calendar_component_set=["VJOURNAL"],
                    )
                except Exception:
                    self.set_feature("save-load.journal", False)
                    journallist = None
            if journallist is not None:
                self.checker.journallist = journallist
                try:
                    simple_journal = add_if_not_existing(
                        Journal,
                        summary="simple journal entry",
                        uid="csc_simple_journal1",
                        dtstart=date(base, 1, 11),
                    )
                    simple_journal.load()
                    self.set_feature("save-load.journal")
                    self.set_feature("save-load.journal.mixed-calendar", False)
                except Exception:
                    self.set_feature("save-load.journal", "ungraceful")

    def _create_test_events(self, calendar, cal_id, name, add_if_not_existing):
        """Create all the test event/task/journal objects in the calendar.

        All fixtures live in ``base`` (next calendar year, see :func:`_base_year`)
        so they stay inside the search window of sliding-window servers.  The
        relative month/day offsets are unchanged from when the fixtures lived in
        year 2000, so every fixture-dependent check keeps the same arithmetic -
        only the year differs.
        """
        base = self.checker.fixture_base_year
        todo_ok = self._prepare_task_calendar(cal_id, name, add_if_not_existing)
        if not todo_ok:
            return False

        self._prepare_journal_calendar(cal_id, name, add_if_not_existing)

        simple_event = add_if_not_existing(
            Event,
            summary="simple event with a start time and an end time",
            uid="csc_simple_event1",
            dtstart=datetime(base, 1, 1, 12, 0, 0, tzinfo=utc),
            dtend=datetime(base, 1, 1, 13, 0, 0, tzinfo=utc),
        )
        simple_event.load()
        self.set_feature("save-load.event")

        non_duration_event = add_if_not_existing(
            Event,
            summary="event with a start time but no end time",
            uid="csc_simple_event2",
            dtstart=datetime(base, 1, 2, 12, 0, 0, tzinfo=utc),
        )

        one_day_event = add_if_not_existing(
            Event,
            summary="event with a start date but no end date",
            uid="csc_simple_event3",
            dtstart=date(base, 1, 3),
        )

        two_days_event = add_if_not_existing(
            Event,
            summary="event with a start date and end date",
            uid="csc_simple_event4",
            dtstart=date(base, 1, 4),
            dtend=date(base, 1, 6),
        )

        event_with_categories = add_if_not_existing(
            Event,
            summary="event with categories",
            uid="csc_event_with_categories",
            categories="hands,feet,head",
            dtstart=datetime(base, 1, 7, 12, 0, 0),
            dtend=datetime(base, 1, 7, 13, 0, 0),
        )

        event_with_class = add_if_not_existing(
            Event,
            summary="event with confidential class",
            uid="csc_event_with_class",
            dtstart=datetime(base, 1, 16, 12, 0, 0, tzinfo=utc),
            dtend=datetime(base, 1, 16, 13, 0, 0, tzinfo=utc),
            class_="CONFIDENTIAL",
        )

        event_with_duration = add_if_not_existing(
            Event,
            summary="event with duration instead of dtend",
            uid="csc_event_with_duration",
            dtstart=datetime(base, 1, 17, 12, 0, 0, tzinfo=utc),
            duration=timedelta(hours=1),
        )

        task_with_due = add_if_not_existing(
            Todo,
            summary="task with a due date",
            uid="csc_simple_task2",
            due=date(base, 1, 8),
        )

        task_with_dtstart_and_due = add_if_not_existing(
            Todo,
            summary="task with a dtstart time and due time",
            uid="csc_simple_task3",
            dtstart=datetime(base, 1, 9, 12, 0, 0, tzinfo=utc),
            due=datetime(base, 1, 9, 13, 0, 0, tzinfo=utc),
        )

        ## Task with DTSTART + DURATION (no DUE): used by CheckOpenTimeRangeSearch
        ## for two tests:
        ## 1. Duration overlap: DTSTART=<base>-01-18T12:00Z, DURATION=PT2H → ends at 14:00Z.
        ##    Searches overlapping the interval [12:00, 14:00] should return this task.
        ##    RFC4791 section 9.9: VTODO overlaps [start, end] if DTSTART+DURATION > start.
        ## 2. Open-start filtering: DTSTART=<base>-01-18 is after end=<base>-01-15, so this
        ##    task must NOT be returned by an end-only search with end=<base>-01-15.
        add_if_not_existing(
            Todo,
            summary="task with dtstart and duration (no due)",
            uid="csc_task_with_duration",
            dtstart=datetime(base, 1, 18, 12, 0, 0, tzinfo=utc),
            duration=timedelta(hours=2),
        )

        ## TODO: there are more variants to be tested - dtstart date and due date,
        ## only duration, no time spec at all, ...

        try:
            event_with_alarm = add_if_not_existing(
                Event,
                summary="event with alarm",
                uid="csc_event_with_alarm",
                dtstart=datetime(base, 1, 10, 8, 0, 0, tzinfo=utc),
                dtend=datetime(base, 1, 10, 9, 0, 0, tzinfo=utc),
                alarm_trigger=timedelta(minutes=-15),
                alarm_action="DISPLAY",
            )
        except Exception:
            ## Some servers reject events with alarms or old dates
            logging.warning("Server rejected event with alarm")

        recurring_event = add_if_not_existing(
            Event,
            summary="monthly recurring event",
            uid="csc_monthly_recurring_event",
            rrule={"FREQ": "MONTHLY"},
            dtstart=datetime(base, 1, 12, 12, 0, 0, tzinfo=utc),
            dtend=datetime(base, 1, 12, 13, 0, 0, tzinfo=utc),
        )
        recurring_event.load()
        self.set_feature("save-load.event.recurrences")

        ## All-day (VALUE=DATE) recurring event, used to test whether the server
        ## handles implicit recurrence for all-day events.  Uses a MONTHLY (not
        ## yearly) rrule so the second occurrence (<base>-03-01) stays close to the
        ## master (<base>-02-01) and inside sliding-window servers' search windows -
        ## a yearly rrule would put the second occurrence ~2 years out, outside
        ## e.g. OX's window, producing a false "broken for all-day events" verdict.
        add_if_not_existing(
            Event,
            summary="monthly recurring all-day event",
            uid="csc_monthly_recurring_allday_event",
            rrule={"FREQ": "MONTHLY"},
            dtstart=date(base, 2, 1),
        )

        event_with_rrule_and_count = add_if_not_existing(
            Event,
            f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Example Corp.//CalDAV Client//EN
BEGIN:VEVENT
UID:csc_weeklymeeting
DTSTAMP:{base}0113T151313Z
DTSTART:{base}0120T140000Z
DTEND:{base}0120T150000Z
SUMMARY:Weekly meeting for three weeks
RRULE:FREQ=WEEKLY;COUNT=3
END:VEVENT
END:VCALENDAR""",
        )
        event_with_rrule_and_count.load()
        component = event_with_rrule_and_count.component
        rrule = component.get("RRULE", None)
        count = rrule and rrule.get("COUNT")
        self.set_feature("save-load.event.recurrences.count", count == [3])

        try:
            recurring_task = add_if_not_existing(
                Todo,
                summary="monthly recurring task",
                uid="csc_monthly_recurring_task",
                rrule={"FREQ": "MONTHLY"},
                dtstart=datetime(base, 1, 12, 12, 0, 0, tzinfo=utc),
                due=datetime(base, 1, 12, 13, 0, 0, tzinfo=utc),
            )
            recurring_task.load()
            self.set_feature("save-load.todo.recurrences")
        except DAVError:
            self.set_feature("save-load.todo.recurrences", "ungraceful")

        try:
            task_with_rrule_and_count = add_if_not_existing(
                Todo,
                f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Example Corp.//CalDAV Client//EN
BEGIN:VTODO
UID:csc_recurring_count_task
DTSTAMP:{base}0113T151313Z
DTSTART:{base}0119T065500Z
STATUS:NEEDS-ACTION
DURATION:PT10M
SUMMARY:Weekly task to be done three times
RRULE:FREQ=WEEKLY;BYDAY=MO;COUNT=3
CATEGORIES:CHORE
PRIORITY:3
END:VTODO
END:VCALENDAR""",
            )
            task_with_rrule_and_count.load()
            component = task_with_rrule_and_count.component
            rrule = component.get("RRULE", None)
            count = rrule and rrule.get("COUNT")
            self.set_feature("save-load.todo.recurrences.count", count == [3])
        except DAVError:
            self.set_feature("save-load.todo.recurrences.count", "ungraceful")

        try:
            recurring_event_with_exception = add_if_not_existing(
                Event,
                f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//tobixen//Caldav-Server-Tester//en_DK
BEGIN:VEVENT
UID:csc_monthly_recurring_with_exception
DTSTART:{base}0113T120000Z
DTEND:{base}0113T130000Z
DTSTAMP:20240429T181103Z
RRULE:FREQ=MONTHLY
SUMMARY:Monthly recurring with exception
END:VEVENT
BEGIN:VEVENT
UID:csc_monthly_recurring_with_exception
RECURRENCE-ID:{base}0213T120000Z
DTSTART:{base}0213T120000Z
DTEND:{base}0213T130000Z
DTSTAMP:20240429T181103Z
SUMMARY:February recurrence with different summary
END:VEVENT
END:VCALENDAR""",
            )

            ## Check whether the server stores exception VEVENTs as part of the same
            ## calendar object resource as the master VEVENT, or incorrectly (either as
            ## separate objects or already expanded into 3 VEVENTs instead of 2).
            ## When stored incorrectly, client-side expansion gives wrong results.
            try:
                exception_uid = "csc_monthly_recurring_with_exception"
                objs_with_exception_uid = [
                    obj for obj in calendar.events() if obj.icalendar_component.get("UID") == exception_uid
                ]
                if len(objs_with_exception_uid) != 1:
                    ## Multiple objects with the same UID: server split exception into separate object
                    self.set_feature("save-load.event.recurrences.exception", False)
                else:
                    ## One object - check it has exactly 2 VEVENTs (master + exception)
                    vevents = [
                        c for c in objs_with_exception_uid[0].icalendar_instance.subcomponents if c.name == "VEVENT"
                    ]
                    self.set_feature(
                        "save-load.event.recurrences.exception",
                        len(vevents) == 2,
                    )
            except Exception:
                self.set_feature("save-load.event.recurrences.exception", "ungraceful")
        except DAVError:
            self.set_feature("save-load.event.recurrences.exception", "ungraceful")

        ## A second recurrence-with-exception event, identical in shape to the one
        ## above but additionally carrying SEQUENCE on both the master and the
        ## override - as real-world clients (e.g. Thunderbird) always do.  Some
        ## servers (observed on Stalwart) only suppress the exception-overridden
        ## occurrence during server-side expand when SEQUENCE is *absent*; with
        ## SEQUENCE present they return both the original occurrence and the
        ## override.  CheckRecurrenceSearch uses this fixture to detect that and
        ## downgrade search.recurrences.expanded.exception to "fragile".
        ## Placed on the 14th so its occurrences never collide with the
        ## SEQUENCE-less exception event (13th) or the plain recurring event (12th).
        try:
            add_if_not_existing(
                Event,
                f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//tobixen//Caldav-Server-Tester//en_DK
BEGIN:VEVENT
UID:csc_monthly_recurring_with_exception_seq
DTSTART:{base}0114T120000Z
DTEND:{base}0114T130000Z
DTSTAMP:20240429T181103Z
SEQUENCE:1
RRULE:FREQ=MONTHLY
SUMMARY:Monthly recurring with exception (seq)
END:VEVENT
BEGIN:VEVENT
UID:csc_monthly_recurring_with_exception_seq
RECURRENCE-ID:{base}0214T120000Z
DTSTART:{base}0214T120000Z
DTEND:{base}0214T130000Z
DTSTAMP:20240429T181103Z
SEQUENCE:1
SUMMARY:February recurrence with different summary (seq)
END:VEVENT
END:VCALENDAR""",
            )
        except DAVError:
            pass

        self._create_olddate_probes()

        return True

    def _create_olddate_probes(self):
        """Create the persistent year-2000 probe objects.

        The reusable fixtures live in the near future (next year) so that
        sliding-window servers can see them, but a couple of checks still need
        an object that is *intentionally* far in the past, to detect whether the
        server hides far-past objects from search:

        * ``csc_olddate_event`` (2000-01-01) - used by
          ``search.time-range.event.old-dates`` and ``search.unlimited-time-range``.
        * ``csc_olddate_task`` (2000-01-09) - used by
          ``search.time-range.todo.old-dates``.

        These are PUT unconditionally (idempotent overwrite by UID) rather than
        via the near-future reuse machinery, since they fall outside the fixture
        window and would never be matched there.  They are excluded from the
        comp-type reference sets by :func:`_filter_fixture_window`.
        """
        try:
            self.checker.calendar.save_object(
                Event,
                summary="old-date probe event (year 2000)",
                uid=OLDDATE_EVENT_UID,
                dtstart=datetime(2000, 1, 1, 12, 0, 0, tzinfo=utc),
                dtend=datetime(2000, 1, 1, 13, 0, 0, tzinfo=utc),
            )
        except Exception:
            logging.warning("Server rejected the year-2000 old-date probe event")
        try:
            self.checker.tasklist.save_object(
                Todo,
                summary="old-date probe task (year 2000)",
                uid=OLDDATE_TASK_UID,
                dtstart=datetime(2000, 1, 9, 12, 0, 0, tzinfo=utc),
                due=datetime(2000, 1, 9, 13, 0, 0, tzinfo=utc),
            )
        except Exception:
            logging.warning("Server rejected the year-2000 old-date probe task")

    def _check_get_by_url(self, calendar):
        """Check if GET requests to server-reported calendar object URLs work."""
        ## Tests the URL returned by the server (via PROPFIND/REPORT), not the
        ## client-constructed PUT URL - some servers (e.g. Zimbra) accept PUTs
        ## to client URLs but return different URLs that fail on GET.
        try:
            server_event = calendar.object_by_uid("csc_simple_event1")
            r = self.client.request(str(server_event.url))
            self.set_feature("save-load.get-by-url", r.status != 404)
        except Exception:
            self.set_feature("save-load.get-by-url", None)

    def _encode_at_request(self, send):
        """Issue one encode-at probe request and triage what came back.

        Returns the response when the server answered affirmatively, ``False``
        when it said no, and ``None`` when it said nothing either way.  All
        four probe requests - the read, the PROPFIND, the PUT and the
        MKCALENDAR/MKCOL - go through here, because they used to each grow
        their own rule and the divergence is not cosmetic: "the server refuses
        a literal '@'" is copied into a server profile and makes the client
        rewrite every such URL from then on.

        * every 4xx is a miss, not only 404 - a server answering a
          percent-encoded path with ``400 Bad Request`` has still failed to
          resolve it;
        * a 5xx is not a miss: that is the server breaking, not the spelling
          failing, and so is a dropped connection, a timeout or a DNS failure;
        * ``AuthorizationError`` (401/403) and ``NotFoundError`` are raised by
          the client *before* a response object exists, so they never show up
          as a status.  Both are misses - this suite documents servers (Robur)
          that answer 403 where others answer 404.
        """
        try:
            response = send()
        except (AuthorizationError, NotFoundError):
            return False
        except Exception:
            return None
        if response.status >= 500:
            return None
        if response.status >= 400:
            return False
        return response

    def _encode_at_ok(self, send):
        """``_encode_at_request`` reduced to True / False / None."""
        outcome = self._encode_at_request(send)
        return outcome if outcome is None or outcome is False else True

    def _resolved_uid(self, url):
        """Which probe object a GET on ``url`` reaches.

        Returns the UID found, ``False`` when the URL does not reach a probe
        object at all, and ``None`` when the request said nothing either way.

        Triaged by ``_encode_at_request``, with one addition the other three
        do not need: a 2xx only counts when the body actually carries one of
        the probe UIDs.  A server that answers 200 for any child path - the
        class this suite probes as ``non-existing-raises-not-found`` broken -
        would otherwise look like it resolved whatever we asked for.
        """
        response = self._encode_at_request(lambda: self.client.request(url))
        if response is None or response is False:
            return response
        try:
            body = to_local(response.raw)
        except Exception:
            return None
        for uid in ENCODE_AT_PROBE_UIDS:
            if uid in body:
                return uid
        return False

    def _put_probe_object(self, url, uid):
        """PUT an encode-at probe object at ``url``.

        ``True`` when the server took it, ``False`` when it refused it (the
        ownCloud shape this probe exists for), and ``None`` when the request
        said nothing either way - ``_encode_at_request`` draws that line.
        """
        year = self.checker.fixture_base_year
        ical = (
            "BEGIN:VCALENDAR\r\n"
            "VERSION:2.0\r\n"
            "PRODID:-//caldav-server-tester//url.encode-at probe//EN\r\n"
            "BEGIN:VEVENT\r\n"
            f"UID:{uid}\r\n"
            f"DTSTAMP:{year}0119T120000Z\r\n"
            f"DTSTART:{year}0119T120000Z\r\n"
            f"DTEND:{year}0119T130000Z\r\n"
            "SUMMARY:url.encode-at probe\r\n"
            "END:VEVENT\r\n"
            "END:VCALENDAR\r\n"
        )
        return self._encode_at_ok(lambda: self.client.put(url, ical, {"Content-Type": "text/calendar; charset=utf-8"}))

    def _record_encode_at(self, *observations):
        """Write what the axes saw into the ``url.encode-at`` subfeatures.

        ``literal`` gets one key per axis.  That used to be merged like the
        other two, on the reasoning that the spelling of a path segment is
        decided well below whatever the segment happens to name, so no server
        would ever differ between the axes.  Stalwart does: it re-encodes an
        ``@`` in an object name and serves a calendar path under whichever
        spelling it was asked for.  Merged, that came out ``fragile``, which
        says "somewhere this does not work" and leaves the one consumer - the
        ownCloud home-set hack, which is about a *principal* path - no way to
        know whether the verdict was about the segment it cares about.

        ``identity`` and ``encoded`` stay shared, because no server has been
        seen to differ on them.  Where their axes disagree the result is
        ``fragile`` and a warning asking for a report: that report is what
        justified splitting ``literal``, and it is what would justify
        splitting either of these.  Splitting one on anything less would be
        modelling a distinction nobody has observed.

        A subfeature no axis observed is recorded ``unknown`` rather than
        filled in from a sibling - each has its own default, and compare()
        skips an unknown, so it says "not observed" and claims nothing.
        """
        observations = [o for o in observations if o is not None]
        prose = "; ".join(f"{o.axis}: {o.behaviour}" for o in observations if o.behaviour)
        self._record_encode_at_literal(observations)
        for name in ("identity", "encoded"):
            seen = [(o.axis, getattr(o, name)) for o in observations if getattr(o, name)]
            levels = {level for _, level in seen}
            if not levels:
                support, note = "unknown", prose
            elif len(levels) == 1:
                support, note = levels.pop(), prose
            else:
                ## A server that treats an '@' in an object name differently
                ## from one in a calendar id (or in a principal path) was
                ## expected never to turn up - the spelling of a path segment
                ## is normally decided in one place, well below whatever the
                ## segment happens to name.  One did, on the reachability of
                ## the literal spelling, and that is exactly why
                ## url.encode-at.literal now has per-axis children.  If this
                ## warning fires for identity or encoded, THAT is the same
                ## evidence for splitting those
                ## (url.encode-at.identity.object / .collection); until then
                ## the split would be modelling a distinction nobody has seen,
                ## and 'fragile' plus the behaviour text says what was observed
                ## without inventing a shape for it.
                disagreement = ", ".join(f"{axis} {level}" for axis, level in seen)
                support = "fragile"
                note = f"the axes disagree ({disagreement})"
                if prose:
                    note = f"{note}; {prose}"
                logging.warning(
                    "url.encode-at.%s: the axes disagree (%s). This was not expected to happen on any "
                    "real server - please report it, as it is the evidence needed to split this feature "
                    "into per-axis subfeatures. Recording 'fragile'.",
                    name,
                    disagreement,
                )
            node = {"support": support}
            if note:
                node["behaviour"] = note
            self.set_feature(f"url.encode-at.{name}", node)

    ## The three axes, in the order their subfeature keys are written.  Named
    ## here rather than taken from the observations, so that an axis which
    ## returned nothing at all still gets its "unknown" written: the checker
    ## asserts that every declared feature was set, and a silently absent key
    ## would read as "the default applies" rather than "nobody looked".
    ENCODE_AT_AXIS_KEYS = ("object", "collection", "principal")

    def _record_encode_at_literal(self, observations):
        """One ``url.encode-at.literal.<axis>`` per axis, no merging.

        An axis that reached no verdict records ``unknown`` and keeps its
        behaviour text, so the profile still says what was seen where nothing
        could be graded.
        """
        by_key = {o.key: o for o in observations if o.key}
        for key in self.ENCODE_AT_AXIS_KEYS:
            observation = by_key.get(key)
            support = observation.literal if observation is not None and observation.literal else "unknown"
            node = {"support": support}
            if observation is not None and observation.behaviour:
                node["behaviour"] = observation.behaviour
            self.set_feature(f"url.encode-at.literal.{key}", node)

    def _delete_probe(self, url, what):
        """Best-effort removal of a probe resource; a leftover is a warning, not a failure."""
        try:
            self.client.delete(url)
        except Exception:
            logging.warning("Could not delete the url.encode-at probe %s at %s", what, url)

    def _delete_probe_object(self, url):
        self._delete_probe(url, "object")

    ## ------------------------------------------------------------------
    ## Axis 1: an '@' in an object name
    ## ------------------------------------------------------------------

    def _probe_encode_at_object_identity(self, literal_url, encoded_url, encoded_resolved_first):
        """One resource, or two?  Writes a second object and sees what moves.

        PUTs a *second* object at the ``%40`` spelling of the same path and
        re-reads the literal one.  If the literal path now yields the second
        object the server aliases the two spellings; if it still yields the
        first they are two resources, and any client that rewrites one spelling
        into the other is silently writing to one and reading from the other.

        **Entered whether or not ``%40`` resolved to begin with**, and that is
        the whole point.  A conformant server - two spellings, two resources -
        answers 404 at ``%40`` for the simple reason that nothing was ever
        written there, which is indistinguishable from a server that cannot
        serve the encoded spelling at all.  Reading the first as the second is
        how the conformant case became unobservable: it fell through to the
        library default, which says the two spellings are one.  On the one
        class of server where that is wrong, the client was then told it could
        rewrite freely.  So the alias object is written either way, and what
        happens to it decides both axes:

        * the write lands and the literal path still holds the first object -
          two resources, and ``%40`` demonstrably resolves;
        * the write lands and the literal path now holds the second - one
          resource under two names;
        * the write is refused, and ``%40`` had not resolved before either -
          the encoded spelling is unusable, and identity is unobservable
          rather than "the same".

        ``encoded_resolved_first`` is whether ``%40`` served the object back
        before any of this: it is what separates "refused a second object"
        (identity unknown, encoded still fine) from "will not take an object at
        that spelling at all" (encoded unsupported).

        Cleans up after itself: the second object is deleted where it turned
        out to be a resource of its own, and the first is re-PUT where the
        server overwrote it, so the fixture survives either outcome.
        """
        both = "both spellings resolve" if encoded_resolved_first else "only the literal spelling resolved"
        alias_put = self._put_probe_object(encoded_url, ENCODE_AT_ALIAS_UID)
        if not alias_put:
            if alias_put is None:
                return _object_axis(
                    f"{both}, and the second probe object could not be PUT to the '%40' spelling, so it is unknown whether the two name the same resource",
                    literal="full",
                    encoded="full" if encoded_resolved_first else None,
                )
            if encoded_resolved_first:
                return _object_axis(
                    "both spellings resolve, but the server would not take a second object at the '%40' spelling, so it is unknown whether they name the same resource",
                    literal="full",
                    encoded="full",
                )
            return _object_axis(
                "the server serves an object under the literal '@' spelling and refuses to store one under '%40'",
                literal="full",
                encoded="unsupported",
            )

        landed = self._resolved_uid(encoded_url)
        literal_now = self._resolved_uid(literal_url)

        if landed != ENCODE_AT_ALIAS_UID:
            ## The second PUT went somewhere we cannot account for; say so
            ## rather than reading the literal path as evidence of anything.
            observation = _object_axis(
                f"{both}, but the second probe object did not turn up at the '%40' spelling it was PUT to",
                literal="full",
                encoded="full" if encoded_resolved_first else None,
            )
        elif literal_now == ENCODE_AT_ALIAS_UID:
            observation = _object_axis(
                "both spellings resolve and reach the same resource - lenient rather than conformant, but nothing a client has to work around",
                literal="full",
                encoded="full",
                identity="unsupported",
            )
        elif literal_now == ENCODE_AT_UID:
            ## Two objects, two spellings, each holding its own: the encoded
            ## spelling resolved here even if it had nothing to serve before.
            self._delete_probe_object(encoded_url)
            return _object_axis(
                "both spellings resolve and name two different resources, which is what RFC3986 says they are",
                literal="full",
                encoded="full",
                identity="full",
            )
        else:
            observation = _object_axis(
                f"{both}, but after writing to the '%40' spelling the literal one reached neither probe object",
                literal="full",
                encoded="full" if encoded_resolved_first else None,
            )

        ## Aliased (or undeterminable): the second PUT may have landed on the
        ## fixture itself, so put the original content back.
        self._put_probe_object(literal_url, ENCODE_AT_UID)
        return observation

    def _probe_encode_at_object(self, calendar):
        """The object axis: an ``@`` in the name of an ``.ics`` inside a calendar.

        *Reachability* - which spellings resolve at all.  RFC3986 section 3.3
        makes ``@`` a legal ``pchar``, so no producer has to encode it, yet
        servers disagree about whether the encoded spelling reaches the same
        path.  Probed by establishing the object at the literal ``@`` URL and
        GETting both spellings.

        *Identity* - when both resolve, whether they are one resource or two.
        Section 2.2 makes ``@`` reserved and section 6.2.2.2 licenses decoding
        only *unreserved* octets, so "two" is the conformant reading; a server
        that aliases them is being lenient.  This axis only matters because
        clients rewrite spellings - the library's ownCloud calendar-home-set
        heuristic does - and on a "two resources" server that rewriting means
        writing one object and reading another.  Probed by writing a second
        object at the ``%40`` spelling and re-reading the literal one.

        The probe PUTs its own objects rather than reusing a fixture, because
        the library's ``save_object()`` runs the UID through ``_quote_uid()``
        and would silently place it at the ``%40`` URL - leaving us comparing a
        spelling we never wrote to against one we did, which is how an earlier
        draft of this check could report "the client must encode" for a server
        that simply stores path segments verbatim.

        Not this probe's business: a server that stores the object under one
        spelling and later *reports* it under the other.  That is a flavour of
        ``save-load.stable-url`` and is probed there.
        """
        try:
            literal_url = str(calendar.url.join(ENCODE_AT_UID + ".ics"))
            encoded_url = str(calendar.url.join(quote(ENCODE_AT_UID) + ".ics"))

            ## Start from a known-empty pair of URLs.  An alias object left by
            ## an aborted run (or by a DELETE the server only pretended to
            ## honour) is otherwise read as "'%40' does not reach an object
            ## stored under a literal '@'" - which is false: '%40' plainly
            ## reached an object, just not the one this run wrote.
            self._delete_probe_object(encoded_url)
            self._delete_probe_object(literal_url)

            literal_put = self._put_probe_object(literal_url, ENCODE_AT_UID)
            if literal_put is None:
                return _object_axis("the PUT to the literal '@' path could not be completed")
            if not literal_put:
                ## The server would not create a resource at the literal
                ## spelling.  If it takes the encoded one instead, that alone
                ## is the actionable fact; we can say nothing about how a
                ## literally-stored resource would have behaved.
                encoded_put = self._put_probe_object(encoded_url, ENCODE_AT_UID)
                if encoded_put is None:
                    return _object_axis("the PUT to the '%40' path could not be completed")
                if not encoded_put:
                    return _object_axis("the server accepted the probe object under neither spelling")
                if self._resolved_uid(encoded_url) == ENCODE_AT_UID:
                    return _object_axis(
                        "the server refused a PUT to the literal '@' path and accepted the '%40' one",
                        literal="unsupported",
                        encoded="full",
                    )
                return _object_axis(
                    "the server refused the literal '@' path and did not serve back what it accepted under '%40'",
                )

            literal_uid = self._resolved_uid(literal_url)
            encoded_uid = self._resolved_uid(encoded_url)

            if literal_uid is None or encoded_uid is None:
                return _object_axis("the url.encode-at probe requests could not be completed")
            if literal_uid == ENCODE_AT_UID:
                ## Both resolve, or only the literal one - either way the
                ## question is now whether a write to '%40' is a write to the
                ## same resource, and that is the same experiment.
                return self._probe_encode_at_object_identity(
                    literal_url, encoded_url, encoded_resolved_first=encoded_uid == ENCODE_AT_UID
                )
            if encoded_uid == ENCODE_AT_UID:
                ## The PUT went to the literal path and only the encoded one
                ## serves it back: whatever the server did internally, '%40' is
                ## the spelling that reaches the object.
                return _object_axis(
                    "an object PUT to the literal '@' path is reachable only under '%40'",
                    literal="unsupported",
                    encoded="full",
                )
            ## Neither spelling served it back.  A write delay looks exactly
            ## like this, so this is not evidence that '@' paths are unusable -
            ## it is simply not an answer.
            return _object_axis(
                "the probe object was not reachable under either spelling after it was stored",
            )
        except Exception:
            return _object_axis("the url.encode-at object probe could not be carried out")

    ## ------------------------------------------------------------------
    ## Axis 2: an '@' in a calendar id
    ## ------------------------------------------------------------------

    def _propfind_resolves(self, url):
        """Whether a PROPFIND on ``url`` reaches a collection.

        ``True``/``False``/``None`` on ``_encode_at_request``'s terms.  A GET
        would not do here - plenty of servers answer 405 for a GET on a
        collection whether or not it exists.
        """
        return self._encode_at_ok(lambda: self.client.propfind(url, ENCODE_AT_PROPFIND_BODY, 0))

    def _mkcalendar_probe(self, url):
        """Create a probe calendar at exactly ``url``.

        ``True``/``False``/``None`` on ``_encode_at_request``'s terms.
        Issued straight at the URL rather than through ``make_calendar()``,
        which builds the path from a ``cal_id`` and would decide the spelling
        this probe is asking about.  MKCOL is used where
        ``create-calendar`` was probed as ``mkcol-required``: asking a server
        that only answers MKCOL with MKCALENDAR fails by construction and
        would be read as "it will not take a calendar at that spelling".
        """
        node = self.checker.features_checked.is_supported("create-calendar", return_type=dict)
        mkcol = isinstance(node, dict) and node.get("behaviour") == "mkcol-required"
        if mkcol:
            return self._encode_at_ok(lambda: self.client.mkcol(url, ENCODE_AT_MKCOL_BODY))
        return self._encode_at_ok(lambda: self.client.mkcalendar(url, ENCODE_AT_MKCALENDAR_BODY))

    def _delete_probe_collection(self, url):
        self._delete_probe(url, "calendar")

    def _probe_encode_at_collection_identity(self, literal_url, encoded_url):
        """One collection, or two?  Called with a calendar created at ``literal_url``.

        An object is stored in it and then asked for through the ``%40``
        spelling of the *collection* segment.  If it comes back, the two
        spellings are one collection and there is nothing more to establish.
        If it does not, that is not yet an answer: the encoded spelling may
        name a different collection which simply does not exist yet, or it may
        not resolve at all.  Creating a second calendar there separates the
        two, exactly as the second object PUT does on the object axis - and
        that disambiguation is needed on *every* path out of here, not only
        where the object was stored successfully.  A bare PROPFIND on ``%40``
        used to decide it where nothing could be stored, which made a
        conformant server (nothing has been created at ``%40``, so it 404s)
        and a merely slow one (the ``create-calendar`` write-delay quirk this
        suite already probes) come out ``encoded: unsupported``.

        A write that said nothing either way - a 5xx, a dropped connection -
        is not a refusal: the object may be there regardless, so it is asked
        for anyway, and an object that comes back through ``%40`` settles
        identity whatever the PUT thought of itself.
        """
        literal_obj = literal_url + ENCODE_AT_IN_COLLECTION_UID + ".ics"
        encoded_obj = encoded_url + ENCODE_AT_IN_COLLECTION_UID + ".ics"

        stored = self._put_probe_object(literal_obj, ENCODE_AT_IN_COLLECTION_UID)
        if stored is not False:
            seen = self._resolved_uid(encoded_obj)
            if seen == ENCODE_AT_IN_COLLECTION_UID:
                return _collection_axis(
                    "an object stored through the literal '@' spelling of the calendar path is served back through '%40' - one collection under two names",
                    literal="full",
                    encoded="full",
                    identity="unsupported",
                )
            if stored and seen is None:
                return _collection_axis(
                    "a calendar exists at the literal '@' path, but reading its contents through the '%40' spelling could not be completed",
                    literal="full",
                )

        ## Either nothing could be stored, or the object did not come back
        ## through '%40'.  Neither says yet that the encoded spelling fails:
        ## it may name a collection of its own that does not exist.
        missing = (
            "no object could be stored in it"
            if stored is False
            else (
                "storing an object in it could not be completed" if stored is None else "it does not serve that object"
            )
        )
        second = self._mkcalendar_probe(encoded_url)
        if second is None:
            return _collection_axis(
                f"a calendar exists at the literal '@' path and {missing} through '%40', and creating a calendar of its own there could not be completed",
                literal="full",
            )
        if not second:
            ## Refused - but "there is already a collection here" is a refusal
            ## too (RFC4918 makes that a 405), and it means the opposite of
            ## "this spelling does not work".  A PROPFIND separates the two:
            ## the only collection that can be there is the one just created
            ## under '@', so an answer means the spelling resolves after all.
            if self._propfind_resolves(encoded_url):
                return _collection_axis(
                    f"the '%40' spelling answers for the calendar created under '@' but {missing}, so it is unknown whether they name the same collection",
                    literal="full",
                    encoded="full",
                )
            return _collection_axis(
                "the '%40' spelling of the calendar path neither serves the contents of the calendar created under '@' nor accepts a calendar of its own",
                literal="full",
                encoded="unsupported",
            )

        if not stored:
            ## A calendar of its own was accepted at '%40', so that spelling
            ## works - but there is no object of ours in the first one to
            ## compare against, so identity stays out of reach.
            return _collection_axis(
                f"both spellings of the calendar path take a calendar of their own, but {missing}, so it is unknown whether they name the same collection",
                literal="full",
                encoded="full",
            )

        still_literal = self._resolved_uid(literal_obj)
        now_encoded = self._resolved_uid(encoded_obj)
        if still_literal is None or now_encoded is None:
            return _collection_axis(
                "a calendar was created at both spellings of the path, but reading them back could not be completed",
                literal="full",
                encoded="full",
            )
        if now_encoded == ENCODE_AT_IN_COLLECTION_UID:
            ## The second MKCALENDAR was accepted and yet the object written
            ## through the first spelling shows up through the second: the
            ## server took the request as naming the collection it already had.
            return _collection_axis(
                "a second calendar was accepted at the '%40' spelling, but it serves the object stored through '@' - one collection under two names",
                literal="full",
                encoded="full",
                identity="unsupported",
            )
        if still_literal == ENCODE_AT_IN_COLLECTION_UID:
            return _collection_axis(
                "the two spellings of the calendar path are two separate collections, each holding its own objects",
                literal="full",
                encoded="full",
                identity="full",
            )
        return _collection_axis(
            "a calendar was created at both spellings of the path, but the object stored in the first one was gone afterwards",
            literal="full",
            encoded="full",
        )

    def _probe_encode_at_collection(self):
        """The collection axis: an ``@`` in a calendar id rather than an object name.

        This is the shape the feature exists for.  The ownCloud/Nextcloud
        calendar-home-set the library has a workaround for is
        ``/remote.php/dav/calendars/user@example.com/`` - a *collection*
        segment carrying an email address - and a server may route that quite
        differently from an ``.ics`` file name, which is all the object axis
        can speak for.

        Creates a calendar of its own rather than looking at the real
        calendar-home-set, because the question is what the server does with a
        spelling *the client chose*, and because the home-set is not ours to
        experiment on.  Returns ``None`` when there is nothing to be learnt:
        no principal, or a server that cannot create and delete calendars, in
        which case the probe would either fail by construction or leak a
        calendar it cannot clean up.
        """
        if self.feature_ungood("create-calendar") or self.feature_ungood("delete-calendar"):
            return _collection_axis(
                "not probed: the server does not support creating and deleting calendars, so a probe calendar could not be made and cleaned up"
            )
        try:
            home = self.checker.principal.calendar_home_set.url
            literal_url = str(home.join(ENCODE_AT_CAL_ID + "/"))
            encoded_url = str(home.join(quote(ENCODE_AT_CAL_ID) + "/"))
        except Exception:
            return None
        if literal_url == encoded_url:
            ## Nothing to compare - the two spellings came out the same, so
            ## whatever built them normalised one into the other.
            return None

        try:
            ## Clear both spellings first, for the same reason the object axis
            ## does: a calendar left at '%40' by an aborted run would be read
            ## as the encoded spelling resolving on its own.
            self._delete_probe_collection(encoded_url)
            self._delete_probe_collection(literal_url)

            made = self._mkcalendar_probe(literal_url)
            if made is None:
                ## A 5xx or a dropped connection may well have created the
                ## collection anyway; every other exit from here deletes both
                ## spellings, and this one used to leak a calendar with an '@'
                ## in its name into the user's account.
                self._delete_probe_collection(literal_url)
                return _collection_axis("creating a calendar at the literal '@' path could not be completed")
            if not made and self._propfind_resolves(literal_url):
                ## "There is already a collection here" is a refusal too
                ## (RFC4918 makes that a 405), and it means the opposite of
                ## "the server will not take an '@' in a calendar path".  The
                ## only collection that can be here is a leftover of ours the
                ## DELETE above did not manage to remove - so carry on with it,
                ## which also gets it cleaned up on the way out.
                made = True
            if not made:
                alternative = self._mkcalendar_probe(encoded_url)
                try:
                    if alternative is None:
                        return _collection_axis("creating a calendar at the '%40' path could not be completed")
                    if not alternative:
                        return _collection_axis("the server accepted a probe calendar under neither spelling")
                    if self._propfind_resolves(encoded_url):
                        return _collection_axis(
                            "the server refused a calendar at the literal '@' path and accepted one at '%40'",
                            literal="unsupported",
                            encoded="full",
                        )
                    return _collection_axis(
                        "the server refused a calendar at the literal '@' path and did not answer for the one it accepted at '%40'"
                    )
                finally:
                    self._delete_probe_collection(encoded_url)

            try:
                return self._probe_encode_at_collection_identity(literal_url, encoded_url)
            finally:
                self._delete_probe_collection(encoded_url)
                self._delete_probe_collection(literal_url)
        except Exception:
            return _collection_axis("the url.encode-at collection probe could not be carried out")

    ## ------------------------------------------------------------------
    ## Axis 3: an '@' in the username, as it appears in a server-given path
    ## ------------------------------------------------------------------

    def _encode_at_principal_urls(self):
        """The two spellings of a server-given path that embeds the username's ``@``.

        Returns ``(literal_url, encoded_url, minted)`` where ``minted`` is
        ``"literal"`` or ``"encoded"`` - which of the two the server itself
        handed out - or ``None`` when the question does not arise: a username
        without an ``@``, no principal, or a principal and calendar-home-set
        whose paths do not carry the username at all (plenty of servers address
        the principal by an opaque id).

        ``minted`` is what makes the axis gradeable.  Nothing is ever created
        at the *other* spelling, so a miss there is no evidence at all; see
        ``_probe_encode_at_principal``.

        Only the *path* is looked at.  An ``@`` in the authority is userinfo,
        a different production entirely, and rewriting it would change which
        credentials the request carries rather than which resource it names.
        """
        username = getattr(self.client, "username", None)
        if not username or "@" not in username:
            return None
        principal = getattr(self.checker, "principal", None)
        if principal is None:
            return None

        candidates = []
        for get in (lambda: principal.url, lambda: principal.calendar_home_set.url):
            try:
                candidates.append(str(get()))
            except Exception:
                pass

        for candidate in candidates:
            parts = urlsplit(candidate)
            if "@" in parts.path:
                minted = "literal"
                literal_path, encoded_path = parts.path, parts.path.replace("@", "%40")
            elif "%40" in parts.path:
                minted = "encoded"
                literal_path, encoded_path = parts.path.replace("%40", "@"), parts.path
            else:
                continue
            return (
                urlunsplit(parts._replace(path=literal_path)),
                urlunsplit(parts._replace(path=encoded_path)),
                minted,
            )
        return None

    def _probe_encode_at_principal(self):
        """The username axis: is a server-given path with an ``@`` reachable both ways?

        Read-only, and deliberately no identity verdict.  Identity means "two
        spellings, two resources", and establishing that takes writing a second
        resource at the other spelling - which here would mean creating a
        second principal.  What can be observed is reachability, and that is
        the half that bites: the ownCloud workaround exists because a server
        hands out a calendar-home-set containing a literal ``@`` and then
        refuses to serve it.

        **Only the spelling the server itself minted is graded, and only
        downwards when the other one answered.**  The other spelling names
        nothing this run created, so on an RFC3986-conformant server it is the
        path of a principal that was never made and 404s for that reason alone
        - exactly the confusion the object axis was rewritten to avoid, and
        worse here, since ``encoded: unsupported`` makes the client mint a
        literal ``@`` for every URL from then on.  So:

        * the minted spelling answers - it works, and that is a fact about
          the server, not about a resource we invented;
        * the other spelling answers too - it works as well: nothing was ever
          created there, so the only way it can answer is if the server treats
          the two as the same path;
        * the other spelling misses - no vote, prose only;
        * the minted spelling misses while the other answers - the ownCloud
          breakage: a server refusing to serve the path it handed out;
        * neither answers - the path is unreachable for some reason that is
          not about spelling, and nothing is graded.
        """
        urls = self._encode_at_principal_urls()
        if urls is None:
            return None
        literal_url, encoded_url, minted = urls
        literal = self._propfind_resolves(literal_url)
        encoded = self._propfind_resolves(encoded_url)
        if literal is None or encoded is None:
            return _principal_axis("the PROPFINDs on the two spellings of the principal path could not be completed")
        if literal and encoded:
            return _principal_axis(
                "the server-given path carrying the username's '@' answers under both spellings (whether they are one resource or two is not observable here)",
                literal="full",
                encoded="full",
            )
        if not literal and not encoded:
            return _principal_axis("neither spelling of the server-given path carrying the username's '@' answered")

        answered, other = ("literal", "encoded") if literal else ("encoded", "literal")
        spelling = {"literal": "'@'", "encoded": "'%40'"}
        if answered == minted:
            ## The other spelling missed.  It names a principal nobody ever
            ## created, so on a conformant server that miss is expected and
            ## says nothing about whether the spelling would resolve.
            return _principal_axis(
                f"the server-given path carrying the username's '@' answers under the {spelling[minted]} "
                f"spelling it was handed out in; the {spelling[other]} spelling of it does not, which on a "
                "server that keeps the two apart says nothing, since no principal was ever created there",
                **{minted: "full"},
            )
        ## The server will not serve the path it minted, and the other
        ## spelling of it will.  That is the ownCloud breakage, and it is the
        ## one thing on this axis that can be graded downwards.
        return _principal_axis(
            f"the server hands out a path carrying the username's '@' spelled {spelling[minted]} and then "
            f"serves it only under {spelling[answered]}",
            **{minted: "unsupported", answered: "full"},
        )

    def _check_encode_at(self, calendar):
        """Probe ``url.encode-at``: how does the server treat ``@`` versus ``%40``?

        Two orthogonal questions - which spellings resolve, and whether they
        name one resource or two - asked of three different things a path can
        embed an ``@`` in: an object name, a calendar id, and the username
        inside a path the server itself handed out.  ``_record_encode_at``
        writes what the three saw: ``literal`` gets a key per axis, since
        Stalwart answers it one way for an object name and another for a
        calendar path, while ``identity`` and ``encoded`` are still shared and
        come out ``fragile`` where the axes disagree, rather than letting one
        of them silently win.

        Best-effort throughout: PrepareCalendar provisions the fixtures every
        later check depends on, so nothing in here may raise, and anything
        undeterminable is reported ``unknown`` rather than guessed at.
        """
        self._record_encode_at(
            self._probe_encode_at_object(calendar),
            self._probe_encode_at_collection(),
            self._probe_encode_at_principal(),
        )

    def _check_stable_url(self, calendar):
        """May a client compare a searched object's URL with one it constructed?

        It may when the server reports the object at ``<collection>/<uid>.ics``
        under the collection address the client is holding.  Two different
        things take that away, and they are asked separately here.

        The server may rename the resource - then no client can construct its
        URL at all, and that is this probe's own observation.

        Or the *collection* may have more than one address.  OX serves every
        calendar both at the requested cal_id and at an opaque canonical
        ``cal://0/NNN`` path, and reports objects under the canonical one, so a
        constructed URL matches only when the client happens to hold that
        address - and the library hands out either, depending on how the
        Calendar was obtained: ``make_calendar()`` adopts the canonical URL and
        a listing reports it, while ``principal.calendar(cal_id=...)`` is pure
        URL arithmetic and never round-trips.  Comparing the two URLs is
        therefore not a question about the *server* at all, and this probe used
        to do exactly that: a run that created the test calendar called OX
        stable and a run that found it again by cal_id called the same server
        unstable.

        So that half is read from ``create-calendar.stable-url``, which probes
        it deterministically by creating a calendar and comparing the canonical
        URL segment with the requested cal_id.  Where it says the collection
        was relocated, a client cannot rely on a constructed object URL either,
        whichever address this particular run ended up with.
        """
        stored_as = "csc_simple_event1.ics"
        try:
            server_event = calendar.object_by_uid(stored_as[: -len(".ics")])
            reported_url = str(server_event.url)
            reported_as = unquote(urlsplit(reported_url).path).rstrip("/").rsplit("/", 1)[-1]
        except Exception:
            self.set_feature("save-load.stable-url", None)
            return

        if reported_as != stored_as:
            self.set_feature(
                "save-load.stable-url",
                {
                    "support": "unsupported",
                    "behaviour": (
                        f"the object stored as {stored_as!r} is reported under the name "
                        f"{reported_as!r}; a client cannot construct the URL of an object it saved"
                    ),
                },
            )
            return

        ## An actual negative observation, not merely the absence of one: a
        ## collection nobody managed to probe must not cost this feature its
        ## verdict.
        relocation = self.checker.features_checked.is_supported("create-calendar.stable-url", dict) or {}
        if relocation.get("support") in ("unsupported", "broken", "ungraceful"):
            ## Quote what create-calendar.stable-url observed rather than
            ## restating one server's flavour of it: OX leaves a second working
            ## address behind, Zimbra leaves an alias that 404s on child
            ## objects.  Both make a constructed object URL unreliable, but
            ## "served at more than one address" is only true of the first.
            observed = relocation.get("behaviour")
            self.set_feature(
                "save-load.stable-url",
                {
                    "support": "unsupported",
                    "behaviour": (
                        "the object keeps the name it was stored under, but the collection does "
                        "not stay at the address it was created at (create-calendar.stable-url"
                        + (f": {observed}" if observed else "")
                        + "), so a constructed object URL resolves only from the address the "
                        "server reports objects under, and a client may be holding another one"
                    ),
                },
            )
            return

        client_url = calendar.url.join(stored_as)
        self.set_feature("save-load.stable-url", server_event.url.canonical() == client_url.canonical())

    def _delete_stale_fixtures(self, object_by_uid):
        """Delete leftover fixtures from previous runs that aren't in the current set.

        Only objects whose UID starts with ``csc_`` are removed.  ``object_by_uid``
        may legitimately contain real user data when ``--caldav-calendar`` points
        the tool at a production calendar (a real VJOURNAL, or a real event/task
        dated in the fixture base year that survived the ``add_if_not_existing``
        pops); deleting those would be silent, permanent data loss.  All fixtures
        the tool creates use the ``csc_`` prefix, so the prefix check is sufficient
        to tell ours apart from the user's.  The year-2000 old-date probes fall
        outside the fixture window and are excluded by ``_filter_fixture_window``,
        so they are never in ``object_by_uid`` to begin with.
        """
        for uid, obj in object_by_uid.items():
            if not str(uid).startswith("csc_"):
                continue
            logging.warning("Deleting stale fixture object with UID %s", uid)
            obj.delete()

    def _run_check(self):
        ## NOTE: Any objects created here with a UID starting with "csc_" will be
        ## cleaned up by the csc_* fallback in checker.cleanup(). For servers that
        ## support calendar deletion, the whole calendar is deleted instead.
        ## If you add new objects here, make sure their UIDs start with "csc_".

        ## The reusable fixtures live in next calendar year (see _base_year): near
        ## enough to stay inside sliding-window servers' search windows, stable
        ## enough that the --no-cleanup reuse feature works for a whole year.
        base = _base_year()
        self.checker.fixture_base_year = base

        cal_id = TEST_CALENDAR_CAL_ID
        test_cal_info = self.checker.expected_features.is_supported(
            "test-calendar.compatibility-tests", return_type=dict
        )
        name = test_cal_info.get("name", "Calendar for checking server feature support")

        self._find_or_create_calendar(cal_id, name, test_cal_info)
        calendar = self.checker.calendar

        ## TODO: replace this with one search if possible(?)
        ## Some servers (e.g. CCS) reject time-range queries for some dates
        ## (min-date-time restriction), so fall back to empty lists.
        ## The range only needs to span the fixtures (Jan 1 - Feb 13); we stop at
        ## March 1 rather than year-end so the *end* bound also stays inside a
        ## sliding window (OX rejects a query whose end is >~18 months ahead).
        try:
            events_in_window = calendar.search(event=True, start=datetime(base, 1, 1), end=datetime(base, 3, 1))
        except (AuthorizationError, DAVError):
            events_in_window = []
        try:
            tasks_in_window = calendar.search(todo=True, start=datetime(base, 1, 1), end=datetime(base, 3, 1))
        except (AuthorizationError, DAVError):
            tasks_in_window = []
        ## Some servers silently return empty for time-range queries.  Fall back
        ## to listing all objects and filtering by date so existing fixtures from
        ## a previous run are detected and not re-PUT.
        if not events_in_window and not tasks_in_window:
            try:
                events_in_window = calendar.events()
            except (AuthorizationError, DAVError):
                pass
            try:
                tasks_in_window = self.checker.tasklist.todos()
            except (AuthorizationError, DAVError):
                pass
        try:
            journals_in_window = calendar.journals()
        except (AuthorizationError, DAVError):
            journals_in_window = []

        object_by_uid = {}

        for obj in _filter_fixture_window(events_in_window + tasks_in_window, base):
            object_by_uid[obj.component["uid"]] = obj
        for obj in journals_in_window:
            try:
                object_by_uid[obj.component["uid"]] = obj
            except Exception:
                pass

        def add_if_not_existing(*largs, **kwargs):
            if largs[0] == Todo:
                cal = self.checker.tasklist
            elif largs[0] == Journal:
                cal = self.checker.journallist
            else:
                cal = self.checker.calendar
            if "uid" in kwargs:
                uid = kwargs["uid"]
            elif not kwargs:
                uid = re.search("UID:(.*)\n", largs[1]).group(1)
            if uid in object_by_uid:
                return object_by_uid.pop(uid)
            try:
                return cal.save_object(*largs, **kwargs)
            except PutError:
                ## 409 Conflict: object exists but is hidden from search
                ## (e.g. OX's sliding window hides old objects from REPORT/PROPFIND).
                ## Try to load the existing object by constructing its URL directly.
                obj_class = largs[0]
                existing = url_object(cal, uid, obj_class)
                existing.load()
                return existing

        if not self._create_test_events(calendar, cal_id, name, add_if_not_existing):
            return

        self._delete_stale_fixtures(object_by_uid)
        if not self.checker.calendar.events():
            logging.error("Calendar appears empty after PrepareCalendar; subsequent checks may be unreliable")
        ## Not asserting on tasklist.todos() here - on servers with broken
        ## comp-type filtering (e.g. Bedework), todos() returns empty even
        ## though todos were saved successfully (verified via load() above).

        self._check_get_by_url(calendar)
        self._check_stable_url(calendar)
        self._check_encode_at(calendar)


class CheckTodoNoDtstartSearch(Check):
    """
    Checks whether a VTODO without DTSTART (but with DUE) is returned by a
    closed date-range search.  RFC5545 / RFC4791 section 9.9 say such a task
    should be found (it has a defined end), but some servers (Davical, Stalwart,
    Synology) skip any task lacking DTSTART.  Replaces the old
    'vtodo_datesearch_nodtstart_task_is_skipped' flag.

    Uses the PrepareCalendar fixture csc_simple_task2 (DUE=<base>-01-08, no
    DTSTART) and a closed window around it.
    """

    depends_on = {PrepareCalendar}
    features_to_be_checked = {"search.time-range.todo.no-dtstart"}

    def _run_check(self) -> None:
        feature = "search.time-range.todo.no-dtstart"
        tasklist = getattr(self.checker, "tasklist", None)
        if tasklist is None:
            return
        ## If generic todo time-range search is broken or unsupported (e.g. Bedework),
        ## the server returns all objects regardless of the date filter, so finding
        ## csc_simple_task2 tells us nothing about no-DTSTART handling specifically.
        ## Leave the feature unset so the parent status propagates.
        if not self.checker.features_checked.is_supported("search.time-range.todo"):
            return
        ## Use the same base year PrepareCalendar placed the fixtures in, not a
        ## fresh _base_year(), so a run that crosses New Year's midnight still
        ## searches the window the fixtures actually live in.
        base = self.checker.fixture_base_year
        try:
            results = tasklist.search(
                todo=True,
                start=datetime(base, 1, 1, tzinfo=utc),
                end=datetime(base, 1, 31, tzinfo=utc),
                include_completed=True,
            )
        except (DAVError, AuthorizationError):
            return  ## cannot probe; leave unset so the default ("full") applies
        found = any("csc_simple_task2" in (getattr(o, "data", "") or "") for o in results)
        self.set_feature(feature, found)


def dav_error_status(exc):
    """The HTTP status a ``DAVError`` carries, when it carries one at all.

    There is no structured status on the exception: ``DAVObject`` raises its
    errors as ``SomeError(errmsg(response))``, and ``errmsg`` renders
    ``"<status> <reason>\n\n<body>"`` into what the exception then stores as
    its ``url``.  So the status has to be read back off that string, and
    anything that does not start with one gives ``None``.

    Worth the ugliness because a 5xx means the server broke, not that it
    answered the wrong error - and this suite's verdicts are copied into
    server profiles, where "answers 403 instead of 404" is read as a
    statement about the server rather than about the minute it was probed.
    """
    match = re.match(r"\s*(\d{3})\b", str(getattr(exc, "url", "") or ""))
    if not match:
        return None
    return int(match.group(1))


class CheckNonExistingResource(Check):
    """
    Checks what happens when something that does not exist is looked up.  The
    expected behaviour is a 404 (NotFoundError); some servers answer 403
    instead (e.g. Robur, probably to avoid leaking whether a resource exists).
    Replaces the old 'non_existing_raises_other' flag.

    A missing calendar *object* and a missing calendar *collection* get separate
    features, because servers do not necessarily treat them alike - and neither
    does the caldav library.  What we can observe is the exception the client
    ends up raising, not the raw status code: ``load()`` retries a failed GET as
    a calendar-multiget REPORT against the parent calendar, so a server
    answering 403 on the object URL still raises NotFoundError if it reports the
    missing href with a 404 inside the multistatus (Robur does exactly that).  A
    missing collection has no such fallback, hence the difference.
    """

    depends_on = {PrepareCalendar, CheckMakeDeleteCalendar}
    features_to_be_checked = {
        "non-existing-raises-not-found.object",
        "non-existing-raises-not-found.collection",
    }

    ## Prefixed so the cleanup sweep would recognise it, in the unlikely event
    ## that a server creates it behind our back (see PROBE_CALENDAR_PREFIXES).
    MISSING_CAL_ID = "caldav-server-checker-does-not-exist"

    def _run_check(self) -> None:
        self._probe_missing_object()
        self._probe_missing_collection()

    NO_ERROR = {"support": "broken", "behaviour": "no error raised for a non-existing resource"}

    @staticmethod
    def _deviation_or_outage(exc, deviation):
        """Classify a DAVError as a deviation from the RFC or as a bad minute.

        ``deviation`` is the behaviour text for the former, with ``{name}``
        standing in for the exception class.
        """
        status = dav_error_status(exc)
        if status is not None and status >= 500:
            return {
                "support": "unknown",
                "behaviour": f"cannot test, the server answered {status} ({type(exc).__name__}) - possibly transient",
            }
        return {"support": "unsupported", "behaviour": deviation.format(name=type(exc).__name__)}

    def _probe_missing_object(self) -> None:
        feature = "non-existing-raises-not-found.object"
        cal = self.checker.calendar
        missing = url_object(cal, "csc_does_not_exist_probe")

        ## Ask the server first, with the library's rescue switched off:
        ## load() retries a failed GET as a calendar-multiget REPORT against
        ## the parent collection, and a server that answers 403 for anything
        ## non-existing (Robur) is reported as a 404 inside that multistatus.
        ## Probing through the rescue can therefore only ever observe "full".
        raw_error = None
        try:
            missing.load(multiget_fallback=False)
        except NotFoundError:
            self.set_feature(feature, True)
            return
        except DAVError as e:
            ## AuthorizationError (a DAVError subclass) is the common case: a
            ## 403 rather than a 404, which is a legitimate choice.
            raw_error = type(e).__name__
        else:
            self.set_feature(feature, self.NO_ERROR)
            return

        ## The server did not answer 404 - but the caller may still get the
        ## NotFoundError it expects, out of the multiget fallback.  That is a
        ## quirk rather than a breakage: nothing downstream has to care.
        try:
            missing.load()
        except NotFoundError:
            self.set_feature(
                feature,
                {
                    "support": "quirk",
                    "behaviour": f"a direct lookup raises {raw_error}; the NotFoundError comes out of load()'s calendar-multiget fallback, where the server reports the missing href with an inner 404",
                },
            )
        except DAVError as e:
            self.set_feature(feature, self._deviation_or_outage(e, "raises {name} instead of NotFoundError"))
        else:
            self.set_feature(feature, self.NO_ERROR)

    def _probe_missing_collection(self) -> None:
        feature = "non-existing-raises-not-found.collection"
        ## On a server that creates calendars on first access, looking up a
        ## non-existing calendar would create the very thing we are probing for.
        ## Record it unknown rather than leaving it unset: its sibling says
        ## nothing about it, so there is nothing to fall back on.
        if self.checker.features_checked.is_supported("create-calendar.auto"):
            self.set_feature(
                feature,
                {
                    "support": "unknown",
                    "behaviour": "not probed: the server creates calendars on first access, so looking one up would create it",
                },
            )
            return
        ## ...and "we never found out whether it auto-creates" is not licence to
        ## try: is_supported() is False for unknown just as for unsupported, so
        ## a CheckMakeDeleteCalendar that raised would let the probe create the
        ## very calendar it then reports as non-existing.
        if self.feature_undeterminated("create-calendar.auto"):
            self.set_feature(
                feature,
                {
                    "support": "unknown",
                    "behaviour": "not probed: create-calendar.auto was never established, so a lookup might create the calendar",
                },
            )
            return
        missing = self.checker.principal.calendar(cal_id=self.MISSING_CAL_ID)
        try:
            missing.get_events()
        except NotFoundError:
            self.set_feature(feature, True)
        except DAVError as e:
            self.set_feature(
                feature,
                self._deviation_or_outage(e, "a non-existing calendar raises {name} instead of NotFoundError"),
            )
        else:
            self.set_feature(
                feature,
                {"support": "broken", "behaviour": "no error raised for a non-existing calendar"},
            )


class CheckMutable(Check):
    """
    Checks whether the server allows modification of existing calendar objects.

    Modifies the SUMMARY of csc_simple_event1, saves it, reloads it, and
    verifies the server accepted the update.  Replaces the old 'no_overwrite'
    compatibility flag.
    """

    depends_on = {PrepareCalendar}
    features_to_be_checked = {"save-load.mutable"}

    def _run_check(self) -> None:
        cal = self.checker.calendar
        uid = "csc_simple_event1"

        ## OX answers a UID calendar-query with 403 Forbidden rather than a 404,
        ## so fall back to a direct-URL GET on any lookup failure.
        try:
            event = cal.event_by_uid(uid)
        except (NotFoundError, AuthorizationError, DAVError):
            event = url_object(cal, uid)
            event.load()

        vevent = event.icalendar_instance.walk("VEVENT")[0]
        original_summary = str(vevent.get("SUMMARY", ""))
        modified_summary = original_summary + " (mutable-check)"

        try:
            vevent["SUMMARY"] = modified_summary
            event.save()
            event.load()

            vevent_reloaded = event.icalendar_instance.walk("VEVENT")[0]
            current_summary = str(vevent_reloaded.get("SUMMARY", ""))

            if current_summary == modified_summary:
                self.set_feature("save-load.mutable")
            else:
                self.set_feature(
                    "save-load.mutable",
                    {"support": "broken", "behaviour": "modification not reflected after save and reload"},
                )
        except (DAVError, AuthorizationError):
            self.set_feature("save-load.mutable", False)
        finally:
            try:
                vevent_restore = event.icalendar_instance.walk("VEVENT")[0]
                vevent_restore["SUMMARY"] = original_summary
                event.save()
            except Exception:
                pass


class CheckCalendarProperties(Check):
    """
    Checks support for the nonstandard Apple/Mozilla calendar collection
    properties calendar-color and calendar-order.

    These are not described by RFC4791/RFC5545 but are supported by quite some
    server implementations.  Replaces the old 'calendar_color' / 'calendar_order'
    compatibility flags.

    Each property is probed by setting two distinct values in turn and reading
    them back, which tells the four cases apart:
      * the stored value tracks the input verbatim -> full
      * the stored value tracks the input but the server transformed it (e.g. a
        colour name normalised to its hex form) -> full, with a behaviour note
      * the same value comes back regardless of what was set (the property is
        effectively read-only) -> broken, with a behaviour note
      * nothing is stored, or the server rejects the property with an error ->
        unsupported (rejecting a nonstandard extension is not an RFC breach)

    calendar-color is probed both with a colour name ("blue") and with a hex
    value (calendar-color.hex); some servers accept one form but not the other.
    """

    depends_on = {PrepareCalendar}
    features_to_be_checked = {"calendar-color", "calendar-color.hex", "calendar-order"}

    def _run_check(self) -> None:
        cal = self.checker.calendar
        self._probe("calendar-color", ical.CalendarColor, "blue", "green", cal)
        self._probe("calendar-color.hex", ical.CalendarColor, "#FF0000FF", "#00FF00FF", cal)
        self._probe("calendar-order", ical.CalendarOrder, "12", "34", cal)

    def _probe(self, feature, element, v1, v2, cal) -> None:
        ## The probe works by *writing* the property, so whatever was there
        ## first has to go back.  --caldav-calendar may well point at the
        ## user's real calendar (it is the documented mode for servers without
        ## MKCALENDAR), and silently repainting it would be exactly the kind of
        ## damage the rest of this tool takes care to avoid.
        try:
            original = cal.get_properties([element()]).get(element.tag)
        except (DAVError, AuthorizationError):
            original = None
        try:
            got1 = self._set_get(element, v1, cal)
            got2 = self._set_get(element, v2, cal)
        except (DAVError, AuthorizationError):
            ## The server declined the nonstandard property with an error.
            self.set_feature(feature, False)
            return
        finally:
            self._restore(element, original, cal)
        if not got1 and not got2:
            ## The property was silently dropped.
            self.set_feature(feature, False)
        elif got1 == v1 and got2 == v2:
            ## Round-trips verbatim.
            self.set_feature(feature, True)
        elif got1 != got2:
            ## The stored value tracks the input but was transformed by the
            ## server (e.g. the colour name "blue" normalised to a hex value).
            self.set_feature(
                feature,
                {"support": "full", "behaviour": f"value normalised ({v1!r} stored as {got1!r})"},
            )
        else:
            ## The same value comes back no matter what we set: read-only.
            self.set_feature(
                feature,
                {"support": "broken", "behaviour": f"read-only (set {v1!r}/{v2!r}, both return {got1!r})"},
            )

    def _set_get(self, element, value, cal):
        cal.set_properties([element(value)])
        return cal.get_properties([element()]).get(element.tag)

    def _restore(self, element, original, cal) -> None:
        """Put the property back the way the probe found it.

        A calendar that had no such property keeps none: the empty value is
        what a client sends to clear it, and a server that refuses either way
        leaves us no better option than the warning.
        """
        try:
            cal.set_properties([element(original if original is not None else "")])
        except (DAVError, AuthorizationError):
            logging.warning(
                "Could not restore %s on %s after probing it; it may be left at the probe value",
                element.tag,
                getattr(cal, "url", "the calendar"),
            )


class CheckIfMatchOptional(Check):
    """
    Checks whether an existing object can be repeatedly overwritten by a fresh
    PUT that carries no If-Match etag.

    OX App Suite enforces optimistic concurrency and rejects such a blind
    overwrite with 409 Conflict (it tolerates the first overwrite, then refuses
    subsequent no-If-Match PUTs).  An etag-conditional save() still works, so
    save-load.mutable stays full - this is a distinct, finer capability.
    """

    depends_on = {PrepareCalendar}
    features_to_be_checked = {"save-load.mutable.if-match-optional"}

    def _run_check(self) -> None:
        cal = self.checker.calendar
        uid = "csc_put_overwrite"
        ts = (datetime.now(tz=utc) + timedelta(days=30)).strftime("%Y%m%dT%H%M%SZ")
        end = (datetime.now(tz=utc) + timedelta(days=30, hours=1)).strftime("%Y%m%dT%H%M%SZ")
        data = (
            "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//caldav-server-tester//EN\n"
            f"BEGIN:VEVENT\nUID:{uid}\nDTSTAMP:{ts}\nDTSTART:{ts}\nDTEND:{end}\n"
            "SUMMARY:put-overwrite check\nEND:VEVENT\nEND:VCALENDAR\n"
        )
        ## Use a dedicated, freshly-created object so the result is independent of
        ## how many times the shared probe events have already been PUT.
        try:
            cal.add_event(data)
        except (DAVError, PutError):
            self.set_feature("save-load.mutable.if-match-optional", None)
            return

        try:
            ## Two blind (no If-Match) overwrites - servers that enforce optimistic
            ## concurrency (e.g. OX) tolerate the first then reject the second with 409.
            for _ in range(2):
                Event(cal.client, data=data, parent=cal).save()
            self.set_feature("save-load.mutable.if-match-optional")
        except (DAVError, PutError):
            self.set_feature("save-load.mutable.if-match-optional", False)
        finally:
            try:
                url_object(cal, uid).delete()
            except Exception:
                pass


class CheckAttendeePartstat(Check):
    """
    Checks whether an attendee's PARTSTAT can be modified via a direct PUT.

    OX App Suite forbids this (403 Forbidden, even with a matching etag) and
    expects the change to be made through iTIP scheduling instead.
    """

    depends_on = {PrepareCalendar}
    features_to_be_checked = {"save-load.mutable.attendee-partstat"}

    def _run_check(self) -> None:
        cal = self.checker.calendar
        start = datetime.now(tz=utc) + timedelta(days=30)
        try:
            ev = cal.add_event(
                uid="csc_attendee_partstat",
                dtstart=start,
                dtend=start + timedelta(hours=1),
                summary="attendee partstat check",
                ical_fragment=("ATTENDEE;ROLE=OPT-PARTICIPANT;PARTSTAT=TENTATIVE:MAILTO:csc-attendee@example.com"),
            )
        except (DAVError, PutError):
            self.set_feature("save-load.mutable.attendee-partstat", None)
            return

        try:
            ev.change_attendee_status(attendee="csc-attendee@example.com", PARTSTAT="ACCEPTED")
        except Exception:
            self.set_feature("save-load.mutable.attendee-partstat", None)
            try:
                ev.delete()
            except Exception:
                pass
            return

        try:
            ev.save()
            self.set_feature("save-load.mutable.attendee-partstat")
        except (DAVError, AuthorizationError):
            self.set_feature("save-load.mutable.attendee-partstat", False)
        finally:
            try:
                ev.delete()
            except Exception:
                pass


class CheckRescheduleRecurrenceSeries(Check):
    """
    Checks whether the whole of a recurring event can be rescheduled - i.e. the
    master VEVENT's DTSTART moved (re-anchoring the series) - while a detached
    exception VEVENT (with RECURRENCE-ID) is attached.

    The probe PUTs the rescheduled series back with a matching If-Match etag, so
    it is independent of save-load.mutable.if-match-optional.  OX App Suite
    rejects this re-anchoring with 409 Conflict even with the etag, although
    rescheduling an exception-free recurring event works fine there.  Exercised
    in the wild by ``save(all_recurrences=True)`` after a dtstart/dtend change.
    """

    depends_on = {PrepareCalendar}
    features_to_be_checked = {"save-load.event.recurrences.exception.reschedule"}

    def _run_check(self) -> None:
        feature = "save-load.event.recurrences.exception.reschedule"

        ## Only meaningful when the server keeps master + exception together;
        ## otherwise there is no series to re-anchor.
        if not self.feature_checked("save-load.event.recurrences.exception"):
            self.set_feature(feature, None)
            return

        cal = self.checker.calendar
        uid = "csc_reschedule_series"

        ## A safe day-of-month (<= 28) so the MONTHLY recurrence is well defined,
        ## comfortably in the future so OX's sliding window keeps it visible.
        start = (datetime.now(tz=utc) + timedelta(days=45)).replace(day=10, hour=12, minute=0, second=0, microsecond=0)
        if start.month == 12:
            second = start.replace(year=start.year + 1, month=1)
        else:
            second = start.replace(month=start.month + 1)

        def f(dt):
            return dt.strftime("%Y%m%dT%H%M%SZ")

        def ics(master_start, rid, exc_start, sequence):
            hour = timedelta(hours=1)
            return (
                "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//caldav-server-tester//EN\n"
                f"BEGIN:VEVENT\nUID:{uid}\nDTSTAMP:{f(start)}\n"
                f"DTSTART:{f(master_start)}\nDTEND:{f(master_start + hour)}\n"
                f"SEQUENCE:{sequence}\nRRULE:FREQ=MONTHLY\n"
                "SUMMARY:reschedule series check\nEND:VEVENT\n"
                f"BEGIN:VEVENT\nUID:{uid}\nDTSTAMP:{f(start)}\n"
                f"RECURRENCE-ID:{f(rid)}\n"
                f"DTSTART:{f(exc_start)}\nDTEND:{f(exc_start + hour)}\n"
                f"SEQUENCE:{sequence}\nSUMMARY:exception occurrence\nEND:VEVENT\n"
                "END:VCALENDAR\n"
            )

        original = ics(start, second, second, 0)
        ## Re-anchor the whole series one hour later; the exception's RECURRENCE-ID
        ## is shifted to keep lining up with the moved series.
        rescheduled = ics(
            start + timedelta(hours=1),
            second + timedelta(hours=1),
            second + timedelta(hours=1),
            1,
        )

        try:
            cal.add_event(original)
        except (DAVError, PutError):
            self.set_feature(feature, None)
            return

        try:
            ## Reload to obtain a fresh etag, then PUT the rescheduled series with
            ## that etag (If-Match), isolating this from if-match-optional.
            obj = url_object(cal, uid)
            obj.load()
            if not obj.etag:
                self.set_feature(feature, None)
                return
            obj.data = rescheduled
            obj.save()
            self.set_feature(feature)
        except (DAVError, PutError):
            self.set_feature(feature, False)
        finally:
            try:
                url_object(cal, uid).delete()
            except Exception:
                pass


class CheckSearch(Check):
    depends_on = {PrepareCalendar}
    features_to_be_checked = {
        "search.time-range.event",
        "search.time-range.event.old-dates",
        "search.text.category",
        "search.time-range.todo",
        "search.time-range.todo.old-dates",
        "search.comp-type",
        "search.comp-type.optional",
        "search.time-range.comp-type-optional",
        "search.text.comp-type-optional",
        "search.combined-is-logical-and",
        "search.unlimited-time-range",
    }  ## TODO: we can do so much better than this

    def _check_time_range_with_recent_data(self, cal, tasklist):
        """Test time-range searches using temporary near-future objects.

        Some servers (e.g. CCS) enforce min-date-time restrictions and
        reject queries for old dates, but work fine with recent dates.
        This check distinguishes "time-range unsupported" from
        "time-range works but only for recent dates".
        """
        now = datetime.now(tz=utc)
        tomorrow = now + timedelta(days=1)
        day_after = now + timedelta(days=2)
        recent_event = None
        recent_task = None
        ## Use unique UIDs to avoid conflicts on servers that enforce unique
        ## UIDs across calendars (e.g. Nextcloud with unique_calendar_ids)
        recent_uid_suffix = uuid.uuid4().hex[:8]

        ## Test event time-range with a recent event
        try:
            recent_event = cal.save_object(
                Event,
                summary="recent time-range check event",
                uid=f"csc_recent_timerange_event_{recent_uid_suffix}",
                dtstart=datetime(tomorrow.year, tomorrow.month, tomorrow.day, 12, 0, 0, tzinfo=utc),
                dtend=datetime(tomorrow.year, tomorrow.month, tomorrow.day, 13, 0, 0, tzinfo=utc),
            )
            events = cal.search(
                start=datetime(tomorrow.year, tomorrow.month, tomorrow.day, 11, 0, 0, tzinfo=utc),
                end=datetime(day_after.year, day_after.month, day_after.day, 0, 0, 0, tzinfo=utc),
                event=True,
                post_filter=False,
            )
            self.set_feature("search.time-range.event", len(events) >= 1)
        except (AuthorizationError, DAVError):
            self.set_feature("search.time-range.event", "ungraceful")
        finally:
            if recent_event:
                try:
                    recent_event.delete()
                except Exception:
                    pass

        ## Test todo time-range with a recent task
        try:
            recent_task = tasklist.save_object(
                Todo,
                summary="recent time-range check task",
                uid=f"csc_recent_timerange_task_{recent_uid_suffix}",
                dtstart=datetime(tomorrow.year, tomorrow.month, tomorrow.day, 12, 0, 0, tzinfo=utc),
                due=datetime(tomorrow.year, tomorrow.month, tomorrow.day, 13, 0, 0, tzinfo=utc),
            )
            tasks = tasklist.search(
                start=datetime(tomorrow.year, tomorrow.month, tomorrow.day, 11, 0, 0, tzinfo=utc),
                end=datetime(day_after.year, day_after.month, day_after.day, 0, 0, 0, tzinfo=utc),
                todo=True,
                include_completed=True,
                post_filter=False,
            )
            self.set_feature("search.time-range.todo", len(tasks) >= 1)
        except (AuthorizationError, DAVError):
            self.set_feature("search.time-range.todo", "ungraceful")
        finally:
            if recent_task:
                try:
                    recent_task.delete()
                except Exception:
                    pass

    def _comptype_search_calendars(self):
        """The distinct calendars PrepareCalendar populated.

        Events, tasks and journals may all live in one calendar, or be split
        across up to three (e.g. servers that reject VTODO/VJOURNAL in a mixed
        calendar get a dedicated ``..._tasks`` / ``..._journals`` calendar).
        """
        cals = []
        for c in (self.checker.calendar, self.checker.tasklist, getattr(self.checker, "journallist", None)):
            if c is not None and not any(c is seen for seen in cals):
                cals.append(c)
        return cals

    def _probe_comptype(self):
        """Set ``search.comp-type``: when a calendar-query DOES specify a
        component type, does the server return only objects of that type?

        Per RFC4791 the server MUST filter a calendar-query by the requested
        component type.  Some servers misclassify (e.g. Bedework returns
        VTODOs for a VEVENT query) or ignore the comp-filter entirely and
        return everything.  When that happens the library falls back to
        client-side filtering, so we send the query with
        ``compatibility_workarounds=False`` to observe the raw server
        behaviour rather than the workaround.

        Supported (``full``) means every comp-type-specific query returned
        only objects of the requested type, across every distinct calendar the
        checker populated.  When a typed query returns wrong-typed objects we
        use the comp-type-LESS listing of the same calendar as ground truth to
        tell two very different behaviours apart:

        * ``unsupported`` - the server silently ignores the comp-filter and
          returns the whole calendar regardless of the requested type.  The
          right-typed objects are all still there, so the library recovers the
          correct result by post-filtering; the server-side feature is simply
          absent.  (Open-Xchange behaves this way.)
        * ``broken`` - a typed query *drops* correctly-typed objects that the
          calendar demonstrably contains (e.g. Bedework returns nothing for a
          VTODO query), so the filter actively produces wrong results that the
          client cannot recover from.

        If no typed query returns anything (nothing to judge from) the result
        is left ``unknown``.
        """
        wanted = {
            "VEVENT": {"event": True},
            "VTODO": {"todo": True, "include_completed": True},
            "VJOURNAL": {"journal": True},
        }
        base = self.checker.fixture_base_year
        try:
            seen_any = False
            mismatches = set()
            ## a typed query dropped correctly-typed objects that the calendar
            ## demonstrably contains -> the filter actively breaks results
            data_loss = set()
            ## a typed query returned wrong-typed objects but still all the
            ## right-typed ones -> the filter is silently ignored (unsupported)
            filter_ignored = False
            for c in self._comptype_search_calendars():
                ## The comp-type-LESS listing is the ground truth for what this
                ## calendar holds, and for each object's real component type.
                try:
                    bare = list(
                        _filter_fixture_window(c.search(post_filter=False, compatibility_workarounds=False), base)
                    )
                except Exception:
                    bare = None
                present: dict[str, set[str]] = {}
                for obj in bare or []:
                    present.setdefault(obj.component.name, set()).add(str(obj.url))
                have_truth = bool(present)
                for comp_name, kwargs in wanted.items():
                    try:
                        results = list(
                            _filter_fixture_window(
                                c.search(post_filter=False, compatibility_workarounds=False, **kwargs), base
                            )
                        )
                    except Exception:
                        ## The server may refuse to search for a component type
                        ## it does not support at all (e.g. VJOURNAL on CCS); it
                        ## cannot misclassify what it won't search for, so skip
                        ## it as a reference.
                        continue
                    typed_urls = {str(obj.url) for obj in results}
                    wrong = {str(obj.url) for obj in results if obj.component.name != comp_name}
                    if results:
                        seen_any = True
                    for obj in results:
                        if obj.component.name != comp_name:
                            mismatches.add(f"a {comp_name} query returned a {obj.component.name}")
                    if have_truth:
                        seen_any = True
                        missing = present.get(comp_name, set()) - typed_urls
                        if missing:
                            data_loss.add(
                                f"a {comp_name} query dropped {len(missing)} object(s) of that "
                                "type present in the calendar"
                            )
                        if wrong:
                            filter_ignored = True
                    elif wrong:
                        ## No comp-type-less reference to judge from; fall back to
                        ## the old, conservative reading (any wrong type = broken).
                        data_loss.add(
                            f"a {comp_name} query returned a wrong-typed object (no comp-type-less reference)"
                        )
            if not seen_any:
                self.set_feature("search.comp-type", None)
            elif data_loss:
                self.set_feature(
                    "search.comp-type",
                    {"support": "broken", "behaviour": "; ".join(sorted(mismatches | data_loss))},
                )
            elif filter_ignored:
                self.set_feature(
                    "search.comp-type",
                    {
                        "support": "unsupported",
                        "behaviour": "comp-filter silently ignored - server returns the whole "
                        "calendar regardless of the requested component type: " + "; ".join(sorted(mismatches)),
                    },
                )
            else:
                self.set_feature("search.comp-type")
        except Exception:
            self.set_feature("search.comp-type", {"support": "ungraceful"})

    def _probe_comptype_optional(self):
        """Set ``search.comp-type.optional``: when a calendar-query omits the
        component type, does the server still return every object?

        The reference set is what the comp-type-*specific* queries (events +
        todos + journals) return across every distinct calendar the checker
        populated.  An earlier version compared a single-calendar comp-type-less
        search against a global object counter that aggregated objects across the
        event/task/journal calendars (and could include objects that failed to
        save), producing spurious "fragile"/"ungraceful" verdicts on every server
        that stores journals or tasks in a separate calendar.
        See https://github.com/python-caldav/caldav/issues/681

        This must be tested WITHOUT a time-range: a comp-type-less query that
        carries a time-range is a separate feature
        (search.time-range.comp-type-optional below), because RFC4791 section 9.7
        forbids a CALDAV:time-range directly under VCALENDAR.
        ``compatibility_workarounds=False`` makes the library send the bare
        comp-type-less query verbatim instead of splitting it per component type.
        """
        base = self.checker.fixture_base_year
        try:
            bare_urls = set()
            typed_urls = set()
            typed_todo_urls = set()
            for c in self._comptype_search_calendars():
                for obj in _filter_fixture_window(c.search(post_filter=False, compatibility_workarounds=False), base):
                    bare_urls.add(str(obj.url))
                for kwargs in (
                    {"event": True},
                    {"todo": True, "include_completed": True},
                    {"journal": True},
                ):
                    try:
                        for obj in _filter_fixture_window(c.search(post_filter=False, **kwargs), base):
                            url = str(obj.url)
                            typed_urls.add(url)
                            if "todo" in kwargs:
                                typed_todo_urls.add(url)
                    except Exception:
                        ## A component type the server does not support at all
                        ## (e.g. VJOURNAL on CCS) - just skip it as a reference.
                        pass

            if not bare_urls:
                self.set_feature(
                    "search.comp-type.optional",
                    {
                        "support": "unsupported",
                        "description": "search that does not include comptype yields nothing",
                    },
                )
            elif typed_urls <= bare_urls:
                ## the comp-type-less query returned (at least) everything the
                ## comp-type-specific queries returned
                self.set_feature("search.comp-type.optional")
            elif typed_todo_urls and not (typed_todo_urls <= bare_urls):
                self.set_feature(
                    "search.comp-type.optional",
                    {
                        "support": "fragile",
                        "description": "search that does not include comptype does not yield tasks",
                    },
                )
            else:
                self.set_feature(
                    "search.comp-type.optional",
                    {
                        "support": "fragile",
                        "description": "unexpected results from date-search without comp-type",
                    },
                )
        except Exception:
            self.set_feature("search.comp-type.optional", {"support": "ungraceful"})

    def _run_check(self):
        cal = self.checker.calendar
        tasklist = self.checker.tasklist
        base = self.checker.fixture_base_year

        ## First, test time-range with recent dates (near-future).
        ## This determines the base search.time-range.event/todo support.
        self._check_time_range_with_recent_data(cal, tasklist)

        ## Then test with old dates (year 2000) for the .old-dates sub-feature,
        ## using the dedicated csc_olddate_* probes that PrepareCalendar PUT far
        ## in the past.  Servers with a sliding window (e.g. OX) hide those.
        ##
        ## "Works" means BOTH: the old-date probe is returned, AND a definite
        ## near-future object is correctly EXCLUDED.  Checking only presence is
        ## fooled two ways: a next-year DUE-only task is open-start (-inf..DUE) on
        ## some servers (Zimbra) and legitimately overlaps a year-2000 range, while
        ## a server with broken VTODO time-range (OX) ignores the range and returns
        ## every task - so the probe is trivially "present".  Requiring a
        ## definite-future fixture (csc_simple_event1 / csc_simple_task3, both with
        ## an explicit next-year DTSTART) to be absent rules both out.
        def _found(objs, uid):
            for o in objs:
                try:
                    if str(o.icalendar_component.get("UID", "")) == uid:
                        return True
                except Exception:
                    pass
            return False

        try:
            events = cal.search(
                start=datetime(2000, 1, 1, tzinfo=utc),
                end=datetime(2000, 1, 2, tzinfo=utc),
                event=True,
                post_filter=False,
            )
            old_event_ok = _found(events, OLDDATE_EVENT_UID) and not _found(events, "csc_simple_event1")
            self.set_feature("search.time-range.event.old-dates", old_event_ok)
        except (AuthorizationError, DAVError):
            self.set_feature("search.time-range.event.old-dates", "ungraceful")

        try:
            tasks = tasklist.search(
                start=datetime(2000, 1, 9, tzinfo=utc),
                end=datetime(2000, 1, 10, tzinfo=utc),
                todo=True,
                include_completed=True,
                post_filter=False,
            )
            old_todo_ok = _found(tasks, OLDDATE_TASK_UID) and not _found(tasks, "csc_simple_task3")
            self.set_feature("search.time-range.todo.old-dates", old_todo_ok)
        except (AuthorizationError, DAVError):
            self.set_feature("search.time-range.todo.old-dates", "ungraceful")

        ## search.text.category
        try:
            events = cal.search(category="hands", event=True, post_filter=False)
            self.set_feature("search.text.category", len(events) == 1)
        except (ReportError, AuthorizationError, DAVError):
            self.set_feature("search.text.category", "ungraceful")
        ## search.combined - category "hands" AND a time-range.  Uses the
        ## in-window "hands" fixture (csc_event_with_categories, <base>-01-07), so
        ## it works regardless of old-date support; only category search is needed.
        if self.feature_checked("search.text.category"):
            try:
                events1 = cal.search(
                    category="hands",
                    event=True,
                    start=datetime(base, 1, 1, 11, 0, 0),
                    end=datetime(base, 1, 13, 14, 0, 0),
                    post_filter=False,
                )
                events2 = cal.search(
                    category="hands",
                    event=True,
                    start=datetime(base, 1, 1, 9, 0, 0),
                    end=datetime(base, 1, 6, 14, 0, 0),
                    post_filter=False,
                )
                self.set_feature("search.combined-is-logical-and", len(events1) == 1 and len(events2) == 0)
            except (AuthorizationError, DAVError):
                self.set_feature("search.combined-is-logical-and", "ungraceful")
        else:
            ## Can't test combined search without category search support
            self.set_feature("search.combined-is-logical-and", None)

        ## search.comp-type: does a query that DOES specify a component type
        ## return only objects of that type? (see _probe_comptype for details)
        self._probe_comptype()

        ## search.comp-type.optional (see _probe_comptype_optional for details)
        self._probe_comptype_optional()

        ## search.time-range.comp-type-optional: does the server accept a
        ## calendar-query that carries a time-range but does NOT specify a
        ## component type?  RFC4791 section 9.7 only allows a CALDAV:time-range
        ## inside a comp-filter for VEVENT/VTODO/VJOURNAL/VFREEBUSY/VALARM, never
        ## directly under VCALENDAR - so 'unsupported' here is FULLY RFC-COMPLIANT
        ## and not a server defect (SabreDAV-based servers such as Baikal reject it
        ## with HTTP 400 "You cannot add time-range filters on the VCALENDAR
        ## component").  compatibility_workarounds=False sends the raw query so we
        ## observe the server's actual reaction rather than the library's
        ## per-component-type split.  Recent dates are used to avoid conflating with
        ## old-date restrictions.  See
        ## https://github.com/python-caldav/caldav/issues/681
        ## We must verify that a known in-range object is actually RETURNED, not
        ## merely that the query did not raise: some servers (e.g. SOGo) accept the
        ## query without error but silently return nothing when no component type is
        ## given, which is not real support.
        now = datetime.now(tz=utc)
        tr_uid = f"csc_tr_comptype_optional_{uuid.uuid4().hex[:8]}"
        tr_event = None
        try:
            tr_event = cal.save_object(
                Event,
                summary="time-range comp-type-optional check event",
                uid=tr_uid,
                dtstart=datetime(now.year, now.month, now.day, 12, 0, 0, tzinfo=utc) + timedelta(days=1),
                dtend=datetime(now.year, now.month, now.day, 13, 0, 0, tzinfo=utc) + timedelta(days=1),
            )
            objects = cal.search(
                start=now,
                end=now + timedelta(days=2),
                post_filter=False,
                compatibility_workarounds=False,
            )
            if any(tr_uid in o.data for o in objects):
                self.set_feature("search.time-range.comp-type-optional")
            else:
                self.set_feature(
                    "search.time-range.comp-type-optional",
                    {
                        "support": "unsupported",
                        "description": "comp-type-less time-range query returns nothing (server silently ignores it or requires a component type)",
                    },
                )
        except (ReportError, AuthorizationError, DAVError):
            self.set_feature(
                "search.time-range.comp-type-optional",
                {
                    "support": "unsupported",
                    "description": "server rejects a time-range filter in a query without a component type (RFC4791 section 9.7 compliant)",
                },
            )
        finally:
            if tr_event:
                try:
                    tr_event.delete()
                except Exception:
                    pass

        ## search.text.comp-type-optional: does a prop-filter (here CATEGORIES) work
        ## without a component type?  Placed under VCALENDAR the prop-filter targets
        ## VCALENDAR's own properties (which lack CATEGORIES), so servers typically
        ## match nothing.  Only meaningful when category search itself works; otherwise
        ## the result would be ambiguous.  compatibility_workarounds=False sends the raw
        ## comp-type-less query.  See https://github.com/python-caldav/caldav/issues/681
        if self.feature_checked("search.text.category"):
            pf_uid = f"csc_pf_comptype_optional_{uuid.uuid4().hex[:8]}"
            pf_cat = f"csccat{uuid.uuid4().hex[:8]}"
            pf_event = None
            try:
                pf_event = cal.save_object(
                    Event,
                    summary="prop-filter comp-type-optional check event",
                    uid=pf_uid,
                    categories=pf_cat,
                    dtstart=datetime(now.year, now.month, now.day, 12, 0, 0, tzinfo=utc) + timedelta(days=1),
                    dtend=datetime(now.year, now.month, now.day, 13, 0, 0, tzinfo=utc) + timedelta(days=1),
                )
                objects = cal.search(
                    category=pf_cat,
                    post_filter=False,
                    compatibility_workarounds=False,
                )
                if any(pf_uid in o.data for o in objects):
                    self.set_feature("search.text.comp-type-optional")
                else:
                    self.set_feature(
                        "search.text.comp-type-optional",
                        {
                            "support": "unsupported",
                            "description": "comp-type-less prop-filter query returns nothing (prop-filter under VCALENDAR matches no component property)",
                        },
                    )
            except (ReportError, AuthorizationError, DAVError):
                self.set_feature(
                    "search.text.comp-type-optional",
                    {
                        "support": "unsupported",
                        "description": "server rejects a prop-filter in a query without a component type",
                    },
                )
            finally:
                if pf_event:
                    try:
                        pf_event.delete()
                    except Exception:
                        pass
        else:
            self.set_feature(
                "search.text.comp-type-optional",
                {
                    "support": "unknown",
                    "description": "cannot test - category/text search not supported by this server",
                },
            )

        ## search.unlimited-time-range: does a REPORT without a time range return all
        ## objects regardless of date?  We look for two probes PrepareCalendar placed:
        ##   * csc_olddate_event - a non-recurring event far in the past (year 2000)
        ##   * csc_simple_event1 - a non-recurring fixture in the near future (next year)
        ## A truly unlimited server returns both.  A sliding-window server (e.g. OX)
        ## returns the near-future fixture but hides the far-past probe -> broken.  A
        ## server with a window so tight it even hides next-year objects is recorded
        ## with that more severe behaviour (and a loud warning), since that makes the
        ## near-future fixtures - and therefore most fixture-dependent checks - unreliable.
        ## Uses _request_report_build_resultlist directly to bypass the
        ## search.unlimited-time-range workaround in search.py, so the actual server
        ## behaviour is observed.
        try:
            searcher = CalDAVSearcher(comp_class=Event)
            xml, comp_class = searcher.build_search_xml_query()
            _, objects = cal._request_report_build_resultlist(xml, comp_class)
            ## Match on the iCalendar UID, not o.id: on URL-canonicalizing servers
            ## (e.g. OX) o.id is the server's internal URL id, not the UID.
            uids = set()
            for o in objects:
                try:
                    uids.add(str(o.icalendar_component.get("UID", "")))
                except Exception:
                    pass
            found_old = OLDDATE_EVENT_UID in uids
            found_next = "csc_simple_event1" in uids
            if found_old and found_next:
                self.set_feature("search.unlimited-time-range")
            elif found_next:
                ## Returned the near-future fixture but not the far-past probe:
                ## classic sliding time window (broken, not unsupported).
                self.set_feature(
                    "search.unlimited-time-range",
                    {
                        "support": "broken",
                        "behaviour": "far-past objects (year 2000) are outside the search window",
                    },
                )
            elif objects:
                ## Did not even return the next-year fixture: the window is tighter
                ## than ~14 months ahead, so the near-future fixtures are unreliable.
                logging.error(
                    "search.unlimited-time-range: even next-year fixtures are outside this "
                    "server's search window - fixture-dependent check results are unreliable."
                )
                self.set_feature(
                    "search.unlimited-time-range",
                    {
                        "support": "broken",
                        "behaviour": "even next-year objects are outside the search window; "
                        "fixture-dependent checks are unreliable on this server",
                    },
                )
            else:
                ## Server returned nothing at all for a no-time-range search
                self.set_feature("search.unlimited-time-range", "unsupported")
        except (AuthorizationError, DAVError):
            self.set_feature("search.unlimited-time-range", "ungraceful")


class CheckIsNotDefined(Check):
    """
    Checks if the server supports is-not-defined searches (RFC4791 section 9.7.4).

    Tests whether searching for objects where a property is not defined works.
    Some servers support this for some properties but not others (e.g. DAViCal
    supports it for CLASS but not for CATEGORIES).
    """

    depends_on = {CheckSearch}
    features_to_be_checked = {
        "search.is-not-defined",
        "search.is-not-defined.category",
        "search.is-not-defined.class",
        "search.is-not-defined.dtend",
    }

    def _run_check(self):
        cal = self.checker.calendar

        ## Test no_category: csc_event_with_categories has CATEGORIES set,
        ## other events don't.  no_category=True should exclude it.
        category_works = None
        try:
            events_no_cat = cal.search(event=True, no_category=True, post_filter=False)
            uids = set()
            for e in events_no_cat:
                try:
                    uids.add(str(e.component.get("uid", "")))
                except Exception:
                    pass
            has_cat_event = "csc_event_with_categories" in uids
            if not has_cat_event and len(events_no_cat) >= 1:
                category_works = True
            else:
                category_works = False
        except (ReportError, AuthorizationError, DAVError):
            category_works = "ungraceful"

        ## Set the category-specific sub-feature
        if category_works == "ungraceful":
            self.set_feature("search.is-not-defined.category", "ungraceful")
        elif category_works is True:
            self.set_feature("search.is-not-defined.category")
        else:
            self.set_feature("search.is-not-defined.category", False)

        ## Test no_class: csc_event_with_class has CLASS:CONFIDENTIAL set,
        ## other events don't.  no_class=True should exclude it.
        class_works = None
        try:
            events_no_class = cal.search(event=True, no_class=True, post_filter=False)
            class_uids = set()
            for e in events_no_class:
                try:
                    class_uids.add(str(e.component.get("uid", "")))
                except Exception:
                    pass
            has_class_event = "csc_event_with_class" in class_uids
            if not has_class_event and len(events_no_class) >= 1:
                class_works = True
            else:
                class_works = False
        except (ReportError, AuthorizationError, DAVError):
            class_works = "ungraceful"

        ## Set the class-specific sub-feature
        if class_works == "ungraceful":
            self.set_feature("search.is-not-defined.class", "ungraceful")
        elif class_works is True:
            self.set_feature("search.is-not-defined.class")
        else:
            self.set_feature("search.is-not-defined.class", False)

        ## Test no_dtend: csc_event_with_duration uses DURATION (no DTEND),
        ## while csc_simple_event1 has explicit DTEND.
        ## no_dtend=True should include duration events and exclude dtend events.
        dtend_works = None
        try:
            events_no_dtend = cal.search(event=True, no_dtend=True, post_filter=False)
            dtend_uids = set()
            for e in events_no_dtend:
                try:
                    dtend_uids.add(str(e.component.get("uid", "")))
                except Exception:
                    pass
            has_dtend_event = "csc_simple_event1" in dtend_uids
            has_no_dtend_event = "csc_event_with_duration" in dtend_uids
            if not has_dtend_event and has_no_dtend_event:
                dtend_works = True
            else:
                dtend_works = False
        except (ReportError, AuthorizationError, DAVError):
            dtend_works = "ungraceful"

        ## Set the dtend-specific sub-feature
        if dtend_works == "ungraceful":
            self.set_feature("search.is-not-defined.dtend", "ungraceful")
        elif dtend_works is True:
            self.set_feature("search.is-not-defined.dtend")
        else:
            self.set_feature("search.is-not-defined.dtend", False)

        ## Determine overall is-not-defined support from sub-features
        results = {
            "category": category_works,
            "class": class_works,
            "dtend": dtend_works,
        }
        if all(v == "ungraceful" for v in results.values()):
            self.set_feature("search.is-not-defined", "ungraceful")
        elif all(v is True for v in results.values()):
            self.set_feature("search.is-not-defined")
        elif any(v is True for v in results.values()):
            working = [k for k, v in results.items() if v is True]
            self.set_feature(
                "search.is-not-defined",
                {"support": "fragile", "details": f"works for {', '.join(working)} but not all properties"},
            )
        else:
            self.set_feature("search.is-not-defined", False)


class CheckAlarmSearch(Check):
    depends_on = {CheckSearch}
    features_to_be_checked = {"search.time-range.alarm"}

    def _run_check(self):
        cal = self.checker.calendar
        base = self.checker.fixture_base_year

        ## Check that the alarm event was created successfully
        try:
            obj = cal.object_by_uid("csc_event_with_alarm")
        except Exception:
            self.set_feature("search.time-range.alarm", False)
            return

        ## The alarm event has dtstart <base>-01-10 08:00 and a -15min alarm,
        ## so the alarm triggers at 07:45.

        ## Search that SHOULD find the alarm (07:40-07:55 covers 07:45)
        try:
            events = cal.search(
                event=True,
                alarm_start=datetime(base, 1, 10, 7, 40, tzinfo=utc),
                alarm_end=datetime(base, 1, 10, 7, 55, tzinfo=utc),
                post_filter=False,
            )
        except (AuthorizationError, DAVError):
            self.set_feature("search.time-range.alarm", "ungraceful")
            return

        if len(events) != 1:
            self.set_feature("search.time-range.alarm", False)
            return

        ## Search that should NOT find the alarm (08:00-08:15 is after trigger)
        try:
            events = cal.search(
                event=True,
                alarm_start=datetime(base, 1, 10, 8, 0, tzinfo=utc),
                alarm_end=datetime(base, 1, 10, 8, 15, tzinfo=utc),
                post_filter=False,
            )
        except (AuthorizationError, DAVError):
            self.set_feature("search.time-range.alarm", "ungraceful")
            return

        self.set_feature("search.time-range.alarm", len(events) == 0)


class CheckRecurrenceSearch(Check):
    depends_on = {CheckSearch}
    features_to_be_checked = {
        "search.recurrences.includes-implicit.todo",
        "search.recurrences.includes-implicit.todo.pending",
        "search.recurrences.includes-implicit.event",
        "search.recurrences.includes-implicit.infinite-scope",
        "search.recurrences.expanded.todo",
        "search.recurrences.expanded.event",
        "search.recurrences.expanded.exception",
    }

    def _run_check(self):
        cal = self.checker.calendar
        tl = self.checker.tasklist
        base = self.checker.fixture_base_year

        ## Precondition: basic event time-range search must return exactly the
        ## one recurring event in Jan <base>.  The two ways it can fail are not
        ## the same thing, and are not graded the same way.
        try:
            events = cal.search(
                start=datetime(base, 1, 12, tzinfo=utc),
                end=datetime(base, 1, 13, tzinfo=utc),
                event=True,
                post_filter=False,
            )
        except (AuthorizationError, DAVError):
            ## The server rejected the range outright (e.g. CCS, which enforces a
            ## min/max-date-time).  It raised rather than answering wrongly, so
            ## this is "ungraceful" - the same reading the far-future probe below
            ## gives the very same 403.
            for feat in self.features_to_be_checked:
                self.set_feature(feat, "ungraceful")
            return
        if len(events) != 1:
            ## The search answered, but with the wrong objects - broken comp-type
            ## filtering (e.g. Bedework) returns extra ones.  That is a silent
            ## wrong answer, which is what "unsupported" means here.
            for feat in self.features_to_be_checked:
                self.set_feature(feat, False)
            return

        ## Whether a VTODO time-range search reliably returns just the one recurring
        ## todo.  Some servers (e.g. OX) ignore the time-range for VTODOs and return
        ## every task, so the todo recurrence sub-features can't be measured there.
        ## We mark those todo sub-features unsupported but STILL run the
        ## event-recurrence checks below (event time-range may work fine).
        todo_testable = False
        if self.checker.features_checked.is_supported("search.time-range.todo"):
            try:
                todos = tl.search(
                    start=datetime(base, 1, 12, tzinfo=utc),
                    end=datetime(base, 1, 13, tzinfo=utc),
                    todo=True,
                    include_completed=True,
                    post_filter=False,
                )
                todo_testable = len(todos) == 1
            except (AuthorizationError, DAVError):
                todo_testable = False
        if not todo_testable:
            logging.warning(
                "Recurring-todo precondition not met (VTODO time-range search unreliable in Jan %d); "
                "todo recurrence sub-features reported unknown",
                base,
            )

        events = cal.search(
            start=datetime(base, 2, 12, tzinfo=utc),
            end=datetime(base, 2, 13, tzinfo=utc),
            event=True,
            post_filter=False,
        )
        implicit_datetime = len(events) == 1
        ## Also check all-day (VALUE=DATE) recurring events: the monthly all-day
        ## event (DTSTART;VALUE=DATE:<base>-02-01) should have its second occurrence
        ## found in the <base>-03-01 range.  A monthly rrule keeps that occurrence
        ## near (and inside sliding-window servers' search windows).
        try:
            allday_events = cal.search(
                start=datetime(base, 3, 1, tzinfo=utc),
                end=datetime(base, 3, 2, tzinfo=utc),
                event=True,
                post_filter=False,
            )
            implicit_allday = len(allday_events) == 1
            allday_raised = False
        except (AuthorizationError, DAVError):
            ## Don't fold a raise into the datetime verdict: the server refusing
            ## the all-day query is not evidence that all-day recurrence works.
            implicit_allday = False
            allday_raised = True
        if allday_raised:
            self.set_feature(
                "search.recurrences.includes-implicit.event",
                {
                    "support": "ungraceful",
                    "behaviour": "the all-day (VALUE=DATE) recurrence query was rejected",
                },
            )
        elif implicit_datetime and not implicit_allday:
            ## Datetime recurring events work but all-day (VALUE=DATE) events do not.
            self.set_feature(
                "search.recurrences.includes-implicit.event",
                {"support": "fragile", "behaviour": "broken for all-day (VALUE=DATE) events"},
            )
        else:
            self.set_feature("search.recurrences.includes-implicit.event", implicit_datetime)

        if todo_testable:
            todos1 = tl.search(
                start=datetime(base, 2, 12, tzinfo=utc),
                end=datetime(base, 2, 13, tzinfo=utc),
                todo=True,
                include_completed=True,
                post_filter=False,
            )
            self.set_feature("search.recurrences.includes-implicit.todo", len(todos1) == 1)
            if todos1:
                todos2 = tl.search(
                    start=datetime(base, 2, 12, tzinfo=utc),
                    end=datetime(base, 2, 13, tzinfo=utc),
                    todo=True,
                    post_filter=False,
                )
                self.set_feature("search.recurrences.includes-implicit.todo.pending", len(todos2) == 1)
            else:
                self.set_feature("search.recurrences.includes-implicit.todo.pending", False)
        else:
            ## The VTODO time-range precondition was never established - either the
            ## server has no todo time-range search, or the probe query came back
            ## with the wrong objects.  Either way nothing was observed about todo
            ## *recurrence*, and not observed is not observed-not-to-work.
            self.set_feature("search.recurrences.includes-implicit.todo", "unknown")
            self.set_feature("search.recurrences.includes-implicit.todo.pending", "unknown")

        exception = cal.search(
            start=datetime(base, 2, 13, 11, tzinfo=utc),
            end=datetime(base, 2, 13, 13, tzinfo=utc),
            event=True,
            post_filter=False,
        )
        if len(exception) != 1:
            ## Can't reliably check expansion/exception features.  The
            ## includes-implicit.{event,todo,todo.pending} features were already
            ## set above; set the remaining (still-unmeasured) ones unsupported.
            ## (A blanket "if not feature_checked" loop would wrongly skip features
            ## whose spec default is "full", since is_supported() returns the
            ## default for an as-yet-unset feature.)
            for feat in (
                "search.recurrences.includes-implicit.infinite-scope",
                "search.recurrences.expanded.event",
                "search.recurrences.expanded.todo",
                "search.recurrences.expanded.exception",
            ):
                self.set_feature(feat, False)
            return
        ## Far-future occurrence of the monthly recurring event, to test whether
        ## implicit recurrence has an unlimited scope.  The probe is ~45 years
        ## ahead of the fixture (base+45, on the 12th to hit a monthly occurrence)
        ## - kept relative to the base year so the gap stays large no matter when
        ## the suite runs; a fixed year would shrink to ~18 years once the fixtures
        ## moved to the near future, letting bounded-expansion servers (e.g. Zimbra)
        ## look falsely "infinite".  Servers that enforce a max-date-time (e.g. CCS)
        ## reject the query outright with 403 - that is an error, not a silent
        ## non-answer, so it is "ungraceful" rather than "unsupported".
        try:
            far_future_recurrence = cal.search(
                start=datetime(base + 45, 3, 12, tzinfo=utc),
                end=datetime(base + 45, 3, 13, tzinfo=utc),
                event=True,
                post_filter=False,
            )
            self.set_feature("search.recurrences.includes-implicit.infinite-scope", len(far_future_recurrence) == 1)
        except (AuthorizationError, DAVError):
            self.set_feature("search.recurrences.includes-implicit.infinite-scope", "ungraceful")

        ## server-side expansion
        events = cal.search(
            start=datetime(base, 2, 12, tzinfo=utc),
            end=datetime(base, 2, 13, tzinfo=utc),
            event=True,
            server_expand=True,
            post_filter=False,
        )
        self.set_feature(
            "search.recurrences.expanded.event",
            len(events) == 1 and events[0].component["dtstart"] == datetime(base, 2, 12, 12, 0, 0, tzinfo=utc),
        )
        if todo_testable:
            ## The task calendar, not the event calendar: on servers that keep
            ## tasks in a separate collection this search finds nothing in cal
            ## and the feature is reported unsupported for no reason.
            todos = tl.search(
                start=datetime(base, 2, 12, tzinfo=utc),
                end=datetime(base, 2, 13, tzinfo=utc),
                todo=True,
                server_expand=True,
                post_filter=False,
            )
            self.set_feature(
                "search.recurrences.expanded.todo",
                len(todos) == 1 and todos[0].component["dtstart"] == datetime(base, 2, 12, 12, 0, 0, tzinfo=utc),
            )
        else:
            ## Unmeasured for the same reason as the two above.
            self.set_feature("search.recurrences.expanded.todo", "unknown")
        exception = cal.search(
            start=datetime(base, 2, 13, 11, tzinfo=utc),
            end=datetime(base, 2, 13, 13, tzinfo=utc),
            event=True,
            server_expand=True,
            post_filter=False,
        )
        self.set_feature(
            "search.recurrences.expanded.exception",
            len(exception) == 1
            and exception[0].component["dtstart"] == datetime(base, 2, 13, 12, 0, 0, tzinfo=utc)
            and exception[0].component["summary"] == "February recurrence with different summary"
            and getattr(exception[0].component.get("RECURRENCE-ID"), "dt", None)
            == datetime(base, 2, 13, 12, tzinfo=utc),
        )

        ## The exception above carries no SEQUENCE.  A second fixture
        ## (csc_monthly_recurring_with_exception_seq, on the 14th) is identical but
        ## carries SEQUENCE on both components, as real-world clients always do.
        ## Some servers (Stalwart) suppress the exception-overridden occurrence
        ## during server-side expand only when SEQUENCE is absent; with SEQUENCE
        ## present they return both the original occurrence and the override.  If
        ## the SEQUENCE-less variant worked but the SEQUENCE one does not, the
        ## feature is fragile rather than fully supported.
        if self.checker.features_checked.is_supported("search.recurrences.expanded.exception"):
            seq_exception = cal.search(
                start=datetime(base, 2, 14, 11, tzinfo=utc),
                end=datetime(base, 2, 14, 13, tzinfo=utc),
                event=True,
                server_expand=True,
                post_filter=False,
            )
            seq_ok = (
                len(seq_exception) == 1
                and seq_exception[0].component["dtstart"] == datetime(base, 2, 14, 12, 0, 0, tzinfo=utc)
                and seq_exception[0].component["summary"] == "February recurrence with different summary (seq)"
                and getattr(seq_exception[0].component.get("RECURRENCE-ID"), "dt", None)
                == datetime(base, 2, 14, 12, tzinfo=utc)
            )
            if not seq_ok:
                self.set_feature(
                    "search.recurrences.expanded.exception",
                    {
                        "support": "fragile",
                        "behaviour": "server-side expand fails to suppress the exception-overridden occurrence when SEQUENCE is present",
                    },
                )

        ## RFC 4791 §9.6.5: non-initial expanded instances MUST include RECURRENCE-ID;
        ## the initial instance MAY omit it.  Query a range covering both the regular
        ## Feb 12 occurrence and the Feb 13 exception to detect servers that omit
        ## RECURRENCE-ID on the initial instance, and annotate the expanded.event feature.
        multi = cal.search(
            start=datetime(base, 2, 12, tzinfo=utc),
            end=datetime(base, 2, 14, tzinfo=utc),
            event=True,
            server_expand=True,
            post_filter=False,
        )
        if len(multi) == 2:
            initial = next(
                (e for e in multi if e.component["dtstart"] == datetime(base, 2, 12, 12, 0, 0, tzinfo=utc)),
                None,
            )
            if initial is not None and getattr(initial.component.get("RECURRENCE-ID"), "dt", None) is None:
                self.set_feature(
                    "search.recurrences.expanded.event",
                    {
                        "behaviour": "initial occurrence lacks RECURRENCE-ID; RFC 4791 §9.6.5 permits this — clients must fall back to DTSTART"
                    },
                )


class CheckCaseSensitiveSearch(Check):
    """
    Checks if the server supports case-sensitive and case-insensitive text searches.

    RFC4791 section 9.7.5 specifies that i;ascii-casemap MUST be the default collation,
    and section 7.5 says servers are REQUIRED to support i;octet (case-sensitive).
    """

    depends_on = {CheckSearch}
    features_to_be_checked = {
        "search.text.case-sensitive",
        "search.text.case-insensitive",
    }

    def _run_check(self):
        cal = self.checker.calendar

        ## The PrepareCalendar check created an event with summary
        ## "simple event with a start time and an end time" (uid csc_simple_event1).
        ## We search for "Simple" (uppercase S) vs "simple" (lowercase s)
        ## to test case sensitivity.

        text_search_filters = True

        ## Case-sensitive search (i;octet collation):
        ## Searching for "Simple" (uppercase S) should NOT match
        ## "simple event ..." (lowercase s).
        ## Using post_filter=False to test server-side behaviour.
        try:
            searcher = CalDAVSearcher(event=True)
            searcher.add_property_filter("SUMMARY", "Simple", case_sensitive=True)
            results_sensitive = searcher.search(cal, post_filter=False)

            searcher2 = CalDAVSearcher(event=True)
            searcher2.add_property_filter("SUMMARY", "simple", case_sensitive=True)
            results_sensitive_match = searcher2.search(cal, post_filter=False)

            ## "Simple" should not match "simple event ...", but "simple" should
            self.set_feature(
                "search.text.case-sensitive", len(results_sensitive) == 0 and len(results_sensitive_match) >= 1
            )

            ## If more than one result is returned for "Simple" (only one event
            ## has "simple" in the summary), the server is not properly filtering
            ## text searches (e.g., robur ignores text filters and returns all
            ## events).  In that case, case-insensitive tests are meaningless.
            if len(results_sensitive) > 1:
                text_search_filters = False
        except (ReportError, DAVError) as e:
            self.set_feature(
                "search.text.case-sensitive",
                {"support": "ungraceful", "behaviour": f"{type(e).__name__}: {e}"},
            )
            text_search_filters = False

        ## Case-insensitive search (i;ascii-casemap collation):
        ## Searching for "SIMPLE" should match "simple event ..."
        ## when case_sensitive=False.
        ## Skip if text search doesn't filter at all (no point testing
        ## case sensitivity when the server ignores text filters).
        if not text_search_filters:
            self.set_feature("search.text.case-insensitive", False)
            return

        try:
            searcher3 = CalDAVSearcher(event=True)
            searcher3.add_property_filter("SUMMARY", "SIMPLE", case_sensitive=False)
            results_insensitive = searcher3.search(cal, post_filter=False)

            self.set_feature("search.text.case-insensitive", len(results_insensitive) >= 1)
        except (ReportError, DAVError) as e:
            self.set_feature(
                "search.text.case-insensitive",
                {"support": "ungraceful", "behaviour": f"{type(e).__name__}: {e}"},
            )


class CheckSubstringSearch(Check):
    """
    Checks if the server supports substring text search (text-match with
    match-type="contains").

    Some servers (e.g. Zimbra) accept the REPORT but only do exact match,
    ignoring the contains match-type.
    """

    depends_on = {CheckSearch}
    features_to_be_checked = {
        "search.text.substring",
    }

    def _run_check(self):
        cal = self.checker.calendar

        try:
            ## First, verify text search works at all by searching for
            ## the full summary (should match regardless of substring support)
            searcher_exact = CalDAVSearcher(event=True)
            searcher_exact.add_property_filter("SUMMARY", "simple event with a start time and an end time")
            results_exact = searcher_exact.search(cal, post_filter=False)

            if len(results_exact) != 1:
                ## Text search doesn't filter properly, can't determine
                ## substring support
                self.set_feature("search.text.substring", None)
                return

            ## Now search for a substring of the same summary
            searcher_sub = CalDAVSearcher(event=True)
            searcher_sub.add_property_filter("SUMMARY", "simple event")
            results_sub = searcher_sub.search(cal, post_filter=False)

            self.set_feature("search.text.substring", len(results_sub) == 1)
        except (ReportError, DAVError) as e:
            self.set_feature(
                "search.text.substring",
                {"support": "ungraceful", "behaviour": f"{type(e).__name__}: {e}"},
            )


class CheckPrincipalSearch(Check):
    """
    Checks if the server supports principal search operations.

    Uses DAVClient.search_principals() which sends a
    DAV:principal-property-search REPORT.
    """

    depends_on = {CheckGetCurrentUserPrincipal}
    features_to_be_checked = {
        "principal-search",
        "principal-search.by-name.self",
        "principal-search.list-all",
    }

    def _run_check(self):
        principal = self.checker.principal
        if not principal:
            self.set_feature("principal-search", False)
            return

        ## Get the display name of the current principal for self-search.
        ## Fall back to the username if no display name is available.
        try:
            search_name = principal.get_display_name()
        except Exception:
            search_name = None
        if not search_name:
            search_name = getattr(self.client, "username", None)

        any_search_worked = False
        any_ungraceful = False

        ## Use search_principals (v3.0+) or principals (v2.x) method
        _search_principals = getattr(self.client, "search_principals", None) or getattr(self.client, "principals", None)
        if not _search_principals:
            self.set_feature("principal-search", False)
            self.set_feature("principal-search.by-name.self", False)
            self.set_feature("principal-search.list-all", False)
            return

        ## principal-search.by-name.self: search for own principal by name
        if search_name:
            try:
                results = _search_principals(name=search_name)
                found_self = any(isinstance(r, Principal) for r in results)
                self.set_feature("principal-search.by-name.self", found_self)
                if found_self:
                    any_search_worked = True
            except (ReportError, DAVError):
                self.set_feature("principal-search.by-name.self", "ungraceful")
                any_ungraceful = True
        else:
            self.set_feature("principal-search.by-name.self", None)

        ## principal-search.list-all: list all principals without filter
        try:
            results = _search_principals()
            found_any = len(results) >= 1
            self.set_feature("principal-search.list-all", found_any)
            if found_any:
                any_search_worked = True
        except (ReportError, DAVError):
            self.set_feature("principal-search.list-all", "ungraceful")
            any_ungraceful = True

        ## principal-search: overall support derived from sub-features
        if any_search_worked:
            self.set_feature("principal-search")
        elif any_ungraceful:
            self.set_feature("principal-search", "ungraceful")
        else:
            self.set_feature("principal-search", False)


class CheckDuplicateEvent(Check):
    """
    Checks whether two events with identical content but different UIDs can
    coexist in the same calendar.  A few servers (Bedework) reject or
    de-duplicate such an event ("duplicates not allowed even with a different
    UID").  Replaces the old 'duplicates_not_allowed' flag.
    """

    depends_on = {PrepareCalendar}
    features_to_be_checked = {"save.duplicate-event"}

    def _run_check(self) -> None:
        feature = "save.duplicate-event"
        cal = self.checker.calendar
        src_uid = "csc_simple_event1"
        dup_uid = "csc_duplicate_event_probe"

        ## Reuse the PrepareCalendar fixture; fall back to a direct-URL load.
        try:
            event1 = cal.event_by_uid(src_uid)
        except (NotFoundError, AuthorizationError, DAVError):
            event1 = url_object(cal, src_uid)
        try:
            event1.load()
        except (NotFoundError, AuthorizationError, DAVError):
            return  ## cannot probe without the source fixture

        ## Same content, different UID.
        dup_data = event1.data.replace(src_uid, dup_uid)
        if dup_uid not in dup_data:
            return

        saved = None
        try:
            try:
                saved = cal.save_event(dup_data)
            except (DAVError, AuthorizationError):
                ## The server rejected the duplicate with an error.
                self.set_feature(feature, {"support": "ungraceful"})
                return
            ## Did the duplicate persist as a separate object?
            try:
                saved.load()
                persisted = dup_uid in saved.data
            except (NotFoundError, AuthorizationError, DAVError):
                persisted = False
            self.set_feature(feature, persisted)
        finally:
            if saved is not None:
                try:
                    saved.delete()
                except Exception:
                    pass


class CheckDuplicateUID(Check):
    """
    Checks how server handles events with duplicate UIDs across calendars.

    Some servers allow the same UID in different calendars (treating them
    as separate entities), while others may throw errors or silently ignore
    duplicates.

    Tests:
    - save.duplicate-uid.cross-calendar: Can events with same UID exist in different calendars?
    """

    depends_on = {PrepareCalendar}
    features_to_be_checked = {"save.duplicate-uid.cross-calendar"}

    def _run_check(self) -> None:
        cal1 = self.checker.calendar
        base = self.checker.fixture_base_year

        ## Reuse an event from PrepareCalendar instead of creating a new one
        test_uid = "csc_simple_event1"
        cal2_name = "csc_duplicate_uid_cal2"

        ## Try to find and delete existing cal2 test calendar
        try:
            for cal in self.client.principal().calendars():
                if cal.name == cal2_name:
                    cal.delete()
                    break
        except Exception:
            pass

        try:
            ## Get existing event from first calendar (created by PrepareCalendar).
            ## Fall back to direct URL lookup if the server's REPORT/search can't
            ## find the event (indexing delay, canonicalized URL) or refuses the
            ## query outright (OX answers a UID calendar-query with 403 Forbidden).
            try:
                event1 = cal1.event_by_uid(test_uid)
            except (NotFoundError, AuthorizationError, DAVError):
                event1 = url_object(cal1, test_uid)
            event1.load()  ## csc_simple_event1 now lives in the near future (next year)

            ## Get the event data for reuse in cal2
            event_ical = event1.data

            ## Create second calendar
            try:
                cal2 = self.client.principal().make_calendar(name=cal2_name)
            except DAVError:
                self.set_feature(
                    "save.duplicate-uid.cross-calendar",
                    {"support": "unknown", "behaviour": "cannot test, have access to only one calendar"},
                )
                return

            try:
                ## Try to save event with same UID to second calendar
                event2 = cal2.save_object(Event, event_ical)

                ## Check if the event actually exists in cal2
                events_in_cal2 = list(_filter_fixture_window(cal2.events(), base))

                ## Check if event still exists in cal1 (Zimbra moves it instead of copying)
                try:
                    cal1.event_by_uid(test_uid)
                    event_was_moved = False
                except NotFoundError:
                    event_was_moved = True

                if len(events_in_cal2) == 0:
                    ## Server silently ignored the duplicate
                    self.set_feature(
                        "save.duplicate-uid.cross-calendar", {"support": "unsupported", "behaviour": "silently-ignored"}
                    )
                elif len(events_in_cal2) == 1 and event_was_moved:
                    ## Server moved the event instead of creating a duplicate (Zimbra behavior)
                    self.set_feature(
                        "save.duplicate-uid.cross-calendar",
                        {"support": "unsupported", "behaviour": "moved-instead-of-copied"},
                    )
                    ## Move event back to cal1 to avoid breaking other tests
                    cal1.save_event(event2.data)
                elif len(events_in_cal2) == 1:
                    if events_in_cal2[0].component["uid"] != test_uid:
                        logging.error("Unexpected UID in duplicate-uid cross-calendar test; skipping")
                        return
                    ## Server accepted the duplicate
                    ## Verify they are treated as separate entities.
                    event1 = cal1.event_by_uid(test_uid)
                    event1.load()

                    ## Store original summary to check later
                    original_summary = str(event1.icalendar_instance.walk("vevent")[0].get("summary", ""))

                    ## Modify event in cal2 and verify cal1's event is unchanged
                    event2.icalendar_instance.walk("vevent")[0]["summary"] = "Modified in Cal2"
                    event2.save()

                    event1.load()
                    current_summary = str(event1.icalendar_instance.walk("vevent")[0].get("summary", ""))
                    if current_summary == original_summary:
                        self.set_feature("save.duplicate-uid.cross-calendar", True)
                    else:
                        self.set_feature(
                            "save.duplicate-uid.cross-calendar",
                            {
                                "support": "fragile",
                                "behaviour": "Modifying duplicate in one calendar affects the other",
                            },
                        )
                else:
                    self.set_feature(
                        "save.duplicate-uid.cross-calendar",
                        {"support": "fragile", "behaviour": f"Unexpected: {len(events_in_cal2)} events in cal2"},
                    )

            except (DAVError, AuthorizationError) as e:
                ## Server rejected the duplicate with an error
                self.set_feature(
                    "save.duplicate-uid.cross-calendar",
                    {"support": "ungraceful", "behaviour": f"Server error: {type(e).__name__}"},
                )
            finally:
                ## Cleanup
                try:
                    cal2.delete()
                except Exception:
                    pass

        finally:
            ## No need to cleanup test event - it's owned by PrepareCalendar
            pass


class CheckSyncToken(Check):
    """
    Checks support for RFC6578 sync-collection reports (sync tokens)

    Tests for four known issues:
    1. No sync token support at all
    2. Time-based sync tokens (second-precision, requires sleep between ops)
    3. Fragile sync tokens (returns extra content, race conditions)
    4. Sync breaks on delete (server fails after object deletion)
    """

    depends_on = {PrepareCalendar}
    features_to_be_checked = {
        "sync-token",
        "sync-token.delete",
    }

    def _run_check(self) -> None:
        cal = self.checker.calendar
        base = self.checker.fixture_base_year

        ## Test 1: Check if sync tokens are supported at all
        ## Use disable_fallback=True to detect true server support
        try:
            my_objects = cal.objects(disable_fallback=True)
            sync_token = my_objects.sync_token

            if not sync_token or sync_token == "":
                self.set_feature("sync-token", False)
                return

            ## Initially assume full support
            sync_support = "full"
            sync_behaviour = None
        except (ReportError, DAVError, AttributeError) as e:
            self.set_feature(
                "sync-token",
                {"support": "ungraceful", "behaviour": f"Server error on sync-collection REPORT: {type(e).__name__}"},
            )
            return

        ## Test 2 & 3: Check for time-based and fragile sync tokens
        ## Use unique UID for sync test since this test deletes the event
        ## (Nextcloud trashbin bug - see https://github.com/nextcloud/server/issues/30096)
        test_uid = f"csc_sync_test_event_{int(time.time() * 1000)}"

        ## Create a new event
        test_event = None
        try:
            test_event = cal.save_object(
                Event,
                summary="Sync token test event",
                uid=test_uid,
                dtstart=datetime(base, 4, 1, 12, 0, 0, tzinfo=utc),
                dtend=datetime(base, 4, 1, 13, 0, 0, tzinfo=utc),
            )

            ## Get objects with new sync token
            my_objects = cal.objects(disable_fallback=True)
            sync_token1 = my_objects.sync_token

            ## Immediately check for changes (should be none)
            my_changed_objects = cal.objects_by_sync_token(sync_token=sync_token1, disable_fallback=True)
            immediate_count = len(list(my_changed_objects))

            if immediate_count > 0:
                ## Fragile sync tokens return extra content
                sync_support = "fragile"

            ## Test for time-based sync tokens
            ## Modify the event within the same second
            test_event.icalendar_instance.subcomponents[0]["SUMMARY"] = "Modified immediately"
            test_event.save()

            ## Check for changes immediately (time-based tokens need sleep(1))
            my_changed_objects = cal.objects_by_sync_token(sync_token=sync_token1, disable_fallback=True)
            changed_count_no_sleep = len(list(my_changed_objects))

            if changed_count_no_sleep == 0:
                ## Might be time-based, wait a second and try again
                time.sleep(1)
                test_event.icalendar_instance.subcomponents[0]["SUMMARY"] = "Modified after sleep"
                test_event.save()
                time.sleep(1)

                my_changed_objects = cal.objects_by_sync_token(sync_token=sync_token1, disable_fallback=True)
                changed_count_with_sleep = len(list(my_changed_objects))

                if changed_count_with_sleep >= 1:
                    sync_behaviour = "time-based"
                else:
                    ## Sync tokens might be completely broken
                    sync_support = "broken"

            ## Set the sync-token feature with support and behaviour
            if sync_behaviour:
                self.set_feature("sync-token", {"support": sync_support, "behaviour": sync_behaviour})
            else:
                self.set_feature("sync-token", {"support": sync_support})

            ## Test 4: Check if sync breaks on delete
            sync_token2 = my_changed_objects.sync_token

            ## Sleep if needed
            if sync_behaviour == "time-based":
                time.sleep(1)

            ## Delete the test event
            test_event.delete()
            test_event = None  ## Mark as deleted

            if sync_behaviour == "time-based":
                time.sleep(1)

            try:
                my_changed_objects = cal.objects_by_sync_token(sync_token=sync_token2, disable_fallback=True)
                ## RFC 6578 3.2: a removed member comes back as a <response> of its
                ## own with status 404, and the library builds an object for every
                ## <response> in the multistatus - so a server that reports the
                ## deletion yields at least one changed object here.  Not raising is
                ## not the same as reporting it: a server that answers 207 with an
                ## empty multistatus has silently dropped the deletion, and a client
                ## syncing incrementally never learns the object is gone.
                deleted_count = len(list(my_changed_objects))
                if deleted_count:
                    self.set_feature("sync-token.delete", True)
                else:
                    self.set_feature(
                        "sync-token.delete",
                        {
                            "support": "unsupported",
                            "behaviour": "the sync-collection report after a delete listed no changes",
                        },
                    )
            except (ReportError, DAVError) as e:
                ## Some servers (like sabre-based) return "418 I'm a teapot" or other
                ## errors.  The sync does not silently come back wrong, it raises - so
                ## this is "ungraceful", the same grading Test 1 above gives its own
                ## failed REPORT.
                self.set_feature(
                    "sync-token.delete", {"support": "ungraceful", "behaviour": f"sync fails after deletion: {e}"}
                )
        finally:
            ## Ensure cleanup even if an exception occurred
            if test_event is not None:
                try:
                    test_event.delete()
                except Exception:
                    pass


class CheckFreeBusyQuery(Check):
    """
    Checks support for RFC4791 free/busy-query REPORT

    Tests if the server supports free/busy queries as specified in RFC4791 section 7.10.
    The free/busy query allows clients to retrieve free/busy information for a time range.
    """

    depends_on = {PrepareCalendar}
    features_to_be_checked = {
        "freebusy-query",
    }

    def _run_check(self) -> None:
        cal = self.checker.calendar
        base = self.checker.fixture_base_year

        try:
            ## Try to perform a simple freebusy query
            ## Use the fixture month (next year, Jan) which overlaps the fixtures
            ## and stays inside sliding-window servers' search windows
            start = datetime(base, 1, 1, 0, 0, 0, tzinfo=utc)
            end = datetime(base, 1, 31, 23, 59, 59, tzinfo=utc)

            freebusy = cal.freebusy_request(start, end)

            ## If we got here without exception, the feature is supported
            ## Verify we got a valid freebusy object
            if freebusy and hasattr(freebusy, "vobject_instance"):
                self.set_feature("freebusy-query", True)
            else:
                self.set_feature(
                    "freebusy-query",
                    {"support": "unsupported", "behaviour": "freebusy query returned invalid or empty response"},
                )
        except (ReportError, DAVError, NotFoundError) as e:
            ## Server doesn't support freebusy queries
            ## Common responses: 500 Internal Server Error, 501 Not Implemented
            self.set_feature("freebusy-query", {"support": "ungraceful", "behaviour": f"freebusy query failed: {e}"})
        except Exception as e:
            ## Unexpected error
            self.set_feature(
                "freebusy-query",
                {"support": "broken", "behaviour": f"unexpected error during freebusy query: {e}"},
            )


class CheckScheduling(Check):
    """
    Checks support for CalDAV Scheduling (RFC6638).

    Calls client.supports_scheduling() to detect whether the server
    advertises scheduling support.
    """

    features_to_be_checked = {"scheduling"}

    def _run_check(self) -> None:
        self.set_feature("scheduling", self.client.supports_scheduling())


class CheckSchedulingDetails(Check):
    """
    Checks RFC6638 scheduling sub-features: mailbox (inbox/outbox) and
    calendar-user-address-set.  Depends on CheckScheduling; when scheduling
    is unsupported both sub-features are recorded as unsupported immediately.
    """

    depends_on = {CheckScheduling, CheckGetCurrentUserPrincipal}
    features_to_be_checked = {"scheduling.mailbox", "scheduling.calendar-user-address-set"}

    def _run_check(self) -> None:
        if self.feature_ungood("scheduling"):
            return

        principal = self.checker.principal
        if principal is None:
            self.set_feature("scheduling.mailbox", {"support": "unknown"})
            self.set_feature("scheduling.calendar-user-address-set", {"support": "unknown"})
            return

        ## Check inbox + outbox
        try:
            principal.schedule_inbox()
            principal.schedule_outbox()
            self.set_feature("scheduling.mailbox", True)
        except NotFoundError:
            self.set_feature("scheduling.mailbox", False)
        except Exception as e:
            self.set_feature("scheduling.mailbox", {"support": "broken", "behaviour": str(e)})

        ## Check calendar-user-address-set
        try:
            principal.calendar_user_address_set()
            self.set_feature("scheduling.calendar-user-address-set", True)
        except NotFoundError:
            self.set_feature("scheduling.calendar-user-address-set", False)
        except Exception as e:
            self.set_feature(
                "scheduling.calendar-user-address-set",
                {"support": "broken", "behaviour": str(e)},
            )


class CheckFreeBusyQueryRFC6638(Check):
    """
    Checks support for RFC6638 freebusy query via the schedule outbox (section 4.1).

    POSTs a VFREEBUSY REQUEST to the principal's schedule outbox listing an
    attendee, and checks whether the server responds without error.  When a
    second principal is available (via extra_principals), queries that
    principal's free/busy — a more realistic cross-user scenario.  Reports
    unknown when no second principal is configured, since RFC6638 is a
    multi-user protocol and a self-query would give unreliable results.

    Distinct from CheckFreeBusyQuery which uses a REPORT against a
    calendar collection.  Requires scheduling and scheduling.mailbox.
    """

    depends_on = {CheckSchedulingDetails}
    features_to_be_checked = {"scheduling.freebusy-query"}

    def _run_check(self) -> None:
        if self.feature_ungood("scheduling.mailbox"):
            return

        principal = self.checker.principal
        if principal is None:
            self.set_feature("scheduling.freebusy-query", {"support": "unknown"})
            return

        ## Determine the attendee address to query.
        ## Prefer a second principal (cross-user scenario); fall back to self-query.
        extra_principals = self.checker.extra_principals
        if extra_principals:
            attendee_principal = extra_principals[0]
            attendee_address = resolve_cu_address(attendee_principal)
            if not attendee_address:
                self.set_feature("scheduling.freebusy-query", {"support": "unknown"})
                return
        else:
            ## No second principal — cannot perform a meaningful cross-user probe.
            ## RFC6638 is a multi-user protocol; mark as unknown rather than
            ## testing against ourselves (self-queries may produce false results).
            ## (The early return above already handles the no-scheduling case.)
            self.set_feature(
                "scheduling.freebusy-query",
                {
                    "support": "unknown",
                    "behaviour": "not tested: only one user configured; server claims scheduling support",
                },
            )
            return

        ## This check does not depend on PrepareCalendar, so derive the base year
        ## directly; the near-future range avoids window restrictions on free-busy.
        base = getattr(self.checker, "fixture_base_year", None) or _base_year()
        dtstart = datetime(base, 1, 9, 0, 0, 0, tzinfo=utc)
        dtend = datetime(base, 1, 10, 0, 0, 0, tzinfo=utc)

        try:
            principal.freebusy_request(dtstart, dtend, [attendee_address])
            self.set_feature("scheduling.freebusy-query")
        except (AuthorizationError, DAVError, NotFoundError) as e:
            self.set_feature("scheduling.freebusy-query", {"support": "ungraceful", "behaviour": str(e)})
        except Exception as e:
            self.set_feature("scheduling.freebusy-query", {"support": "broken", "behaviour": str(e)})


class CheckScheduleTag(Check):
    """
    Checks support for the Schedule-Tag response header and property (RFC6638 sections 3.2-3.3).

    Creates a scheduling object resource (a VEVENT with an ORGANIZER property),
    GETs it back and checks whether the server returns a Schedule-Tag response
    header (captured into props by caldav's load()) and exposes the schedule-tag
    DAV property via PROPFIND, as mandated by RFC6638 section 3.2.

    This check is skipped when the server does not advertise CalDAV scheduling
    support, since Schedule-Tag is only required on scheduling object resources.
    """

    depends_on = {CheckSchedulingDetails, PrepareCalendar}
    features_to_be_checked = {"scheduling.schedule-tag"}

    def _run_check(self) -> None:
        if self.feature_ungood("scheduling"):
            return

        ## Resolve the authenticated user's calendar address so that the probe
        ## event has an ORGANIZER that matches the session.  Servers like Stalwart
        ## only assign a Schedule-Tag when the ORGANIZER equals the authenticated
        ## user; a fake address causes the PUT to succeed but return no tag.
        principal = self.checker.principal
        if self.feature_checked("scheduling.calendar-user-address-set"):
            try:
                own_address = str(principal.get_vcal_address())
            except Exception:
                self.set_feature("scheduling.schedule-tag", {"support": "unknown"})
                return
        else:
            username = getattr(self.client, "username", None)
            if not username or "@" not in str(username):
                self.set_feature("scheduling.schedule-tag", {"support": "unknown"})
                return
            own_address = "mailto:" + username

        cal = self.checker.calendar
        probe_uid = f"csc_schedule_tag_probe_{uuid.uuid4().hex}"
        ## Resolve a second attendee address. Prefer a real local account (so
        ## servers like Stalwart trigger full scheduling semantics) and fall back
        ## to a dummy address for single-account setups.
        extra_principals = self.checker.extra_principals
        if extra_principals:
            try:
                attendee_address = str(extra_principals[0].get_vcal_address())
            except Exception:
                attendee_username = getattr(extra_principals[0].client, "username", None)
                attendee_address = (
                    "mailto:" + attendee_username
                    if attendee_username and "@" in str(attendee_username)
                    else "mailto:csc-probe-attendee@example.com"
                )
        else:
            attendee_address = "mailto:csc-probe-attendee@example.com"

        ## Minimal scheduling object resource: a VEVENT with an ORGANIZER property.
        ## Per RFC6638 section 2.3.4, the presence of ORGANIZER makes this a
        ## scheduling object resource and obliges the server to return Schedule-Tag.
        ## The ORGANIZER must match the authenticated user; the second ATTENDEE is a
        ## real local account when available so that servers like Stalwart apply full
        ## scheduling semantics and assign a Schedule-Tag.
        probe_ical = (
            "BEGIN:VCALENDAR\r\n"
            "VERSION:2.0\r\n"
            "PRODID:-//caldav-server-tester//test//EN\r\n"
            "BEGIN:VEVENT\r\n"
            f"UID:{probe_uid}\r\n"
            "DTSTART:20300101T100000Z\r\n"
            "DTEND:20300101T110000Z\r\n"
            "SUMMARY:caldav-server-tester schedule-tag probe\r\n"
            f"ORGANIZER:{own_address}\r\n"
            f"ATTENDEE;RSVP=TRUE;PARTSTAT=NEEDS-ACTION:{own_address}\r\n"
            f"ATTENDEE;RSVP=TRUE;PARTSTAT=NEEDS-ACTION:{attendee_address}\r\n"
            "END:VEVENT\r\n"
            "END:VCALENDAR\r\n"
        )

        try:
            event = cal.add_event(probe_ical)

            ## RFC6638 s3.2: server MUST return Schedule-Tag on successful PUT.
            if event.schedule_tag:
                self.set_feature("scheduling.schedule-tag")
                return

            self.set_feature(
                "scheduling.schedule-tag",
                {
                    "support": "unsupported",
                    "behaviour": "server did not return Schedule-Tag header on GET or via PROPFIND",
                },
            )
        except (DAVError, PutError) as e:
            self.set_feature("scheduling.schedule-tag", {"support": "ungraceful", "behaviour": str(e)})
        except Exception as e:
            self.set_feature("scheduling.schedule-tag", {"support": "broken", "behaviour": str(e)})
        finally:
            try:
                cal.object_by_uid(probe_uid).delete()
            except Exception:
                pass


class CheckSchedulingInboxDelivery(Check):
    """
    Checks two related scheduling features:
      scheduling.mailbox.inbox-delivery – whether incoming iTIP REQUEST messages
        appear in the attendee's schedule-inbox (RFC6638 section 4.1).
      scheduling.auto-schedule – whether the server automatically processes
        iTIP REQUESTs and adds the event to the attendee's calendar without
        requiring explicit inbox acceptance (RFC6638 SCHEDULE-AGENT=SERVER).

    When a second principal is available (via ServerQuirkChecker.extra_principals),
    uses a cross-user probe: the main user invites the second user and checks
    both the inbox and the attendee's calendar.  Reports unknown for both
    features when no second principal is configured, since RFC6638 is a
    multi-user protocol and self-invite results are unreliable (some servers
    skip self-invite delivery entirely, which RFC6638 permits).
    """

    depends_on = {CheckSchedulingDetails, PrepareCalendar}
    features_to_be_checked = {"scheduling.mailbox.inbox-delivery", "scheduling.auto-schedule"}

    def _run_check(self) -> None:
        if self.feature_ungood("scheduling.mailbox"):
            return

        principal = self.checker.principal

        ## Determine own address for composing the probe invite.
        ## Prefer calendar-user-address-set; fall back to the client username
        ## when it is unavailable (mirrors the fix for
        ## https://github.com/python-caldav/caldav/issues/399).
        if self.feature_checked("scheduling.calendar-user-address-set"):
            try:
                own_address = principal.get_vcal_address()
            except Exception:
                self.set_feature("scheduling.mailbox.inbox-delivery", {"support": "unknown"})
                self.set_feature("scheduling.auto-schedule", {"support": "unknown"})
                return
        else:
            username = getattr(self.client, "username", None)
            if not username or "@" not in str(username):
                ## No address source available; cannot compose probe invite.
                self.set_feature("scheduling.mailbox.inbox-delivery", {"support": "unknown"})
                self.set_feature("scheduling.auto-schedule", {"support": "unknown"})
                return
            own_address = "mailto:" + username

        ## Decide probe mode: cross-user (preferred) or self-invite (fallback)
        extra_principals = self.checker.extra_principals
        if extra_principals:
            attendee_principal = extra_principals[0]
            attendee_address = resolve_cu_address(attendee_principal)
            attendees = [attendee_address] if attendee_address else [attendee_principal]
        else:
            ## No second principal — cannot perform a meaningful cross-user probe.
            ## RFC6638 is a multi-user protocol; mark as unknown rather than
            ## testing against ourselves (self-invites may be skipped per RFC6638).
            ## (The early return above already handles the no-scheduling case.)
            behaviour = "not tested: only one user configured; server claims scheduling support"
            self.set_feature("scheduling.mailbox.inbox-delivery", {"support": "unknown", "behaviour": behaviour})
            self.set_feature("scheduling.auto-schedule", {"support": "unknown", "behaviour": behaviour})
            return

        ## Some servers (e.g. sabre/dav / Davis) require the attendee to have at
        ## least one calendar before they will deliver scheduling messages to the
        ## inbox.  Create a temporary calendar for the attendee if needed and
        ## remove it after the probe.
        attendee_temp_cal = None
        if extra_principals:
            try:
                if not attendee_principal.calendars():
                    attendee_temp_cal = attendee_principal.make_calendar(
                        cal_id="csc-inbox-probe-attendee-cal",
                        name="csc-inbox-probe-attendee-cal",
                    )
            except Exception:
                pass

        ## Snapshot attendee inbox before the probe
        try:
            inbox = attendee_principal.schedule_inbox()
            inbox_before = {item.url for item in inbox.get_items()}
        except Exception:
            if attendee_temp_cal:
                try:
                    attendee_temp_cal.delete()
                except Exception:
                    pass
            self.set_feature("scheduling.mailbox.inbox-delivery", {"support": "unknown"})
            self.set_feature("scheduling.auto-schedule", {"support": "unknown"})
            return

        ## Create the probe ical event
        from caldav.lib.vcal import create_ical

        probe_uid = f"csc_inbox_delivery_probe_{uuid.uuid4().hex}"
        ## Use a future date: some servers (e.g. Cyrus) skip iTIP delivery for past events.
        probe_ical = create_ical(
            objtype="VEVENT",
            uid=probe_uid,
            summary="caldav-server-tester inbox-delivery probe",
            dtstart=datetime(2099, 6, 15, 10, 0, 0, tzinfo=utc),
            dtend=datetime(2099, 6, 15, 11, 0, 0, tzinfo=utc),
        )

        ## Use a temporary calendar if possible, fall back to the shared checker calendar
        probe_cal_id = "csc-inbox-delivery-probe"
        use_temp_calendar = self.feature_checked("create-calendar") and self.feature_checked("delete-calendar")
        if use_temp_calendar:
            try:
                probe_calendar = principal.make_calendar(cal_id=probe_cal_id, name=probe_cal_id)
            except Exception:
                use_temp_calendar = False
                probe_calendar = self.checker.calendar
        else:
            probe_calendar = self.checker.calendar

        try:
            probe_calendar.save_with_invites(probe_ical, attendees)
        except Exception as e:
            self.set_feature("scheduling.mailbox.inbox-delivery", {"support": "unknown", "behaviour": str(e)})
            self.set_feature("scheduling.auto-schedule", {"support": "unknown"})
            return

        ## Check if anything new arrived in the attendee inbox.
        ## Poll for up to 30 seconds before concluding that inbox delivery is unsupported:
        ## some servers (e.g. Davis, DAViCal) deliver scheduling messages asynchronously,
        ## and deleting the probe event before polling would race with that delivery.
        new_items: set = set()
        try:
            for _ in range(30):
                inbox_after = {item.url for item in inbox.get_items()}
                new_items = inbox_after - inbox_before
                if new_items:
                    break
                time.sleep(1)
            if new_items:
                ## Clean up the inbox item(s) using the attendee's client
                attendee_client = attendee_principal.client
                for url in new_items:
                    try:
                        DAVObject(client=attendee_client, url=url).delete()
                    except Exception:
                        pass
                self.set_feature("scheduling.mailbox.inbox-delivery", True)
            else:
                self.set_feature("scheduling.mailbox.inbox-delivery", False)

            ## Check whether the event was auto-scheduled into the attendee's calendar.
            ## Only detectable with a cross-user probe; report unknown in self-invite mode.
            if extra_principals:
                auto_scheduled = False
                try:
                    auto_scheduled = any(
                        event.icalendar_component.get("UID") == probe_uid
                        for cal in attendee_principal.calendars()
                        for event in cal.get_events()
                    )
                except Exception:
                    pass
                if auto_scheduled:
                    try:
                        for cal in attendee_principal.calendars():
                            for event in cal.get_events():
                                if event.icalendar_component.get("UID") == probe_uid:
                                    event.delete()
                    except Exception:
                        pass
                self.set_feature("scheduling.auto-schedule", auto_scheduled)
            else:
                self.set_feature("scheduling.auto-schedule", {"support": "unknown"})
        except Exception as e:
            self.set_feature("scheduling.mailbox.inbox-delivery", {"support": "unknown", "behaviour": str(e)})
            self.set_feature("scheduling.auto-schedule", {"support": "unknown"})
        finally:
            ## Clean up the probe event/calendar now that polling is done.
            ## This is intentionally done AFTER polling so that async delivery
            ## is not cancelled by deleting the originating event too early.
            if use_temp_calendar:
                try:
                    probe_calendar.delete()
                except Exception:
                    pass
            else:
                try:
                    probe_calendar.object_by_uid(probe_uid).delete()
                except Exception:
                    pass
            ## Clean up the temporary attendee calendar if we created one.
            if attendee_temp_cal:
                try:
                    attendee_temp_cal.delete()
                except Exception:
                    pass


class CheckScheduleTagStablePartstat(Check):
    """
    Verifies that a PARTSTAT-only attendee update does not change the Schedule-Tag
    (RFC6638 section 3.2 requirement).

    Requires a cross-user setup (extra_principals) and auto-schedule behaviour so
    that the probe event lands in the attendee's calendar automatically.  The check
    is skipped (reported as unknown) when either prerequisite is absent.
    """

    depends_on = {CheckScheduleTag, CheckSchedulingInboxDelivery, PrepareCalendar}
    features_to_be_checked = {"scheduling.schedule-tag.stable-partstat"}

    def _run_check(self) -> None:
        if self.feature_ungood("scheduling.schedule-tag"):
            return

        extra_principals = self.checker.extra_principals
        if not extra_principals:
            self.set_feature("scheduling.schedule-tag.stable-partstat", {"support": "unknown"})
            return

        if not self.feature_checked("scheduling.auto-schedule"):
            ## Without auto-schedule the event won't appear in the attendee's
            ## calendar automatically; skip rather than implement full inbox-accept.
            self.set_feature("scheduling.schedule-tag.stable-partstat", {"support": "unknown"})
            return

        principal = self.checker.principal
        attendee_principal = extra_principals[0]

        try:
            own_address = str(principal.get_vcal_address())
        except Exception:
            self.set_feature("scheduling.schedule-tag.stable-partstat", {"support": "unknown"})
            return

        attendee_address = resolve_cu_address(attendee_principal)
        if not attendee_address:
            self.set_feature("scheduling.schedule-tag.stable-partstat", {"support": "unknown"})
            return
        attendee_address = str(attendee_address)

        probe_uid = f"csc_tag_partstat_probe_{uuid.uuid4().hex}"
        probe_ical = (
            "BEGIN:VCALENDAR\r\n"
            "VERSION:2.0\r\n"
            "PRODID:-//caldav-server-tester//test//EN\r\n"
            "BEGIN:VEVENT\r\n"
            f"UID:{probe_uid}\r\n"
            "DTSTART:20300601T100000Z\r\n"
            "DTEND:20300601T110000Z\r\n"
            "SUMMARY:caldav-server-tester schedule-tag partstat-stability probe\r\n"
            f"ORGANIZER:{own_address}\r\n"
            f"ATTENDEE;RSVP=TRUE;PARTSTAT=NEEDS-ACTION:{own_address}\r\n"
            f"ATTENDEE;RSVP=TRUE;PARTSTAT=NEEDS-ACTION:{attendee_address}\r\n"
            "END:VEVENT\r\n"
            "END:VCALENDAR\r\n"
        )

        probe_cal = self.checker.calendar
        try:
            probe_cal.save_with_invites(probe_ical, [principal, attendee_address])
        except Exception as e:
            self.set_feature("scheduling.schedule-tag.stable-partstat", {"support": "unknown", "behaviour": str(e)})
            return

        ## Wait for the event to appear in the attendee's calendar (auto-schedule)
        attendee_event = None
        for _ in range(15):
            try:
                for cal in attendee_principal.calendars():
                    try:
                        attendee_event = cal.event_by_uid(probe_uid)
                        break
                    except Exception:
                        pass
            except Exception:
                pass
            if attendee_event:
                break
            time.sleep(1)

        if attendee_event is None:
            self.set_feature("scheduling.schedule-tag.stable-partstat", {"support": "unknown"})
            self._cleanup(probe_cal, probe_uid, attendee_principal)
            return

        try:
            attendee_event.load()
            tag_before = attendee_event.schedule_tag
            if tag_before is None:
                ## Server doesn't return Schedule-Tag for the attendee copy; can't test stability
                self.set_feature("scheduling.schedule-tag.stable-partstat", {"support": "unknown"})
                return

            attendee_event.change_attendee_status(partstat="ACCEPTED")
            attendee_event.save()
            attendee_event.load()
            tag_after = attendee_event.schedule_tag

            if tag_after is None:
                self.set_feature("scheduling.schedule-tag.stable-partstat", {"support": "unknown"})
            elif tag_before == tag_after:
                self.set_feature("scheduling.schedule-tag.stable-partstat")
            else:
                self.set_feature(
                    "scheduling.schedule-tag.stable-partstat",
                    {
                        "support": "unsupported",
                        "behaviour": f"tag changed after PARTSTAT-only update: {tag_before!r} → {tag_after!r}",
                    },
                )
        except Exception as e:
            self.set_feature("scheduling.schedule-tag.stable-partstat", {"support": "unknown", "behaviour": str(e)})
        finally:
            self._cleanup(probe_cal, probe_uid, attendee_principal)

    def _cleanup(self, probe_cal, probe_uid: str, attendee_principal) -> None:
        try:
            probe_cal.object_by_uid(probe_uid).delete()
        except Exception:
            pass
        try:
            for cal in attendee_principal.calendars():
                try:
                    cal.event_by_uid(probe_uid).delete()
                    break
                except Exception:
                    pass
        except Exception:
            pass


class CheckTimezone(Check):
    """
    Checks support for non-UTC timezone information in events.

    Tests if the server accepts events with timezone information using zoneinfo.
    Some servers reject events with timezone data (returning 403 Forbidden).
    Related to GitHub issue https://github.com/python-caldav/caldav/issues/372
    """

    depends_on = {PrepareCalendar}
    features_to_be_checked = {
        "save-load.event.timezone",
    }

    def _run_check(self) -> None:
        cal = self.checker.calendar
        base = self.checker.fixture_base_year

        try:
            ## Create an event with a non-UTC timezone (America/Los_Angeles)
            ## Use unique UID since this test deletes the event
            ## (Nextcloud trashbin bug - see https://github.com/nextcloud/server/issues/30096)
            tz = ZoneInfo("America/Los_Angeles")
            event = cal.save_event(
                summary="Timezone test event",
                dtstart=datetime(base, 6, 15, 14, 0, 0, tzinfo=tz),
                dtend=datetime(base, 6, 15, 15, 0, 0, tzinfo=tz),
                uid=f"csc_timezone_test_event_{int(time.time() * 1000)}",
            )

            ## Try to load the event back
            event.load()

            ## Verify the event was saved correctly
            if event.vobject_instance:
                self.set_feature("save-load.event.timezone")
                ## Clean up
                try:
                    event.delete()
                except Exception:
                    pass
            else:
                self.set_feature(
                    "save-load.event.timezone",
                    {"support": "broken", "behaviour": "Event with timezone was saved but could not be loaded"},
                )
        except AuthorizationError as e:
            ## Server rejected the event with a 403 Forbidden
            ## This is the specific issue reported in GitHub #372
            self.set_feature(
                "save-load.event.timezone",
                {"support": "unsupported", "behaviour": f"Server rejected event with timezone (403 Forbidden): {e}"},
            )
        except DAVError as e:
            ## Other DAV error (e.g., 400 Bad Request, 500 Internal Server Error)
            self.set_feature(
                "save-load.event.timezone",
                {"support": "ungraceful", "behaviour": f"Server error when saving event with timezone: {e}"},
            )
        except Exception as e:
            ## Unexpected error
            self.set_feature(
                "save-load.event.timezone",
                {"support": "broken", "behaviour": f"Unexpected error during timezone test: {e}"},
            )


class CheckRelatedTo(Check):
    """Check if RELATED-TO properties (RFC5545 section 3.8.4.5) are preserved.

    Saves an event with two RELATED-TO lines and loads it back.
    - full: both RELATED-TO lines preserved
    - broken: only the first RELATED-TO line preserved (subsequent ones stripped)
    - unsupported: all RELATED-TO lines stripped
    """

    depends_on = {PrepareCalendar}
    features_to_be_checked = {"save-load.icalendar.related-to"}

    def _run_check(self):
        cal = self.checker.calendar
        base = self.checker.fixture_base_year
        uid = f"csc_related_to_{uuid.uuid4().hex[:8]}"
        test_obj = None
        try:
            test_obj = cal.save_object(
                Event,
                f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//tobixen//Caldav-Server-Tester//en_DK
BEGIN:VEVENT
UID:{uid}
DTSTART:{base}0601T120000Z
DTEND:{base}0601T130000Z
DTSTAMP:{base}0101T000000Z
SUMMARY:event with related-to for feature detection
RELATED-TO;RELTYPE=PARENT:some-parent-uid
RELATED-TO;RELTYPE=SIBLING:some-sibling-uid
END:VEVENT
END:VCALENDAR""",
            )
            test_obj.load()
            count = test_obj.data.count("RELATED-TO")
            if count == 0:
                self.set_feature("save-load.icalendar.related-to", False)
            elif count == 1:
                self.set_feature(
                    "save-load.icalendar.related-to",
                    {
                        "support": "broken",
                        "behaviour": "first RELATED-TO line preserved but subsequent RELATED-TO lines are stripped",
                    },
                )
            else:
                assert count == 2
                self.set_feature("save-load.icalendar.related-to")
        except (AuthorizationError, DAVError) as e:
            self.set_feature("save-load.icalendar.related-to", {"support": "ungraceful", "behaviour": str(e)})
        finally:
            if test_obj:
                try:
                    test_obj.delete()
                except Exception:
                    pass


class CheckOpenTimeRangeSearch(Check):
    """Check open-ended time-range search behaviour for VTODOs and VEVENTs.

    RFC4791 section 9.9: the CALDAV:time-range element's "start" and "end"
    attributes are both optional; if absent, assume -infinity and +infinity
    respectively.  At least one must be present.

    - search.time-range.open.start.duration: components with DTSTART+DURATION (no
      DTEND/DUE) must be found by time-range searches overlapping their computed
      interval.  Tested for both VTODO (csc_task_with_duration) and VEVENT
      (csc_event_with_duration).  If support is asymmetric across component types
      the feature is marked "broken" with a behaviour note; separate per-component
      sub-features may be introduced in a future iteration.

    - search.time-range.open.start: when searching with only an end bound (open
      start, start assumed -infinity), tasks whose DTSTART is after the end bound
      must NOT be returned.  Uses end=<base>-01-01 (all fixture tasks start Jan 8+).

    - search.time-range.open.end: when searching with only a start bound (open end,
      end assumed +infinity), tasks that overlap the start bound must be returned.
      Uses start=<base>-01-09 to find csc_simple_task3 (DTSTART=<base>-01-09T12:00Z,
      DUE=<base>-01-09T13:00Z); per RFC4791 sec 9.9 condition for VTODO with DTSTART+DUE
      and absent end: (start < DUE) OR (start <= DTSTART) → TRUE.
    """

    depends_on = {CheckSearch}
    features_to_be_checked = {
        "search.time-range.open.start.duration",
        "search.time-range.open.start",
        "search.time-range.open.end",
    }

    def _run_check(self):
        if not self.feature_checked("search.time-range.todo"):
            for feat in self.features_to_be_checked:
                self.set_feature(feat, None)
            return

        tasklist = self.checker.tasklist
        cal = self.checker.calendar
        base = self.checker.fixture_base_year

        ## Test 1: Components with DTSTART+DURATION must be found by time-range
        ## searches that overlap their computed interval.
        ## RFC4791 section 9.9:
        ##   VEVENT (duration > 0s): (start < DTSTART+DURATION) AND (end > DTSTART)
        ##   VTODO:  (start <= DTSTART+DURATION) AND ((end > DTSTART) OR (end >= DTSTART+DURATION))

        ## Test 1a: VTODO — csc_task_with_duration (DTSTART=<base>-01-18T12:00Z, DURATION=PT2H → ends 14:00Z).
        ## Two overlap scenarios:
        ##   a) Search [13:00, 15:00]: start inside duration, end after → must match.
        ##   b) Search [11:00, 13:00]: start before DTSTART, end inside duration → must match.
        ## Sanity: csc_simple_task3 (DTSTART=Jan 9) must NOT appear in a Jan-18 search.
        todo_dur_ok = None
        dur_err = None
        try:
            results_a = tasklist.search(
                start=datetime(base, 1, 18, 13, 0, 0, tzinfo=utc),
                end=datetime(base, 1, 18, 15, 0, 0, tzinfo=utc),
                todo=True,
                include_completed=True,
                post_filter=False,
            )
            results_b = tasklist.search(
                start=datetime(base, 1, 18, 11, 0, 0, tzinfo=utc),
                end=datetime(base, 1, 18, 13, 0, 0, tzinfo=utc),
                todo=True,
                include_completed=True,
                post_filter=False,
            )
            found_a = any(r.component.get("UID") == "csc_task_with_duration" for r in results_a)
            found_b = any(r.component.get("UID") == "csc_task_with_duration" for r in results_b)
            not_spurious = not any(r.component.get("UID") == "csc_simple_task3" for r in results_a)
            if not not_spurious:
                ## The server returned csc_simple_task3, whose DTSTART (Jan 9) is
                ## outside the Jan-18 search window: it does not honour the VTODO
                ## time-range at all (tracked separately as
                ## search.time-range.todo.strict).  With the range ignored we
                ## cannot tell whether DTSTART+DURATION is interpreted correctly,
                ## so the VTODO duration result is inconclusive (None), not a
                ## failure - otherwise this feature spuriously reports "broken"
                ## (asymmetric) on every server with a non-strict VTODO time-range
                ## (e.g. Open-Xchange).
                todo_dur_ok = None
            else:
                todo_dur_ok = found_a and found_b
        except (AuthorizationError, DAVError) as e:
            todo_dur_ok = False
            dur_err = str(e)

        ## Test 1b: VEVENT — csc_event_with_duration (DTSTART=<base>-01-17T12:00Z, DURATION=PT1H → ends 13:00Z).
        ## Only run if event time-range search is known to work.
        ## Two overlap scenarios:
        ##   a) Search [12:30, 14:00]: start inside duration, end after → must match.
        ##   b) Search [11:00, 12:30]: start before DTSTART, end inside duration → must match.
        ## Sanity: csc_simple_event1 (Jan 1) must NOT appear in a Jan-17 search.
        event_dur_ok = None
        if self.feature_checked("search.time-range.event"):
            try:
                ev_a = cal.search(
                    start=datetime(base, 1, 17, 12, 30, 0, tzinfo=utc),
                    end=datetime(base, 1, 17, 14, 0, 0, tzinfo=utc),
                    event=True,
                    post_filter=False,
                )
                ev_b = cal.search(
                    start=datetime(base, 1, 17, 11, 0, 0, tzinfo=utc),
                    end=datetime(base, 1, 17, 12, 30, 0, tzinfo=utc),
                    event=True,
                    post_filter=False,
                )
                ev_found_a = any(r.component.get("UID") == "csc_event_with_duration" for r in ev_a)
                ev_found_b = any(r.component.get("UID") == "csc_event_with_duration" for r in ev_b)
                ev_not_spurious = not any(r.component.get("UID") == "csc_simple_event1" for r in ev_a)
                event_dur_ok = ev_found_a and ev_found_b and ev_not_spurious
            except (AuthorizationError, DAVError) as e:
                event_dur_ok = False
                if not dur_err:
                    dur_err = str(e)

        ## Combine VTODO and VEVENT duration results, considering only the
        ## component types that gave a conclusive answer (None = not tested or
        ## inconclusive, e.g. VTODO when the server ignores the time-range).
        conclusive = [r for r in (todo_dur_ok, event_dur_ok) if r is not None]
        if not conclusive:
            ## Nothing conclusive to judge from.
            if dur_err:
                self.set_feature(
                    "search.time-range.open.start.duration", {"support": "ungraceful", "behaviour": dur_err}
                )
            else:
                self.set_feature("search.time-range.open.start.duration", None)
        elif all(conclusive):
            self.set_feature("search.time-range.open.start.duration")
        elif not any(conclusive):
            if dur_err:
                self.set_feature(
                    "search.time-range.open.start.duration", {"support": "ungraceful", "behaviour": dur_err}
                )
            else:
                self.set_feature("search.time-range.open.start.duration", False)
        else:
            ## Genuinely asymmetric: both component types gave a conclusive answer
            ## and they disagree (one finds its DTSTART+DURATION component, the
            ## other does not).
            todo_status = "supported" if todo_dur_ok else "unsupported"
            event_status = "supported" if event_dur_ok else "unsupported"
            self.set_feature(
                "search.time-range.open.start.duration",
                {
                    "support": "broken",
                    "behaviour": f"asymmetric DURATION support: VTODO {todo_status}, VEVENT {event_status}",
                },
            )

        ## Test 2: open-start search (only end bound, start assumed -infinity).
        ## Must not return tasks whose DTSTART is after the end bound.
        ## All fixture tasks have DTSTART on Jan 8 or later, so a search ending
        ## at <base>-01-01 should return none of them.
        ## Uses csc_simple_task3 (DTSTART+DUE) and csc_task_with_duration (DTSTART+DURATION).
        known_future_uids = {"csc_simple_task3", "csc_task_with_duration"}
        try:
            results = tasklist.search(
                end=datetime(base, 1, 1, tzinfo=utc),
                todo=True,
                include_completed=True,
                post_filter=False,
            )
            returned_uids = {r.component.get("UID") for r in results}
            found_future = bool(returned_uids & known_future_uids)
            if found_future:
                self.set_feature(
                    "search.time-range.open.start",
                    {
                        "support": "broken",
                        "behaviour": "tasks with DTSTART after end bound returned when only an end bound is given",
                    },
                )
            else:
                self.set_feature("search.time-range.open.start")
        except (AuthorizationError, DAVError) as e:
            self.set_feature("search.time-range.open.start", {"support": "ungraceful", "behaviour": str(e)})

        ## Test 3: open-end search (only start bound, end assumed +infinity).
        ## RFC4791 sec 9.9: for VTODO with DTSTART+DUE and absent end (+infinity),
        ## condition is (start < DUE) OR (start <= DTSTART).
        ## csc_simple_task3 (DTSTART=<base>-01-09T12:00Z, DUE=<base>-01-09T13:00Z) with
        ## start=<base>-01-09T00:00Z: (Jan 9 < Jan 9T13:00) = TRUE → must be returned.
        try:
            results = tasklist.search(
                start=datetime(base, 1, 9, tzinfo=utc),
                todo=True,
                include_completed=True,
                post_filter=False,
            )
            found = any(r.component.get("UID") == "csc_simple_task3" for r in results)
            self.set_feature("search.time-range.open.end", found)
        except (AuthorizationError, DAVError) as e:
            self.set_feature("search.time-range.open.end", {"support": "ungraceful", "behaviour": str(e)})


class CheckTodoTimeRangeStrict(Check):
    """Check that VTODO time-range searches do not return tasks outside the searched range.

    Motivated by a xandikos bug where having a VTODO with DTSTART+DURATION (no DUE) on
    the calendar caused xandikos's index-fallback logic to also return other tasks whose
    DTSTART+DUE were entirely outside the searched range.

    Two probes:
    1. A freshly created VTODO with DTSTART+DUE in March (next year) must NOT appear in a
       [Jan 9, Jan 10] search.  This exercises the same code path as the xandikos bug
       (index-based filtering of a task with both DTSTART and DUE in the index).
    2. csc_task_with_duration (DTSTART=<base>-01-18, DURATION=PT2H) must NOT appear in the
       same search.  This exercises the DURATION fallback path (no DUE means the server
       must fall back from index-based filtering to a full file check).
    """

    depends_on = {CheckSearch}
    features_to_be_checked = {"search.time-range.todo.strict"}

    def _run_check(self) -> None:
        if not self.feature_checked("search.time-range.todo"):
            self.set_feature("search.time-range.todo.strict", None)
            return

        tasklist = self.checker.tasklist
        base = self.checker.fixture_base_year
        temp_todo = None
        try:
            ## Create a task clearly outside the Jan 9-10 search window.
            ## DTSTART=<base>-03-15 is two months after the range end (Jan 10), so a
            ## correct server must not return it.  Unlike csc_task_future (which was
            ## removed because xandikos returned it erroneously), this task is created
            ## fresh so it is present exactly during this check and then cleaned up.
            temp_todo = tasklist.save_object(
                Todo,
                uid="csc_temp_outside_range",
                summary="temporary task outside search range (should not appear in Jan search)",
                dtstart=datetime(base, 3, 15, 12, 0, 0, tzinfo=utc),
                due=datetime(base, 3, 15, 13, 0, 0, tzinfo=utc),
            )

            results = tasklist.search(
                start=datetime(base, 1, 9, tzinfo=utc),
                end=datetime(base, 1, 10, tzinfo=utc),
                todo=True,
                include_completed=True,
                post_filter=False,
            )
            returned_uids = {r.component.get("UID") for r in results}

            ## Neither the fresh March task nor the pre-existing Jan-18 DURATION task
            ## should appear in a Jan 9-10 search.
            false_positive_uids = returned_uids & {"csc_temp_outside_range", "csc_task_with_duration"}
            if false_positive_uids:
                self.set_feature(
                    "search.time-range.todo.strict",
                    {
                        "support": "broken",
                        "behaviour": f"tasks outside range returned: {sorted(false_positive_uids)}",
                    },
                )
            else:
                self.set_feature("search.time-range.todo.strict")
        except (AuthorizationError, DAVError) as e:
            self.set_feature("search.time-range.todo.strict", {"support": "ungraceful", "behaviour": str(e)})
        finally:
            if temp_todo:
                try:
                    temp_todo.delete()
                except Exception:
                    pass
