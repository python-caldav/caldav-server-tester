"""The other two axes of the ``url.encode-at`` probe, and how the three merge.

``tests/test_encode_at_check.py`` covers the object axis - an ``@`` in the name
of an ``.ics``.  The feature is not really about ``.ics`` names, though: the
ownCloud/Nextcloud shape it exists for is a *calendar-home-set*
(``/remote.php/dav/calendars/user@example.com/``), an ``@`` inside a collection
segment that came from a username.  So there are two more axes - a calendar id
and a server-given principal path - and the three share the same three
subfeatures.  What they cannot be allowed to do is quietly outvote each other,
which is what most of this file is about.
"""

import logging
import warnings
from unittest.mock import Mock

from caldav.compatibility_hints import FeatureSet
from caldav.lib.error import AuthorizationError, NotFoundError

from caldav_server_tester.checks import (
    ENCODE_AT_CAL_ID,
    ENCODE_AT_IN_COLLECTION_UID,
    PrepareCalendar,
    _collection_axis,
    _object_axis,
    _principal_axis,
)

HOME = "http://dav.example.com/cal/"
CAL_LITERAL = HOME + ENCODE_AT_CAL_ID + "/"
CAL_ENCODED = HOME + ENCODE_AT_CAL_ID.replace("@", "%40") + "/"
OBJ = ENCODE_AT_IN_COLLECTION_UID + ".ics"

PRINCIPAL_LITERAL = "http://dav.example.com/p/alice@example.com/"
PRINCIPAL_ENCODED = "http://dav.example.com/p/alice%40example.com/"


def _make_checker(username="alice@example.com", principal_url=PRINCIPAL_LITERAL, home_url=HOME) -> Mock:
    """A checker whose calendar-create features are probed and working.

    Both are set explicitly rather than left to the library defaults: the
    collection axis declines to run when the server cannot create *and* delete
    calendars, and a test that leaned on a default would stop testing the thing
    it names the moment that default moved.
    """
    from caldav.lib.url import URL

    checker = Mock()
    checker._features_checked = FeatureSet()
    checker._features_checked.set_feature("create-calendar", True)
    checker._features_checked.set_feature("delete-calendar", True)
    checker.features_checked = checker._features_checked
    checker.debug_mode = None
    checker.fixture_base_year = 2026
    checker._client_obj = Mock()
    checker._client_obj.features = FeatureSet()
    checker._client_obj.username = username
    checker.expected_features = FeatureSet()
    checker.principal = Mock()
    checker.principal.url = URL.objectify(principal_url) if principal_url else None
    checker.principal.calendar_home_set.url = URL.objectify(home_url)
    return checker


