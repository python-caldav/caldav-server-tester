"""Unit tests for the url.encode-at probe in PrepareCalendar.

The probe answers three questions, one per subfeature: does a literal ``@``
resolve (``url.encode-at.literal``), does ``%40`` resolve
(``url.encode-at.encoded``), and - only observable when both do - are the two
spellings two resources (``url.encode-at.identity``).  Its whole value rests on
knowing which spelling the resource actually exists under, so a good deal of
this is about the PUTs that establish it, not only the GETs.

A subfeature this probe did not observe must be left undeclared rather than
filled in from a sibling: each has its own default, and a guess here reaches
every profile the checker writes.
"""

from unittest.mock import Mock

from caldav.compatibility_hints import FeatureSet
from caldav.lib.error import AuthorizationError

from caldav_server_tester.checks import ENCODE_AT_ALIAS_UID, ENCODE_AT_UID, PrepareCalendar

CAL = "http://dav.example.com/cal/"
LITERAL = CAL + ENCODE_AT_UID + ".ics"
## spelled out rather than taken from the library's _quote_uid(): that now
## returns the literal "@" by default, and a probe that derived its "other
## spelling" from the code under test would compare a spelling with itself
ENCODED = CAL + ENCODE_AT_UID.replace("@", "%40") + ".ics"


def _make_checker() -> Mock:
    checker = Mock()
    checker._features_checked = FeatureSet()
    checker.features_checked = checker._features_checked
    checker.debug_mode = None
    checker.fixture_base_year = 2026
    checker._client_obj = Mock()
    checker._client_obj.features = FeatureSet()
    checker.expected_features = FeatureSet()
    return checker


def _make_calendar() -> Mock:
    from caldav.lib.url import URL

    calendar = Mock()
    calendar.url = URL.objectify(CAL)
    return calendar


class FakeServer:
    """A server holding at most one probe object per URL spelling.

    ``aliased`` makes the two spellings the same storage slot, which is the
    whole point of the identity axis.  ``accepts`` names the spellings that
    take a PUT at all; ``serves`` those that answer a GET (defaults to
    ``accepts``, but they differ on servers that store under one spelling and
    serve under another).
    """

    def __init__(self, accepts, serves=None, aliased=True, get_raises=None, put_status=None, get_status=None):
        self.accepts = set(accepts)
        self.serves = set(accepts if serves is None else serves)
        self.aliased = aliased
        self.get_raises = get_raises or {}
        ## per-URL status overrides, for the "the server had a bad minute" cases
        self.put_status = put_status or {}
        self.get_status = get_status or {}
        self.store: dict[str, str] = {}
        self.calls: list[tuple[str, str]] = []

    def _slot(self, url):
        return "shared" if self.aliased else url

    def install(self, checker):
        checker._client_obj.put.side_effect = self.put
        checker._client_obj.request.side_effect = self.request
        checker._client_obj.delete.side_effect = self.delete
        return self

    def put(self, url, body, headers=None):
        url = str(url)
        self.calls.append(("PUT", url))
        r = Mock()
        if url in self.put_status:
            r.status = self.put_status[url]
            return r
        if url not in self.accepts:
            r.status = 403
            return r
        uid = [line for line in body.splitlines() if line.startswith("UID:")][0][4:]
        self.store[self._slot(url)] = uid
        r.status = 201
        return r

    def request(self, url, *args, **kwargs):
        url = str(url)
        self.calls.append(("GET", url))
        if url in self.get_raises:
            raise self.get_raises[url]
        r = Mock()
        if url in self.get_status:
            r.status = self.get_status[url]
            r.raw = "<html>server error</html>"
            return r
        uid = self.store.get(self._slot(url)) if url in self.serves else None
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
        self.store.pop(self._slot(url), None)
        return Mock(status=204)


def _every_server_shape():
    """One FakeServer per behaviour the probe is built to tell apart."""
    return [
        ## conformant: two spellings, two resources
        FakeServer(accepts={LITERAL, ENCODED}, aliased=False),
        ## lenient: two spellings, one resource
        FakeServer(accepts={LITERAL, ENCODED}, aliased=True),
        ## the ownCloud shape: will not store under the literal spelling
        FakeServer(accepts={ENCODED}),
        ## the mirror image: will not store under the encoded spelling
        FakeServer(accepts={LITERAL}),
        ## stores under either, serves neither back (a write delay looks like this)
        FakeServer(accepts={LITERAL, ENCODED}, serves=set()),
        ## takes nothing at all
        FakeServer(accepts=set()),
        ## a bad minute on each of the two writes, and on a read
        FakeServer(accepts={LITERAL, ENCODED}, put_status={LITERAL: 503}),
        FakeServer(accepts={LITERAL, ENCODED}, put_status={ENCODED: 500}),
        FakeServer(accepts={LITERAL, ENCODED}, get_status={ENCODED: 502}),
        ## an authorization error rather than a status
        FakeServer(accepts={LITERAL, ENCODED}, get_raises={ENCODED: AuthorizationError("403")}),
    ]


