"""Unit tests for the ``save-load.stable-url`` probe in PrepareCalendar.

The question is whether a client may compare a searched object's URL with one
it constructed as ``<collection>/<uid>.ics``.  What the answer must not depend
on is which of a collection's addresses the run happens to be holding: OX
serves every calendar both at the requested cal_id and at an opaque canonical
``cal://0/NNN`` path and reports objects under the canonical one, so a run that
*created* the test calendar (and so adopted the canonical URL) used to call OX
stable while a run that *found* it again by cal_id (an alias, minted by pure
URL arithmetic and never round-tripped) called the same server unstable.

Same server, same object, opposite verdicts - and the consumers compare whole
URLs, so the honest answer for such a server is "no" either way.  That half is
read from ``create-calendar.stable-url``, which probes the relocation
deterministically.
"""

from unittest.mock import Mock

from caldav.compatibility_hints import FeatureSet
from caldav.lib.error import NotFoundError
from caldav.lib.url import URL

from caldav_server_tester.checks import PrepareCalendar

ALIAS = "http://dav.example.com/caldav/caldav-server-checker-calendar/"
CANONICAL = "http://dav.example.com/caldav/Y2FsOi8vMC8xMzY/"
UID = "csc_simple_event1"


def _make_checker(create_calendar_stable_url=None) -> Mock:
    checker = Mock()
    checker._features_checked = FeatureSet()
    if create_calendar_stable_url is not None:
        checker._features_checked.set_feature("create-calendar.stable-url", create_calendar_stable_url)
    checker.features_checked = checker._features_checked
    checker.debug_mode = None
    checker.fixture_base_year = 2026
    checker._client_obj = Mock()
    checker._client_obj.features = FeatureSet()
    checker.expected_features = FeatureSet()
    return checker


def _calendar(handle_url, reported_object_url=None, raises=None) -> Mock:
    """A calendar addressed at ``handle_url`` whose REPORT answers with
    ``reported_object_url`` (defaulting to the object sitting right under the
    handle)."""
    calendar = Mock()
    calendar.url = URL.objectify(handle_url)
    if raises is not None:

        def object_by_uid(uid):
            raise raises

    else:
        url = reported_object_url or handle_url + UID + ".ics"

        def object_by_uid(uid):
            obj = Mock()
            obj.url = URL.objectify(url)
            return obj

    calendar.object_by_uid = object_by_uid
    return calendar


def _probe(calendar, create_calendar_stable_url=None) -> str:
    checker = _make_checker(create_calendar_stable_url)
    PrepareCalendar(checker)._check_stable_url(calendar)
    return checker.features_checked.is_supported("save-load.stable-url", str)


class TestAnOrdinaryServer:
    def test_the_constructed_url_is_the_reported_url(self) -> None:
        assert _probe(_calendar(ALIAS), create_calendar_stable_url=True) == "full"

    def test_a_server_that_renames_the_resource_is_unsupported(self) -> None:
        """The client PUT ``<uid>.ics`` and the server reports something else,
        so the URL cannot be constructed at all."""
        assert (
            _probe(
                _calendar(ALIAS, reported_object_url=ALIAS + "7f3a9c12-server-assigned.ics"),
                create_calendar_stable_url=True,
            )
            == "unsupported"
        )

    def test_a_lookup_that_says_nothing_records_nothing(self) -> None:
        assert _probe(_calendar(ALIAS, raises=NotFoundError("nope"))) == "unknown"