class FakeCollectionServer:
    """A calendar home holding at most one collection per spelling of the cal_id.

    ``accepts`` names the spellings that will take a MKCALENDAR/MKCOL at all;
    ``aliased`` makes the two spellings one storage slot, which is the whole
    point of the identity axis.  Collections hold objects by file name, so an
    object written through one spelling of the *collection* is visible through
    the other exactly when the two are aliased - which is the question.
    """

    def __init__(self, accepts, aliased=True, mkcal_status=None, propfind_status=None, raises=None):
        self.accepts = set(accepts)
        self.aliased = aliased
        self.mkcal_status = mkcal_status or {}
        self.propfind_status = propfind_status or {}
        self.raises = raises or {}
        self.collections: dict[str, dict[str, str]] = {}
        self.calls: list[tuple[str, str]] = []

    def install(self, checker):
        checker._client_obj.mkcalendar.side_effect = self.mkcalendar
        checker._client_obj.mkcol.side_effect = self.mkcol
        checker._client_obj.propfind.side_effect = self.propfind
        checker._client_obj.put.side_effect = self.put
        checker._client_obj.request.side_effect = self.request
        checker._client_obj.delete.side_effect = self.delete
        return self

    def _slot(self, collection_url):
        return "shared" if self.aliased else collection_url

    def _split(self, url):
        """``(collection_url, remainder)``, or ``(None, url)`` for anything else."""
        for spelling in (CAL_LITERAL, CAL_ENCODED):
            if url.startswith(spelling):
                return spelling, url[len(spelling) :]
        return None, url

    def mkcalendar(self, url, body="", dummy=None, _method="MKCALENDAR"):
        url = str(url)
        self.calls.append((_method, url))
        if url in self.raises:
            raise self.raises[url]
        if url in self.mkcal_status:
            return Mock(status=self.mkcal_status[url])
        if url not in self.accepts:
            return Mock(status=403)
        slot = self._slot(url)
        if slot in self.collections:
            ## RFC4918: MKCOL/MKCALENDAR on an existing collection is 405
            return Mock(status=405)
        self.collections[slot] = {}
        return Mock(status=201)

    def mkcol(self, url, body, dummy=None):
        return self.mkcalendar(url, body, _method="MKCOL")

    def propfind(self, url=None, props=None, depth=0):
        url = str(url)
        self.calls.append(("PROPFIND", url))
        if url in self.raises:
            raise self.raises[url]
        if url in self.propfind_status:
            return Mock(status=self.propfind_status[url])
        collection, rest = self._split(url)
        if collection is None or rest:
            return Mock(status=404)
        return Mock(status=207 if self._slot(collection) in self.collections else 404)

    def put(self, url, body, headers=None):
        url = str(url)
        self.calls.append(("PUT", url))
        collection, rest = self._split(url)
        if collection is None or not rest:
            return Mock(status=403)
        objects = self.collections.get(self._slot(collection))
        if objects is None:
            return Mock(status=404)
        objects[rest] = [line for line in body.splitlines() if line.startswith("UID:")][0][4:]
        return Mock(status=201)

    def request(self, url, *args, **kwargs):
        url = str(url)
        self.calls.append(("GET", url))
        collection, rest = self._split(url)
        objects = self.collections.get(self._slot(collection)) if collection else None
        uid = objects.get(rest) if objects else None
        r = Mock()
        if uid is None:
            r.status = 404
            r.raw = "<html>not found</html>"
        else:
            r.status = 200
            r.raw = f"BEGIN:VCALENDAR\r\nUID:{uid}\r\nEND:VCALENDAR"
        return r

    def delete(self, url):
        url = str(url)
        self.calls.append(("DELETE", url))
        collection, rest = self._split(url)
        if collection is not None and not rest:
            self.collections.pop(self._slot(collection), None)
        elif collection is not None:
            objects = self.collections.get(self._slot(collection))
            if objects:
                objects.pop(rest, None)
        return Mock(status=204)


def _collection(checker):
    return PrepareCalendar(checker)._probe_encode_at_collection()


def _support(checker, name):
    node = checker._features_checked.is_supported(f"url.encode-at.{name}", dict, return_defaults=False)
    return node.get("support") if isinstance(node, dict) else None


def _behaviour(checker, name):
    node = checker._features_checked.is_supported(f"url.encode-at.{name}", dict, return_defaults=False)
    return node.get("behaviour", "") if isinstance(node, dict) else ""


class TestTheSpellingsActuallyDiffer:
    def test_the_two_calendar_spellings_differ(self) -> None:
        """Everything on this axis rests on the two URLs not being the same string."""
        assert CAL_LITERAL != CAL_ENCODED
        assert "@" in CAL_LITERAL and "%40" in CAL_ENCODED

    def test_the_in_collection_object_carries_no_at_of_its_own(self) -> None:
        """Otherwise the object axis would be riding along inside this one and a
        verdict here would not be about the collection segment at all."""
        assert "@" not in ENCODE_AT_IN_COLLECTION_UID

    def test_the_probe_calendar_is_cleanup_eligible(self) -> None:
        """checker.PROBE_CALENDAR_PREFIXES sweeps whatever a crashed run left."""
        from caldav_server_tester.checker import PROBE_CALENDAR_PREFIXES

        assert any(ENCODE_AT_CAL_ID.startswith(p) for p in PROBE_CALENDAR_PREFIXES)