def _run(checker) -> None:
    """Record the object axis alone.

    ``_check_encode_at`` merges three axes; everything below is about what the
    object axis observes, so it is run and recorded on its own.  The merge
    itself, and the other two axes, are covered further down.
    """
    check = PrepareCalendar(checker)
    check._record_encode_at(check._probe_encode_at_object(_make_calendar()))


## ``url.encode-at.literal`` is recorded per axis (Stalwart answers it one way
## for an object name and another for a calendar path).  Everything in this
## file drives the object axis, so "literal" here is that axis's key.
_SUBFEATURE_KEYS = {"literal": "literal.object"}


def _support(checker, name) -> str | None:
    """The support level recorded for one subfeature, or None if never set."""
    key = _SUBFEATURE_KEYS.get(name, name)
    node = checker._features_checked.is_supported(f"url.encode-at.{key}", dict, return_defaults=False)
    return node.get("support") if isinstance(node, dict) else None


def _nothing_observed(checker) -> bool:
    """All three recorded, none of them decided - "unknown", not a guess."""
    return all(_support(checker, n) == "unknown" for n in ("identity", "literal", "encoded"))


class TestSpellingsActuallyDiffer:
    def test_quoted_and_unquoted_spellings_differ(self) -> None:
        """Guards the assumption the whole probe rests on."""
        assert LITERAL != ENCODED
        assert "%40" in ENCODED and "@" not in ENCODED.rsplit("/", 1)[-1]

    def test_the_two_probe_uids_are_not_substrings_of_each_other(self) -> None:
        """``_resolved_uid`` tells them apart by substring match on the body."""
        assert ENCODE_AT_UID not in ENCODE_AT_ALIAS_UID
        assert ENCODE_AT_ALIAS_UID not in ENCODE_AT_UID


class TestReachabilityAxis:
    def test_probe_puts_to_the_literal_spelling_first(self) -> None:
        """The probe establishes the resource under the literal spelling itself
        rather than through save_object(), so it does not depend on which
        spelling the library happens to mint - which is exactly the thing under
        test, and which has changed once already."""
        checker = _make_checker()
        server = FakeServer(accepts={LITERAL}).install(checker)
        _run(checker)
        puts = [c for c in server.calls if c[0] == "PUT"]
        assert puts[0] == ("PUT", LITERAL)

    def test_only_the_literal_spelling_resolves(self) -> None:
        checker = _make_checker()
        FakeServer(accepts={LITERAL}, serves={LITERAL}, aliased=False).install(checker)
        _run(checker)
        assert _support(checker, "literal") == "full"
        assert _support(checker, "encoded") == "unsupported"
        ## with only one spelling resolving there is no second resource to
        ## compare against, so identity is not observable here
        assert _support(checker, "identity") == "unknown"

    def test_literal_put_refused_encoded_accepted(self) -> None:
        """The server would not create a literal-@ path - the client must encode."""
        checker = _make_checker()
        FakeServer(accepts={ENCODED}, aliased=False).install(checker)
        _run(checker)
        assert _support(checker, "literal") == "unsupported"
        assert _support(checker, "encoded") == "full"

    def test_stored_under_the_literal_spelling_served_only_under_the_encoded_one(self) -> None:
        checker = _make_checker()
        FakeServer(accepts={LITERAL, ENCODED}, serves={ENCODED}).install(checker)
        _run(checker)
        assert _support(checker, "literal") == "unsupported"
        assert _support(checker, "encoded") == "full"

    def test_both_puts_refused_is_unknown(self) -> None:
        """Nothing was established, so nothing can be concluded."""
        checker = _make_checker()
        FakeServer(accepts=set()).install(checker)
        _run(checker)
        assert _nothing_observed(checker)

    def test_stored_but_served_by_neither_spelling_is_unknown(self) -> None:
        """A write delay looks exactly like this; it is not evidence that an
        '@' path is unusable, it is simply not an answer."""
        checker = _make_checker()
        FakeServer(accepts={LITERAL}, serves=set()).install(checker)
        _run(checker)
        assert _nothing_observed(checker)