class TestACollectionWithTwoAddresses:
    """The OX shape, from both ends.  Same server, same object, two handles -
    and the verdict has to be the same both times."""

    RELOCATES = {"support": "unsupported", "behaviour": "canonical URL segment differs"}

    def test_the_alias_handle_cannot_construct_the_url(self) -> None:
        """What a run that found the calendar by cal_id sees."""
        assert (
            _probe(
                _calendar(ALIAS, reported_object_url=CANONICAL + UID + ".ics"),
                create_calendar_stable_url=self.RELOCATES,
            )
            == "unsupported"
        )

    def test_the_canonical_handle_gets_the_same_answer(self) -> None:
        """What a run that created the calendar sees, having adopted the
        canonical URL.  The URLs do match here - but only because this run
        happened to be holding that address, and the library hands out either,
        so the feature must not report it as a property of the server."""
        assert (
            _probe(
                _calendar(CANONICAL, reported_object_url=CANONICAL + UID + ".ics"),
                create_calendar_stable_url=self.RELOCATES,
            )
            == "unsupported"
        )

    def test_the_two_reasons_are_told_apart_in_the_behaviour(self) -> None:
        checker = _make_checker(self.RELOCATES)
        PrepareCalendar(checker)._check_stable_url(_calendar(CANONICAL, reported_object_url=CANONICAL + UID + ".ics"))
        node = checker.features_checked.is_supported("save-load.stable-url", dict)
        assert "create-calendar.stable-url" in node["behaviour"]
        assert "keeps the name it was stored under" in node["behaviour"]

    def test_a_rename_is_reported_as_a_rename(self) -> None:
        """A relocating server may rename the object too, and then that is the
        more specific thing to say."""
        checker = _make_checker(self.RELOCATES)
        PrepareCalendar(checker)._check_stable_url(
            _calendar(ALIAS, reported_object_url=CANONICAL + "server-assigned.ics")
        )
        node = checker.features_checked.is_supported("save-load.stable-url", dict)
        assert node["support"] == "unsupported"
        assert "server-assigned.ics" in node["behaviour"]


class TestANonObservationIsNotAnObservation:
    def test_a_relocation_probe_that_gave_up_does_not_decide_this_one(self) -> None:
        """``create-calendar.stable-url`` recorded as ``unknown`` says the probe
        ran and could not tell.  That is not evidence that a constructed object
        URL will not match."""
        assert _probe(_calendar(ALIAS), create_calendar_stable_url={"support": "unknown"}) == "full"

    def test_a_relocation_never_probed_does_not_decide_this_one(self) -> None:
        """On a server where neither MKCALENDAR nor MKCOL works,
        ``CheckMakeDeleteCalendar`` records ``create-calendar`` unsupported and
        never reaches its ``stable-url`` sub-probe, so that feature is never
        set at all.  ``is_supported()`` walks *up* the dotted tree when a
        feature is unset, so asking for the child hands back the parent's
        ``unsupported`` - an observation about whether calendars can be created,
        which says nothing about where an existing one is addressable."""
        checker = _make_checker()
        checker.features_checked.set_feature("create-calendar", False)
        PrepareCalendar(checker)._check_stable_url(_calendar(ALIAS))
        assert checker.features_checked.is_supported("save-load.stable-url", str) == "full"


class TestTheSpellingOfTheNameIsNotTheQuestion:
    def test_a_percent_encoded_name_is_compared_decoded(self) -> None:
        """``url.encode-at`` owns the ``@``-versus-``%40`` question; a probe
        that answered it here too would report a spelling difference as a
        server that renames resources."""
        calendar = _calendar(ALIAS + "x/", reported_object_url=ALIAS + "x/" + "csc_simple%5Fevent1.ics")
        assert _probe(calendar, create_calendar_stable_url=True) == "full"


class TestTheRelocationIsDescribedAsTheServerDidIt:
    """The derived branch used to spell out the OX shape - a collection
    "served at more than one address" - for every server that got there.  That
    is not what Zimbra does: a display name relocates the collection, and the
    alias left behind at the requested cal_id 404s on child objects, so it has
    one usable address, not two.  Say what ``create-calendar.stable-url``
    actually observed instead of restating one server's flavour of it."""

    def _behaviour(self, create_calendar_stable_url) -> str:
        checker = _make_checker(create_calendar_stable_url)
        PrepareCalendar(checker)._check_stable_url(_calendar(ALIAS, reported_object_url=CANONICAL + UID + ".ics"))
        return checker.features_checked.is_supported("save-load.stable-url", dict)["behaviour"]

    def test_the_observed_reason_is_carried_through(self) -> None:
        behaviour = self._behaviour(
            {
                "support": "unsupported",
                "behaviour": "a display name set at creation relocates the collection",
            }
        )
        assert "a display name set at creation relocates the collection" in behaviour

    def test_no_server_specific_flavour_is_invented(self) -> None:
        assert "more than one address" not in self._behaviour({"support": "unsupported"})

    def test_an_observation_without_a_reason_still_explains_itself(self) -> None:
        behaviour = self._behaviour({"support": "unsupported"})
        assert "create-calendar.stable-url" in behaviour
        assert "keeps the name it was stored under" in behaviour