class TestTheCollectionAxis:
    def test_two_collections_are_observed_as_two(self) -> None:
        """The conformant server: '@' and '%40' name different collections.

        This is the case that has to be *observed* rather than defaulted - the
        library's default says the two spellings are one, so a server that gets
        RFC3986 right is precisely the one a silent default gets wrong.
        """
        checker = _make_checker()
        FakeCollectionServer(accepts={CAL_LITERAL, CAL_ENCODED}, aliased=False).install(checker)
        observation = _collection(checker)
        assert observation.literal == "full"
        assert observation.encoded == "full"
        assert observation.identity == "full"

    def test_one_collection_under_two_names_is_observed_as_one(self) -> None:
        """An object stored through '@' comes back through '%40': same collection."""
        checker = _make_checker()
        FakeCollectionServer(accepts={CAL_LITERAL, CAL_ENCODED}, aliased=True).install(checker)
        observation = _collection(checker)
        assert observation.literal == "full"
        assert observation.encoded == "full"
        assert observation.identity == "unsupported"

    def test_the_owncloud_shape_refuses_a_literal_at(self) -> None:
        checker = _make_checker()
        FakeCollectionServer(accepts={CAL_ENCODED}, aliased=False).install(checker)
        observation = _collection(checker)
        assert observation.literal == "unsupported"
        assert observation.encoded == "full"
        assert observation.identity is None

    def test_a_server_that_will_not_take_the_encoded_spelling(self) -> None:
        """'%40' neither serves the collection's contents nor accepts one of its
        own - the only reading left is that the encoded spelling does not work.
        Identity stays unobserved rather than being guessed at."""
        checker = _make_checker()
        FakeCollectionServer(accepts={CAL_LITERAL}, aliased=False).install(checker)
        observation = _collection(checker)
        assert observation.literal == "full"
        assert observation.encoded == "unsupported"
        assert observation.identity is None

    def test_an_existing_collection_at_the_encoded_spelling_is_not_a_refusal(self) -> None:
        """RFC4918 answers a MKCOL/MKCALENDAR on an existing collection with 405.

        A server that aliases the two spellings but does not serve the
        collection's *contents* through '%40' therefore refuses the second
        create - and reading that as "the encoded spelling does not work" gets
        it exactly backwards, since the refusal is the server saying the
        collection is already there.
        """
        checker = _make_checker()
        server = FakeCollectionServer(accepts={CAL_LITERAL}, aliased=True)
        server.install(checker)
        ## contents are served only through the spelling they were written to
        original = server.request
        server.request = lambda url, *a, **k: (
            original(url, *a, **k) if str(url).startswith(CAL_LITERAL) else Mock(status=404, raw="")
        )
        checker._client_obj.request.side_effect = server.request
        observation = _collection(checker)
        assert observation.encoded != "unsupported"
        assert observation.identity is None

    def test_a_server_that_takes_no_calendar_at_all_says_nothing(self) -> None:
        checker = _make_checker()
        FakeCollectionServer(accepts=set()).install(checker)
        observation = _collection(checker)
        assert (observation.literal, observation.encoded, observation.identity) == (None, None, None)
        assert observation.behaviour

    def test_a_5xx_on_the_first_mkcalendar_is_not_a_refusal(self) -> None:
        """These verdicts reach server profiles; a bad minute must not establish
        "this server will not take an '@' in a calendar path"."""
        checker = _make_checker()
        FakeCollectionServer(accepts={CAL_LITERAL, CAL_ENCODED}, mkcal_status={CAL_LITERAL: 503}).install(checker)
        observation = _collection(checker)
        assert (observation.literal, observation.encoded, observation.identity) == (None, None, None)

    def test_a_5xx_on_the_second_mkcalendar_is_not_a_refusal_either(self) -> None:
        checker = _make_checker()
        FakeCollectionServer(accepts={CAL_LITERAL}, aliased=False, mkcal_status={CAL_ENCODED: 500}).install(checker)
        observation = _collection(checker)
        assert observation.literal == "full"
        assert observation.encoded is None

    def test_a_403_on_the_encoded_spelling_is_still_a_refusal(self) -> None:
        """403 is raised by the client before a status exists; Robur answers 403
        where others answer 404, and that is a miss, not an unknown."""
        checker = _make_checker()
        FakeCollectionServer(
            accepts={CAL_LITERAL},
            aliased=False,
            raises={CAL_ENCODED: AuthorizationError("403")},
        ).install(checker)
        observation = _collection(checker)
        assert observation.literal == "full"
        assert observation.encoded == "unsupported"

    def test_the_probe_calendars_are_deleted_again(self) -> None:
        """Two calendars per run, one of them with an '@' in the name, would
        otherwise pile up in the user's account."""
        for aliased in (True, False):
            checker = _make_checker()
            server = FakeCollectionServer(accepts={CAL_LITERAL, CAL_ENCODED}, aliased=aliased).install(checker)
            _collection(checker)
            assert server.collections == {}, f"aliased={aliased}"

    def test_leftovers_are_cleared_before_probing(self) -> None:
        """A collection left at '%40' by an aborted run would otherwise be read
        as the encoded spelling resolving on its own - the collection-level
        version of the leftover bug on the object axis."""
        checker = _make_checker()
        server = FakeCollectionServer(accepts={CAL_LITERAL, CAL_ENCODED}, aliased=False)
        server.collections[CAL_ENCODED] = {OBJ: "left over from an aborted run"}
        server.install(checker)
        observation = _collection(checker)
        assert observation.identity == "full"
        first_write = next(i for i, c in enumerate(server.calls) if c[0] in ("MKCALENDAR", "MKCOL", "PUT"))
        assert ("DELETE", CAL_ENCODED) in server.calls[:first_write]

    def test_mkcol_is_used_where_the_server_requires_it(self) -> None:
        """Asking a MKCOL-only server with MKCALENDAR fails by construction, and
        would be recorded as "it will not take a calendar at that spelling"."""
        checker = _make_checker()
        checker._features_checked.set_feature("create-calendar", {"support": "quirk", "behaviour": "mkcol-required"})
        server = FakeCollectionServer(accepts={CAL_LITERAL, CAL_ENCODED}, aliased=False).install(checker)
        observation = _collection(checker)
        assert observation.identity == "full"
        assert not any(c[0] == "MKCALENDAR" for c in server.calls)

    def test_the_axis_declines_when_calendars_cannot_be_made_and_removed(self) -> None:
        """It would fail by construction on a server that cannot create
        calendars, and leak one it cannot delete on a server that cannot."""
        for feature in ("create-calendar", "delete-calendar"):
            checker = _make_checker()
            checker._features_checked.set_feature(feature, False)
            server = FakeCollectionServer(accepts={CAL_LITERAL, CAL_ENCODED}, aliased=False).install(checker)
            observation = _collection(checker)
            assert (observation.literal, observation.encoded, observation.identity) == (None, None, None)
            assert server.calls == [], feature

    def test_the_axis_never_raises(self) -> None:
        checker = _make_checker()
        checker._client_obj.mkcalendar.side_effect = RuntimeError("boom")
        checker._client_obj.delete.side_effect = RuntimeError("boom")
        observation = _collection(checker)
        assert observation is None or observation.identity is None