class TestIdentityAxis:
    def test_aliased_server(self) -> None:
        checker = _make_checker()
        FakeServer(accepts={LITERAL, ENCODED}, aliased=True).install(checker)
        _run(checker)
        assert _support(checker, "literal") == "full"
        assert _support(checker, "encoded") == "full"
        ## aliased: lenient, so "identity" is the thing not supported
        assert _support(checker, "identity") == "unsupported"

    def test_distinct_server(self) -> None:
        """Both spellings resolve, but writing to one does not change the other."""
        checker = _make_checker()
        server = FakeServer(accepts={LITERAL, ENCODED}, aliased=False).install(checker)
        ## make the encoded spelling resolve before the identity probe runs
        server.store[ENCODED] = ENCODE_AT_UID
        _run(checker)
        assert _support(checker, "literal") == "full"
        assert _support(checker, "encoded") == "full"
        ## distinct is what RFC3986 asks for, hence "full"
        assert _support(checker, "identity") == "full"

    def test_the_alias_object_is_deleted_again_on_a_distinct_server(self) -> None:
        """A distinct-resource server would otherwise be left with two objects."""
        checker = _make_checker()
        server = FakeServer(accepts={LITERAL, ENCODED}, aliased=False).install(checker)
        server.store[ENCODED] = ENCODE_AT_UID
        _run(checker)
        assert ("DELETE", ENCODED) in server.calls
        assert ENCODED not in server.store

    def test_the_fixture_survives_an_aliased_server(self) -> None:
        """On an aliased server the second PUT overwrites the fixture itself,
        so the probe has to put the original content back."""
        checker = _make_checker()
        server = FakeServer(accepts={LITERAL, ENCODED}, aliased=True).install(checker)
        _run(checker)
        assert server.store["shared"] == ENCODE_AT_UID
        ## the leading DELETEs are the leftover sweep, before anything is written;
        ## what must not happen is a DELETE once the alias turned out to *be* the
        ## fixture, which would take the fixture with it
        after_first_put = server.calls[next(i for i, c in enumerate(server.calls) if c[0] == "PUT") :]
        assert ("DELETE", ENCODED) not in after_first_put

    def test_a_refused_second_put_leaves_the_identity_unknown(self) -> None:
        """Not guessed at: a server enforcing UID uniqueness, or refusing the
        write for any other reason, tells us nothing about identity."""
        checker = _make_checker()

        ## an aliasing server: after the first PUT both spellings serve the
        ## object, so "encoded resolves" is established before the second write
        server = FakeServer(accepts={LITERAL, ENCODED}, aliased=True)
        server.install(checker)
        original_put = server.put
        seen = []

        def put(url, body, headers=None):
            seen.append(str(url))
            if ENCODE_AT_ALIAS_UID in body:
                return Mock(status=409)
            return original_put(url, body, headers)

        checker._client_obj.put.side_effect = put
        _run(checker)
        assert _support(checker, "literal") == "full"
        assert _support(checker, "encoded") == "full"
        assert _support(checker, "identity") == "unknown"


class TestResolutionIsJudgedHonestly:
    def test_200_without_a_probe_uid_in_the_body_is_not_a_hit(self) -> None:
        """Servers that answer 200 for any child path exist - this suite probes
        for them under non-existing-raises-not-found.  A bare 200 proves nothing."""
        checker = _make_checker()

        def request(url, *args, **kwargs):
            r = Mock()
            r.status = 200
            r.raw = "BEGIN:VCALENDAR\r\nUID:something_else\r\nEND:VCALENDAR"
            return r

        def put(url, body, headers=None):
            return Mock(status=201 if str(url) == LITERAL else 403)

        checker._client_obj.put.side_effect = put
        checker._client_obj.request.side_effect = request
        _run(checker)
        assert _nothing_observed(checker)

    def test_authorization_error_counts_as_not_resolving(self) -> None:
        """401/403 raise before a response exists, so status<400 never sees them.
        A 403 on the encoded form means it did not resolve, not 'unknowable'."""
        checker = _make_checker()
        FakeServer(
            accepts={LITERAL},
            aliased=False,
            get_raises={ENCODED: AuthorizationError()},
        ).install(checker)
        _run(checker)
        assert _support(checker, "literal") == "full"
        assert _support(checker, "encoded") == "unsupported"

    def test_transport_error_is_unknown_not_a_verdict(self) -> None:
        """A dropped connection says nothing about encoding."""
        checker = _make_checker()
        FakeServer(
            accepts={LITERAL},
            aliased=False,
            get_raises={ENCODED: OSError("connection reset")},
        ).install(checker)
        _run(checker)
        assert _nothing_observed(checker)


