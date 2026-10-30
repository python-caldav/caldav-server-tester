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
            if uid != UID:
                raise NotFoundError(uid)
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


NAME_PROBE_UID = "csc_stable_url_probe"
NAME_PROBE_NAME = "csc_stable_url_probe_name.ics"


def _name_probe_calendar(
    reported_as, put_fails=False, lookup_misses=False, put_status=None, redirect_to=None, reported_base=ALIAS
) -> tuple[Mock, Mock]:
    """A calendar that reports the object PUT as the probe's arbitrary name
    under ``reported_as``, besides the ordinary fixture.  Returns the
    calendar and the client the PUT and DELETE go through.  With
    ``redirect_to``, the PUT is answered with a 302 there that the client
    did not follow, and nothing is stored."""
    calendar = _calendar(ALIAS)
    ordinary = calendar.object_by_uid

    def object_by_uid(uid):
        if uid != NAME_PROBE_UID:
            return ordinary(uid)
        if lookup_misses or redirect_to:
            raise NotFoundError(uid)
        obj = Mock()
        obj.url = URL.objectify(reported_base + reported_as)
        return obj

    calendar.object_by_uid = object_by_uid
    client = Mock()
    client.put.return_value = Mock(status=201, headers={})
    if put_fails:
        client.put.side_effect = NotFoundError("refused")
    if put_status is not None:
        ## DAVClient.put hands back a refusal as a response, it does not raise
        client.put.return_value = Mock(status=put_status, headers={})
    if redirect_to:
        client.put.return_value = Mock(status=302, headers={"Location": redirect_to})
    return calendar, client


def _name_probe(reported_as, create_calendar_stable_url=None, **kw) -> tuple[dict, Mock]:
    calendar, client = _name_probe_calendar(reported_as, **kw)
    checker = _make_checker(create_calendar_stable_url)
    checker._client_obj = client
    PrepareCalendar(checker)._check_stable_url(calendar)
    return checker.features_checked.is_supported("save-load.stable-url", dict), client


class TestAnObjectStoredUnderAnArbitraryName:
    """Storing ``<uid>.ics`` cannot tell a server that keeps the name from one
    that names every object ``<uid>.ics``: the two agree.  So the probe also
    stores an object under a name that is not its UID."""

    def test_the_name_is_kept(self) -> None:
        node, client = _name_probe(NAME_PROBE_NAME)
        assert node["support"] == "full"
        assert "forces_uid_ics" not in node
        assert str(client.put.call_args.args[0]) == ALIAS + NAME_PROBE_NAME

    def test_a_server_naming_it_uid_ics_forces_the_convention(self) -> None:
        node, _ = _name_probe(NAME_PROBE_UID + ".ics")
        assert node["support"] == "unsupported"
        assert node["forces_uid_ics"] is True
        assert NAME_PROBE_NAME in node["behaviour"]

    def test_another_rename_is_unsupported_without_the_convention(self) -> None:
        node, _ = _name_probe("7f3a9c12-server-assigned.ics")
        assert node["support"] == "unsupported"
        assert "forces_uid_ics" not in node

    def test_a_refused_put_leaves_the_verdict_alone(self) -> None:
        node, _ = _name_probe(NAME_PROBE_NAME, put_fails=True)
        assert node["support"] == "full"

    def test_a_put_answered_with_a_refusal_leaves_the_verdict_alone(self) -> None:
        """A 4xx comes back as a response, and the object is not there to be
        found."""
        node, _ = _name_probe(NAME_PROBE_NAME, put_status=403, lookup_misses=True)
        assert node["support"] == "full"

    def test_a_lookup_that_misses_after_an_accepted_put_leaves_the_verdict_alone(self) -> None:
        node, _ = _name_probe(NAME_PROBE_NAME, lookup_misses=True)
        assert node["support"] == "full"

    def test_a_forced_name_is_not_described_as_unconstructable(self) -> None:
        node, _ = _name_probe(NAME_PROBE_UID + ".ics")
        assert "cannot construct" not in node["behaviour"]
        assert "<uid>.ics" in node["behaviour"]

    def test_an_unpredictable_name_is_described_as_unconstructable(self) -> None:
        node, _ = _name_probe("7f3a9c12-server-assigned.ics")
        assert "cannot construct" in node["behaviour"]

    def test_the_probe_object_is_removed(self) -> None:
        _, client = _name_probe(NAME_PROBE_UID + ".ics")
        deleted = {str(call.args[0]) for call in client.delete.call_args_list}
        assert {ALIAS + NAME_PROBE_NAME, ALIAS + NAME_PROBE_UID + ".ics"} <= deleted

    def test_an_object_the_server_named_itself_is_removed_too(self) -> None:
        _, client = _name_probe("7f3a9c12-server-assigned.ics")
        deleted = {str(call.args[0]) for call in client.delete.call_args_list}
        assert ALIAS + "7f3a9c12-server-assigned.ics" in deleted

    def test_a_redirect_not_followed_names_the_object(self) -> None:
        """The Zimbra docker image redirects the PUT to ``<uid>.ics`` on its
        internal port 8443, which the client does not follow (caldav repeats
        a redirected PUT on the same host and port only).  Nothing is stored,
        but the Location says the server's name for it - the verdict must be
        the one the reachable production server gets."""
        node, _ = _name_probe(
            NAME_PROBE_NAME,
            redirect_to="https://dav.example.com:8443/caldav/caldav-server-checker-calendar/" + NAME_PROBE_UID + ".ics",
        )
        assert node["support"] == "unsupported"
        assert node["forces_uid_ics"] is True

    def test_a_relative_location_is_read_too(self) -> None:
        node, _ = _name_probe(NAME_PROBE_NAME, redirect_to=NAME_PROBE_UID + ".ics")
        assert node["forces_uid_ics"] is True

    def test_a_relocated_collection_does_not_hide_the_name(self) -> None:
        """Zimbra relocates a calendar created with a display name, and that
        verdict used to be returned before the name was probed."""
        node, _ = _name_probe(
            NAME_PROBE_UID + ".ics",
            create_calendar_stable_url={"support": "unsupported"},
        )
        assert node["support"] == "unsupported"
        assert node["forces_uid_ics"] is True
        assert "create-calendar.stable-url" in node["behaviour"]

    def test_the_object_is_removed_where_the_server_reported_it(self) -> None:
        """On Zimbra a rerun holds the cal_id alias, while the object lives
        under the canonical collection URL."""
        _, client = _name_probe(NAME_PROBE_UID + ".ics", reported_base=CANONICAL)
        deleted = {str(call.args[0]) for call in client.delete.call_args_list}
        assert CANONICAL + NAME_PROBE_UID + ".ics" in deleted

    def test_a_redirect_elsewhere_says_nothing(self) -> None:
        """A redirect to a login page names no object."""
        node, _ = _name_probe(NAME_PROBE_NAME, redirect_to="https://dav.example.com/login")
        assert node["support"] == "full"

    def test_a_redirect_without_location_falls_back_to_the_lookup(self) -> None:
        node, _ = _name_probe(NAME_PROBE_UID + ".ics", put_status=302)
        assert node["forces_uid_ics"] is True