class TestThePrincipalAxis:
    def _run(self, checker):
        return PrepareCalendar(checker)._probe_encode_at_principal()

    def test_a_username_without_an_at_raises_no_question(self) -> None:
        checker = _make_checker(username="alice", principal_url="http://dav.example.com/p/alice/")
        assert self._run(checker) is None

    def test_a_path_that_does_not_carry_the_username_raises_no_question(self) -> None:
        """Plenty of servers address the principal by an opaque id, and then
        there is no '@' in a path to ask about."""
        checker = _make_checker(
            principal_url="http://dav.example.com/p/9f1c/",
            home_url="http://dav.example.com/cal/9f1c/",
        )
        assert self._run(checker) is None

    def test_an_at_in_the_authority_is_not_a_path_question(self) -> None:
        """That '@' is userinfo - a different production entirely, and rewriting
        it would change which credentials the request carries."""
        checker = _make_checker(
            principal_url="http://alice@dav.example.com/p/9f1c/",
            home_url="http://alice@dav.example.com/cal/9f1c/",
        )
        assert self._run(checker) is None

    def test_both_spellings_answering_is_recorded_without_an_identity_verdict(self) -> None:
        """Identity would take writing a second resource at the other spelling,
        which here means creating a second principal.  Not observable."""
        checker = _make_checker()
        checker._client_obj.propfind.side_effect = lambda url=None, props=None, depth=0: Mock(status=207)
        observation = self._run(checker)
        assert observation.literal == "full"
        assert observation.encoded == "full"
        assert observation.identity is None

    def test_only_the_encoded_spelling_answering(self) -> None:
        """The ownCloud complaint in its original form: the server hands out a
        home-set with a literal '@' and then will not serve it."""
        checker = _make_checker()

        def propfind(url=None, props=None, depth=0):
            return Mock(status=207 if str(url) == PRINCIPAL_ENCODED else 404)

        checker._client_obj.propfind.side_effect = propfind
        observation = self._run(checker)
        assert observation.literal == "unsupported"
        assert observation.encoded == "full"
        assert observation.identity is None

    def test_only_the_literal_spelling_answering(self) -> None:
        checker = _make_checker()

        def propfind(url=None, props=None, depth=0):
            return Mock(status=207 if str(url) == PRINCIPAL_LITERAL else 404)

        checker._client_obj.propfind.side_effect = propfind
        observation = self._run(checker)
        assert observation.literal == "full"
        assert observation.encoded == "unsupported"

    def test_an_encoded_home_set_is_probed_in_its_literal_spelling_too(self) -> None:
        """The server may hand the path out already encoded; the pair of URLs
        has to be built from whichever spelling it used."""
        checker = _make_checker(
            principal_url="http://dav.example.com/p/9f1c/",
            home_url=PRINCIPAL_ENCODED,
        )
        seen = []

        def propfind(url=None, props=None, depth=0):
            seen.append(str(url))
            return Mock(status=207)

        checker._client_obj.propfind.side_effect = propfind
        observation = self._run(checker)
        assert sorted(seen) == sorted([PRINCIPAL_LITERAL, PRINCIPAL_ENCODED])
        assert observation.literal == "full"

    def test_a_5xx_is_not_a_verdict(self) -> None:
        checker = _make_checker()
        checker._client_obj.propfind.side_effect = lambda url=None, props=None, depth=0: Mock(status=502)
        observation = self._run(checker)
        assert (observation.literal, observation.encoded, observation.identity) == (None, None, None)

    def test_a_404_on_both_spellings_is_not_a_verdict(self) -> None:
        """Whatever is wrong with that URL, it is not about the spelling."""
        checker = _make_checker()
        checker._client_obj.propfind.side_effect = NotFoundError("404")
        observation = self._run(checker)
        assert (observation.literal, observation.encoded, observation.identity) == (None, None, None)

    def test_the_axis_writes_nothing(self) -> None:
        """It probes the user's own principal, which is not ours to write to."""
        checker = _make_checker()
        checker._client_obj.propfind.side_effect = lambda url=None, props=None, depth=0: Mock(status=207)
        self._run(checker)
        for method in ("put", "delete", "mkcalendar", "mkcol"):
            assert not getattr(checker._client_obj, method).called, method