class TestTheVerdictIsConsumable:
    """Every value the probe can emit has to mean something to the library."""

    def test_every_verdict_the_probe_can_emit_reaches_the_library(self) -> None:
        from caldav.compatibility_hints import at_spelling_to_mint, at_spellings_are_aliased

        ## Note both columns.  The library mints "%40" - what it has always
        ## sent - unless the server is declared not to resolve it; and its 3.x
        ## default for url.encode-at.identity is "unsupported", so "aliased" is
        ## what an undeclared server gets and conformance is the opt-in.
        for declared, mint, aliased in [
            ({}, "%40", True),
            ({"literal": "full", "encoded": "unsupported"}, "@", True),
            ({"literal": "unsupported", "encoded": "full"}, "%40", True),
            ({"literal": "full", "encoded": "full", "identity": "full"}, "%40", False),
            ({"literal": "full", "encoded": "full", "identity": "unsupported"}, "%40", True),
        ]:
            fs = FeatureSet()
            for name, support in declared.items():
                fs.set_feature(f"url.encode-at.{name}", {"support": support})
            assert at_spelling_to_mint(fs) == mint, declared
            assert at_spellings_are_aliased(fs) is aliased, declared

    def test_the_probe_never_writes_an_unknown_feature_name(self) -> None:
        """A typo here reaches the user as a UserWarning from the library.

        Every server shape the probe can meet, not just the happy one: the
        feature name is written at eight separate call sites and a typo in any
        of the other seven would go unnoticed by a single-shape test.
        """
        import warnings

        for server in _every_server_shape():
            checker = _make_checker()
            server.install(checker)
            with warnings.catch_warnings():
                warnings.simplefilter("error")
                _run(checker)

    def test_every_verdict_the_probe_actually_emits_is_one_the_library_reads(self) -> None:
        """Ties the table above to the probe rather than to my typing.

        The table is hand-written, so on its own it would still pass if the
        probe stopped emitting one of those combinations - or started emitting
        one that is not in it.  This drives the probe over every server shape
        and asserts the library can read back whatever came out.
        """
        from caldav.compatibility_hints import at_spelling_to_mint, at_spellings_are_aliased

        emitted = set()
        for server in _every_server_shape():
            checker = _make_checker()
            server.install(checker)
            _run(checker)
            fs = checker._features_checked
            emitted.add(tuple(_support(checker, n) for n in ("literal", "encoded", "identity")))
            assert at_spelling_to_mint(fs) in ("@", "%40")
            assert at_spellings_are_aliased(fs) in (True, False)
        ## and the shapes really do produce different verdicts, so this is not
        ## one outcome exercised nine times
        assert len(emitted) >= 4, emitted


class TestProbeIsGraceful:
    def test_probe_never_raises(self) -> None:
        """PrepareCalendar provisions the fixtures every later check needs; this
        probe must never be the thing that aborts the run."""
        checker = _make_checker()
        checker._client_obj.put.side_effect = RuntimeError("boom")
        _run(checker)
        assert _nothing_observed(checker)

    def test_cleanup_eligible_uids(self) -> None:
        """checker.cleanup() sweeps by the csc_ prefix."""
        for uid in (ENCODE_AT_UID, ENCODE_AT_ALIAS_UID):
            assert uid.startswith("csc_")
            assert "@" in uid

    def test_a_failing_delete_is_a_warning_not_a_failure(self) -> None:
        checker = _make_checker()
        server = FakeServer(accepts={LITERAL, ENCODED}, aliased=False)
        server.store[ENCODED] = ENCODE_AT_UID
        server.install(checker)
        checker._client_obj.delete.side_effect = RuntimeError("no")
        _run(checker)
        assert _support(checker, "identity") == "full"

    def test_every_subfeature_is_declared_as_checked(self) -> None:
        for name in ("identity", "literal.object", "literal.collection", "literal.principal", "encoded"):
            assert f"url.encode-at.{name}" in PrepareCalendar.features_to_be_checked