class TestTheAxesAreMerged:
    """Three axes, three subfeatures, and no axis silently winning."""

    def _record(self, *observations):
        checker = _make_checker()
        PrepareCalendar(checker)._record_encode_at(*observations)
        return checker

    def test_agreeing_axes_record_what_they_agree_on(self) -> None:
        checker = self._record(
            _object_axis("two resources", literal="full", encoded="full", identity="full"),
            _collection_axis("two collections", literal="full", encoded="full", identity="full"),
        )
        assert _support(checker, "literal.object") == "full"
        assert _support(checker, "literal.collection") == "full"
        assert _support(checker, "encoded") == "full"
        assert _support(checker, "identity") == "full"

    def test_disagreeing_axes_are_fragile_rather_than_one_of_them(self) -> None:
        """Still true of the two subfeatures that are still shared: the uid
        aliases the two spellings while the calendar path keeps them apart.
        The feature holds in one place and not in another, and "fragile" is the
        only honest single verdict."""
        checker = self._record(
            _object_axis("aliased", literal="full", encoded="full", identity="unsupported"),
            _collection_axis("two collections", literal="full", encoded="full", identity="full"),
        )
        assert _support(checker, "identity") == "fragile"
        ## the axes that did agree are unaffected
        assert _support(checker, "encoded") == "full"

    def test_a_disagreement_says_which_axis_saw_what(self) -> None:
        """ "fragile" on its own leaves a human no way to work around it."""
        checker = self._record(
            _object_axis("aliased", identity="unsupported"),
            _collection_axis("two collections", identity="full"),
        )
        behaviour = _behaviour(checker, "identity")
        assert "object paths" in behaviour and "calendar paths" in behaviour
        assert "full" in behaviour and "unsupported" in behaviour

    def test_a_disagreement_asks_to_be_reported(self, caplog) -> None:
        """A server differing between the axes is the whole case for splitting
        a subfeature into per-axis children - it is what split ``literal``.  A
        `fragile` buried in a profile would not reach anyone; a warning on the
        run that saw it might."""
        with caplog.at_level(logging.WARNING):
            self._record(
                _object_axis("aliased", identity="unsupported"),
                _collection_axis("two collections", identity="full"),
            )
        assert any("url.encode-at.identity" in r.getMessage() for r in caplog.records)

    def test_agreeing_axes_are_not_warned_about(self, caplog) -> None:
        """Otherwise the warning would be noise on every ordinary run."""
        with caplog.at_level(logging.WARNING):
            self._record(
                _object_axis("both spellings work", literal="full", encoded="full", identity="full"),
                _collection_axis("likewise", literal="full", encoded="full", identity="full"),
            )
        assert caplog.records == []

    def test_an_axis_that_observed_nothing_does_not_outvote_one_that_did(self) -> None:
        checker = self._record(
            _object_axis("two resources", literal="full", encoded="full", identity="full"),
            _collection_axis("the server took no calendar at either spelling"),
            _principal_axis("neither spelling answered"),
        )
        assert _support(checker, "identity") == "full"
        assert _support(checker, "encoded") == "full"

    def test_nothing_observed_at_all_is_unknown_not_a_guess(self) -> None:
        """Each subfeature has its own default and compare() skips an unknown,
        so "unknown" says "not observed" and claims nothing."""
        checker = self._record(_object_axis("nothing worked"), _collection_axis("nothing worked either"))
        for name in ("identity", "encoded", "literal.object", "literal.collection", "literal.principal"):
            assert _support(checker, name) == "unknown"

    def test_the_behaviour_of_every_axis_survives_into_the_profile(self) -> None:
        checker = self._record(
            _object_axis("the object story", identity="full"),
            _collection_axis("the calendar story", identity="full"),
            _principal_axis("the principal story", identity="full"),
        )
        behaviour = _behaviour(checker, "identity")
        for fragment in ("the object story", "the calendar story", "the principal story"):
            assert fragment in behaviour

    def test_a_missing_axis_is_skipped(self) -> None:
        """The collection and principal axes return None where the question does
        not arise; that must not become an entry in the merge."""
        checker = self._record(_object_axis("only the object axis ran", identity="full"), None, None)
        assert _support(checker, "identity") == "full"
        assert "None" not in _behaviour(checker, "identity")

    def test_the_merge_never_writes_an_unknown_feature_name(self) -> None:
        """A typo here reaches the user as a UserWarning from the library."""
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            self._record(
                _object_axis("a", literal="full", encoded="unsupported"),
                _collection_axis("b", literal="unsupported", encoded="full", identity="full"),
                _principal_axis("c", literal="full"),
            )

    def test_the_merged_verdict_is_one_the_library_reads(self) -> None:
        """Including "fragile", which the axes can now produce and the object
        axis never could."""
        from caldav.compatibility_hints import at_spelling_to_mint, at_spellings_are_aliased

        checker = self._record(
            _object_axis("a", literal="full", encoded="full", identity="full"),
            _collection_axis("b", literal="unsupported", encoded="full", identity="unsupported"),
        )
        fs = checker._features_checked
        assert _support(checker, "identity") == "fragile"
        assert at_spelling_to_mint(fs) in ("@", "%40")
        assert at_spellings_are_aliased(fs) in (True, False)


class TestTheWholeProbeStillHoldsTogether:
    def test_check_encode_at_runs_all_three_axes(self) -> None:
        """An axis that is written but never called records nothing at all."""
        checker = _make_checker()
        check = PrepareCalendar(checker)
        called = []
        check._probe_encode_at_object = lambda calendar: called.append("object")
        check._probe_encode_at_collection = lambda: called.append("collection")
        check._probe_encode_at_principal = lambda: called.append("principal")
        check._check_encode_at(Mock())
        assert called == ["object", "collection", "principal"]

    def test_the_whole_probe_records_every_subfeature(self) -> None:
        from caldav.lib.url import URL

        checker = _make_checker()
        FakeCollectionServer(accepts={CAL_LITERAL, CAL_ENCODED}, aliased=False).install(checker)
        calendar = Mock()
        calendar.url = URL.objectify(HOME + "testcal/")
        PrepareCalendar(checker)._check_encode_at(calendar)
        for name in ("identity", "encoded", "literal.object", "literal.collection", "literal.principal"):
            assert _support(checker, name) is not None

    def test_only_the_subfeature_a_server_differed_on_was_split(self) -> None:
        """The ruling stands for the two that are still shared: split them into
        per-axis children only once a server is actually seen to differ.  Only
        ``literal`` has been (Stalwart), so only ``literal`` has children."""
        assert {f for f in PrepareCalendar.features_to_be_checked if f.startswith("url.encode-at")} == {
            "url.encode-at.identity",
            "url.encode-at.literal.object",
            "url.encode-at.literal.collection",
            "url.encode-at.literal.principal",
            "url.encode-at.encoded",
        }