class TestABadMinuteIsNotAVerdict:
    """A 5xx says the server broke, not that it rejects a spelling.

    These verdicts are copied into the caldav library's server profiles, where
    "the server refuses a literal '@' in a path" makes the client rewrite every
    such URL.  A 503 during the probe must not be able to establish that.
    """

    def test_a_5xx_on_the_literal_put_is_not_a_refusal(self) -> None:
        checker = _make_checker()
        FakeServer(accepts={LITERAL, ENCODED}, put_status={LITERAL: 503}).install(checker)
        _run(checker)
        assert _support(checker, "literal") != "unsupported"
        assert _nothing_observed(checker)

    def test_a_5xx_on_both_puts_is_not_a_refusal_either(self) -> None:
        checker = _make_checker()
        FakeServer(
            accepts={LITERAL, ENCODED},
            put_status={LITERAL: 500, ENCODED: 500},
        ).install(checker)
        _run(checker)
        assert _nothing_observed(checker)

    def test_a_4xx_on_the_literal_put_is_still_a_refusal(self) -> None:
        """The ownCloud shape must keep working - only 5xx is excused."""
        checker = _make_checker()
        FakeServer(accepts={ENCODED}).install(checker)
        _run(checker)
        assert _support(checker, "literal") == "unsupported"
        assert _support(checker, "encoded") == "full"

    def test_a_5xx_on_a_get_is_not_a_miss(self) -> None:
        checker = _make_checker()
        FakeServer(accepts={LITERAL, ENCODED}, get_status={ENCODED: 502}).install(checker)
        _run(checker)
        assert _support(checker, "encoded") != "unsupported"
        assert _nothing_observed(checker)

    def test_a_4xx_refusal_of_the_encoded_write_is_still_evidence(self) -> None:
        """The counterpart to the 5xx cases: a refusal is a fact about the
        server and must keep producing a verdict."""
        checker = _make_checker()
        FakeServer(accepts={LITERAL}, serves={LITERAL}).install(checker)
        _run(checker)
        assert _support(checker, "literal") == "full"
        assert _support(checker, "encoded") == "unsupported"


class TestADistinctServerCanActuallyBeObserved:
    """The conformant server must be *reachable*, not merely the default.

    RFC3986 makes '@' reserved, so two spellings are two resources - and a
    server that gets that right was, until this was fixed, indistinguishable
    from one that simply never serves '%40': the probe wrote a single object at
    the literal spelling, found nothing at the encoded one, and stopped.  The
    profile then fell back to the library default, which says the two spellings
    are one resource.  On the one class of server where that is wrong, the
    client was being told to rewrite one spelling into the other.
    """

    def test_a_distinct_server_is_observed_not_defaulted(self) -> None:
        checker = _make_checker()
        server = FakeServer(accepts={LITERAL, ENCODED}, aliased=False).install(checker)
        _run(checker)
        assert _support(checker, "identity") == "full"
        assert _support(checker, "literal") == "full"
        assert _support(checker, "encoded") == "full"
        ## and the store was never pre-seeded: the probe reached it on its own
        assert ("PUT", ENCODED) in server.calls

    def test_the_alias_object_is_cleaned_up_on_a_distinct_server(self) -> None:
        checker = _make_checker()
        server = FakeServer(accepts={LITERAL, ENCODED}, aliased=False).install(checker)
        _run(checker)
        assert ENCODE_AT_ALIAS_UID not in server.store.values()
        assert server.store.get(LITERAL) == ENCODE_AT_UID, "the fixture object was not left in place"

    def test_an_aliasing_server_is_still_reported_as_aliasing(self) -> None:
        checker = _make_checker()
        FakeServer(accepts={LITERAL, ENCODED}, aliased=True).install(checker)
        _run(checker)
        assert _support(checker, "identity") == "unsupported"

    def test_a_server_that_refuses_the_encoded_put_says_so(self) -> None:
        """No second resource can be written, so identity stays unobserved -
        but that is now recorded as such rather than as "they are the same"."""
        checker = _make_checker()
        FakeServer(accepts={LITERAL}, serves={LITERAL}).install(checker)
        _run(checker)
        assert _support(checker, "literal") == "full"
        assert _support(checker, "encoded") == "unsupported"
        assert _support(checker, "identity") == "unknown"


class TestALeftoverDoesNotFlipTheVerdict:
    """H2: an alias object surviving from an earlier run used to be read as
    "'%40' does not reach an object stored under a literal '@'" - which is
    false, since '%40' plainly reached an object."""

    def test_a_leftover_alias_object_is_cleared_before_probing(self) -> None:
        checker = _make_checker()
        server = FakeServer(accepts={LITERAL, ENCODED}, aliased=False).install(checker)
        ## an aborted earlier run left its second probe object behind
        server.store[ENCODED] = ENCODE_AT_ALIAS_UID
        _run(checker)
        assert _support(checker, "identity") == "full"
        assert _support(checker, "encoded") == "full"