class TestLiteralIsRecordedPerAxis:
    """The one subfeature a real server was seen to answer differently per axis.

    Stalwart re-encodes an ``@`` in an object name - a PUT to the literal path
    is accepted and the resource is then reachable only under ``%40`` - while
    serving a calendar path under whichever spelling it was asked for.  Merged
    into one verdict that came out ``fragile``, which told the one consumer
    (the ownCloud home-set hack, which is about a principal path) nothing it
    could act on.
    """

    def _record(self, *observations):
        checker = _make_checker()
        PrepareCalendar(checker)._record_encode_at(*observations)
        return checker

    def test_each_axis_keeps_its_own_verdict(self) -> None:
        checker = self._record(
            _object_axis("reachable only under '%40'", literal="unsupported"),
            _collection_axis("both spellings answer", literal="full"),
            _principal_axis("both spellings answer", literal="full"),
        )
        assert _support(checker, "literal.object") == "unsupported"
        assert _support(checker, "literal.collection") == "full"
        assert _support(checker, "literal.principal") == "full"

    def test_disagreeing_axes_are_no_longer_fragile(self) -> None:
        """That was the whole point: nothing is merged, so nothing collides."""
        checker = self._record(
            _object_axis("reachable only under '%40'", literal="unsupported"),
            _collection_axis("both spellings answer", literal="full"),
        )
        for axis in ("object", "collection", "principal"):
            assert _support(checker, f"literal.{axis}") != "fragile"

    def test_a_disagreement_on_literal_is_not_warned_about_any_more(self, caplog) -> None:
        """The warning asks for evidence to split a subfeature.  This one is
        split, so the evidence has been acted on and the warning would be noise
        on every Stalwart run."""
        with caplog.at_level(logging.WARNING):
            self._record(
                _object_axis("reachable only under '%40'", literal="unsupported"),
                _collection_axis("both spellings answer", literal="full"),
            )
        assert not any("url.encode-at.literal" in r.getMessage() for r in caplog.records)

    def test_an_axis_that_did_not_grade_it_records_unknown(self) -> None:
        checker = self._record(_object_axis("nothing could be established"))
        assert _support(checker, "literal.object") == "unknown"

    def test_an_axis_that_never_ran_records_unknown_too(self) -> None:
        """The checker asserts that every declared feature was set, and a key
        left absent would read as "the default applies" rather than "nobody
        looked"."""
        checker = self._record(_object_axis("only this axis ran", literal="full"), None, None)
        assert _support(checker, "literal.collection") == "unknown"
        assert _support(checker, "literal.principal") == "unknown"

    def test_each_axis_carries_its_own_behaviour_text(self) -> None:
        checker = self._record(
            _object_axis("the object story", literal="unsupported"),
            _collection_axis("the calendar story", literal="full"),
        )
        assert _behaviour(checker, "literal.object") == "the object story"
        assert _behaviour(checker, "literal.collection") == "the calendar story"

    def test_the_home_set_hack_reads_the_principal_axis(self) -> None:
        """End to end into the library: the Stalwart shape must not switch on
        the ownCloud workaround, and a real ownCloud observation must."""
        from caldav.compatibility_hints import at_literal_is_refused

        stalwart = self._record(
            _object_axis("reachable only under '%40'", literal="unsupported"),
            _collection_axis("both spellings answer", literal="full"),
            _principal_axis("both spellings answer", literal="full"),
        )
        assert not at_literal_is_refused(stalwart._features_checked)

        owncloud = self._record(
            _object_axis("both spellings answer", literal="full"),
            _collection_axis("both spellings answer", literal="full"),
            _principal_axis("the home-set is served only as '%40'", literal="unsupported"),
        )
        assert at_literal_is_refused(owncloud._features_checked)
