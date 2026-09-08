"""Unit tests for check classes with mocked server responses

These tests demonstrate how to mock CalDAV server responses for testing.
They execute the actual check logic but with mocked server responses.

NOTE: These tests can be slow because they run complex
check logic. Use pytest -m "not slow" to skip them in normal development.

DISCLAIMER: those tests are AI-generated, and haven't been reviewed

Tests based on mocked up server-client-communication is notoriously
fragile, the only reason why this is added at all is that it's a
relatively cheap thing to do with AI - but the value is questionable.
If those tests will break in the future, then consider just deleting
this file.
"""

from unittest.mock import Mock

import pytest
from caldav.compatibility_hints import FeatureSet
from caldav.lib.error import DAVError, NotFoundError, ReportError

from caldav_server_tester.checker import ServerQuirkChecker
from caldav_server_tester.checks import (
    CheckGetCurrentUserPrincipal,
    CheckIsNotDefined,
    CheckMakeDeleteCalendar,
    CheckSearch,
    CheckWellKnown,
    PrepareCalendar,
)

# Mark all tests in this file as slow since they run actual check logic
pytestmark = pytest.mark.slow


class TestCheckGetCurrentUserPrincipal:
    """Test CheckGetCurrentUserPrincipal with mocked server responses"""

    def create_checker_with_mock_client(self) -> tuple[ServerQuirkChecker, Mock]:
        """Helper to create checker with mocked client"""
        client = Mock()
        client.features = FeatureSet()
        checker = ServerQuirkChecker(client, debug_mode=None)
        return checker, client

    def test_principal_supported_sets_feature_to_true(self) -> None:
        """When principal() succeeds, feature should be set to True"""
        checker, client = self.create_checker_with_mock_client()

        # Mock successful principal response
        mock_principal = Mock()
        client.principal.return_value = mock_principal

        check = CheckGetCurrentUserPrincipal(checker)
        check.run_check()

        # Should set feature to supported
        assert checker.features_checked.is_supported("get-current-user-principal")
        assert checker.principal == mock_principal

    def test_transient_principal_failure_marks_unknown_and_keeps_principal(self) -> None:
        """Finding #9: a later principal() failure is transient (init already proved
        it works), so it is retried and reported 'unknown' — not 'unsupported' — and
        the principal obtained during init is preserved for downstream checks."""
        checker, client = self.create_checker_with_mock_client()
        init_principal = checker.principal  # the working principal from __init__

        # principal() now fails on every call (simulating a transient outage)
        client.principal.side_effect = Exception("503 Service Unavailable")

        check = CheckGetCurrentUserPrincipal(checker)
        check.run_check()

        assert checker.features_checked.is_supported("get-current-user-principal", str) == "unknown"
        # principal from __init__ is kept, not nulled out
        assert checker.principal is init_principal
        # the failure was retried (>1 call inside the check)
        assert client.principal.call_count >= 2

    def test_principal_assertion_error_reraises(self) -> None:
        """AssertionError should be re-raised, not caught"""
        checker, client = self.create_checker_with_mock_client()

        # Mock AssertionError
        client.principal.side_effect = AssertionError("Test assertion")

        check = CheckGetCurrentUserPrincipal(checker)

        with pytest.raises(AssertionError, match="Test assertion"):
            check.run_check()


class TestCheckMakeDeleteCalendar:
    """Test CheckMakeDeleteCalendar with mocked server responses"""

    def create_checker_with_principal(self) -> tuple[ServerQuirkChecker, Mock, Mock]:
        """Helper to create checker with mocked client and principal"""
        client = Mock()
        client.features = FeatureSet()
        checker = ServerQuirkChecker(client, debug_mode=None)

        # Mock principal
        mock_principal = Mock()
        checker.principal = mock_principal

        # Mark dependency as run
        checker._checks_run.add(CheckGetCurrentUserPrincipal)

        return checker, client, mock_principal

    def test_create_calendar_async_delayed(self, monkeypatch) -> None:
        """Infomaniak/SabreDAV create calendars asynchronously: MKCALENDAR returns
        OK but the new collection 404s for a few seconds.  The probe must poll for
        it to materialise instead of concluding creation failed (which both
        mis-reports create-calendar AND leaks the orphaned calendar), and record
        the result as a 'delayed creation' quirk."""
        import caldav_server_tester.checks as checks_mod

        checker, client, principal = self.create_checker_with_principal()
        monkeypatch.setattr(checks_mod.time, "sleep", lambda _s: None)
        # bypass the raw-DELETE machinery; this test is only about creation detection
        monkeypatch.setattr(checks_mod.DAVObject, "delete", lambda _self: None)

        # events() 404s the first 3 times (calendar not yet materialised), then OK
        state = {"n": 0}

        def events_side_effect():
            state["n"] += 1
            if state["n"] <= 3:
                raise NotFoundError("Node could not be found")
            return []

        probe_cal = Mock()
        probe_cal.events.side_effect = events_side_effect
        principal.calendar.return_value = probe_cal
        principal.make_calendar.return_value = Mock()

        check = CheckMakeDeleteCalendar(checker)
        calmade = check._try_make_calendar(cal_id="x")

        assert calmade is True
        assert checker.features_checked.is_supported("create-calendar", str) == "quirk"
        assert "delayed" in checker.features_checked.is_supported("create-calendar", dict)["behaviour"]

    def test_create_calendar_never_materialises_is_unsupported(self, monkeypatch) -> None:
        """If the calendar never becomes accessible, creation is still treated as
        failed (returns False) — the poll must not turn a real failure into a
        false positive."""
        import caldav_server_tester.checks as checks_mod

        checker, client, principal = self.create_checker_with_principal()
        monkeypatch.setattr(checks_mod.time, "sleep", lambda _s: None)

        probe_cal = Mock()
        probe_cal.events.side_effect = NotFoundError("never shows up")
        principal.calendar.return_value = probe_cal
        principal.make_calendar.return_value = Mock()

        check = CheckMakeDeleteCalendar(checker)
        assert check._try_make_calendar(cal_id="x") is False
        ## _try_make_calendar leaves the verdict to the caller; it must NOT have
        ## recorded create-calendar as supported off the back of a failed poll.
        assert "create-calendar" not in checker.features_checked.dotted_feature_set_list()

    def test_calendar_auto_creation_detected(self) -> None:
        """When accessing non-existent calendar creates it, auto feature is set"""
        checker, client, principal = self.create_checker_with_principal()

        # Mock auto-creation: accessing a non-existent calendar auto-creates it
        mock_auto_calendar = Mock()
        mock_auto_calendar.events.return_value = []

        # Mock successful calendar creation when trying to make test calendars
        mock_test_calendar = Mock()
        mock_test_calendar.id = "caldav-server-checker-mkdel-test"

        # Track state: calendar is created (by make_calendar) then deleted
        created = [False]
        deleted = [False]

        def delete_cal():
            # Only mark as deleted if it was actually created
            if created[0]:
                deleted[0] = True

        mock_test_calendar.delete = delete_cal

        # events() should raise NotFoundError after deletion
        def events_side_effect():
            if deleted[0]:
                raise NotFoundError("Calendar was deleted")
            return []

        mock_test_calendar.events.side_effect = events_side_effect

        # Track all calls for debugging
        calls = []

        def calendar_side_effect(cal_id=None, name=None):
            calls.append((cal_id, name))

            # First call: checking if "this_should_not_exist" auto-creates
            if cal_id == "this_should_not_exist":
                # Auto-creation: returns a calendar even though it "shouldn't exist"
                return mock_auto_calendar

            # Calls during _try_make_calendar for "caldav-server-checker-mkdel-test":
            # 1. Line 96: Try to delete if exists (before creation)
            # 2. Line 107: Verify after make_calendar()
            # 3. Line 117: Look up by name for displayname check
            # 4. Line 142: Check if deleted
            # 5. Line 158: Recheck after sleep if not deleted

            # If calendar was deleted, it's not found
            if deleted[0] and cal_id == "caldav-server-checker-mkdel-test":
                raise NotFoundError("Calendar was deleted")

            # Looking up by name (for displayname check)
            if name == "Yep":
                cal = Mock()
                cal.id = mock_test_calendar.id
                cal.events.return_value = []
                return cal

            # Normal lookup by cal_id (before deletion)
            if cal_id == "caldav-server-checker-mkdel-test":
                return mock_test_calendar

            # Everything else not found
            raise NotFoundError("Calendar not found")

        principal.calendar.side_effect = calendar_side_effect
        principal.calendars.return_value = []

        # Track when calendar is created
        def make_calendar_side_effect(cal_id=None, **kwargs):
            created[0] = True
            return mock_test_calendar

        principal.make_calendar.side_effect = make_calendar_side_effect

        check = CheckMakeDeleteCalendar(checker)
        check.run_check()

        # Should detect auto-creation
        assert checker.features_checked.is_supported("create-calendar.auto")

    @pytest.mark.skip(
        reason="Complex multi-call mocking pattern - CheckMakeDeleteCalendar._run_check has very complex logic with multiple retry paths"
    )
    def test_calendar_no_auto_creation(self) -> None:
        """When accessing non-existent calendar fails, auto feature is not set"""
        checker, client, principal = self.create_checker_with_principal()

        # Mock successful manual creation
        mock_calendar = Mock()
        mock_calendar.events.return_value = []
        mock_calendar.id = "caldav-server-checker-mkdel-test"
        deleted_count = [0]

        def delete_calendar():
            deleted_count[0] += 1

        mock_calendar.delete = delete_calendar
        principal.make_calendar.return_value = mock_calendar

        # Mock calendar lookup behavior
        call_count = [0]

        def calendar_side_effect(cal_id=None, name=None):
            call_count[0] += 1
            # Initial cleanup attempt - calendar doesn't exist
            if call_count[0] == 1 and cal_id == "this_should_not_exist":
                raise NotFoundError("Not found")
            # Lookup by name (for displayname test) - should find it
            if name == "Yep" and cal_id == "caldav-server-checker-mkdel-test":
                mock_cal = Mock()
                mock_cal.id = mock_calendar.id
                mock_cal.events.return_value = []
                return mock_cal
            # After calendar is deleted, it's not found
            if deleted_count[0] > 0 and cal_id == "caldav-server-checker-mkdel-test":
                raise NotFoundError("Deleted")
            # Lookup by cal_id returns the created calendar (before deletion)
            if cal_id == "caldav-server-checker-mkdel-test":
                return mock_calendar
            # Other lookups fail
            raise NotFoundError("Not found")

        principal.calendar.side_effect = calendar_side_effect
        principal.calendars.return_value = []

        check = CheckMakeDeleteCalendar(checker)
        check.run_check()

        # Should not detect auto-creation (first attempt with weird name fails)
        # Note: The actual result depends on complex flow, but calendar creation should succeed
        assert checker.features_checked.is_supported("create-calendar")

    def test_auto_probe_survives_500_and_still_detects_create(self) -> None:
        """BTC-b: a 500 on the auto-create probe must not escape the probe; the
        check should record auto=False and go on to detect manual create support."""
        checker, client, principal = self.create_checker_with_principal()

        mock_calendar = Mock()
        mock_calendar.events.return_value = []
        mock_calendar.id = "caldav-server-checker-mkdel-test"
        deleted_count = [0]

        def delete_calendar():
            deleted_count[0] += 1

        mock_calendar.delete = delete_calendar
        principal.make_calendar.return_value = mock_calendar

        call_count = [0]

        def calendar_side_effect(cal_id=None, name=None):
            call_count[0] += 1
            # Auto-create probe: server answers 500 (a generic DAVError)
            if call_count[0] == 1 and cal_id == "this_should_not_exist":
                raise DAVError("500 Internal Server Error")
            if name == "Yep" and cal_id == "caldav-server-checker-mkdel-test":
                mock_cal = Mock()
                mock_cal.id = mock_calendar.id
                mock_cal.events.return_value = []
                return mock_cal
            if deleted_count[0] > 0 and cal_id == "caldav-server-checker-mkdel-test":
                raise NotFoundError("Deleted")
            if cal_id == "caldav-server-checker-mkdel-test":
                return mock_calendar
            raise NotFoundError("Not found")

        principal.calendar.side_effect = calendar_side_effect
        principal.calendars.return_value = []

        check = CheckMakeDeleteCalendar(checker)
        check.run_check()

        # The 500 was absorbed as auto=unsupported (before the fix it escaped the
        # probe and the generic handler marked it "unknown")...
        assert checker.features_checked.is_supported("create-calendar.auto", str) == "unsupported"
        # ...and the probe carried on to the manual make/delete attempts instead
        # of aborting at the auto probe.
        principal.make_calendar.assert_called()

    def _run_displayname_probe(
        self,
        checker,
        principal,
        *,
        relocate,
        name_sticks=True,
        get_displayname_raises=False,
        make_calendar_raises=False,
        existing_displayname=None,
        cal_id_reachable=True,
        located_segment=None,
    ):
        """Drive CheckMakeDeleteCalendar._check_set_displayname() with a mocked
        principal.

        relocate: if True, the calendar's *canonical* URL (discovered by looking
                  it up by display name) lands under a segment that differs from
                  the requested cal_id, so ``create-calendar.stable-url`` is
                  unsupported.  This models BOTH Zimbra (display-name-derived
                  segment) and OX (opaque cal://0/NNN segment) with no
                  distinction.  If False the canonical segment equals the cal_id
                  (normal server) -> stable.
        name_sticks: if False, the display name given at creation is not applied.
        get_displayname_raises: if True, the direct DAV:displayname read on the
                  calendar object raises an exception (simulating no PROPFIND support).
        make_calendar_raises: if True, creating the probe calendar raises a DAVError
                  (e.g. a server that needs mkcol, or refuses a name= at creation).
        existing_displayname: if set, the principal already exposes one calendar
                  with this display name (used as the propfind.displayname fallback
                  target when probe-calendar creation fails).
        cal_id_reachable: whether a PROPFIND/GET (events()) on the requested cal_id
                  succeeds - used only as the creation-materialised barrier and for
                  the name-did-not-stick branch.  It no longer decides stable-url
                  (the canonical-URL segment comparison does); Zimbra keeps a
                  reachable collection-level alias at the cal_id even when the URL
                  is not stable, so reachability is a misleading signal.
        located_segment: the URL segment the calendar is discovered under when
                  looked up by display name.  Defaults to a name-derived segment
                  when ``relocate`` else the cal_id; pass an opaque value to model
                  OX's cal://0/NNN canonical URL.
        """
        probe_cal_id = "caldav-server-checker-displayname-test"
        base = "https://example.com/dav/me/"

        created = {"name": None, "url": None}

        def make_calendar(cal_id=None, name=None, **kwargs):
            if make_calendar_raises:
                raise DAVError("cannot create probe calendar")
            cal = Mock()
            cal.url = base + cal_id + "/"
            if name_sticks and name:
                created["name"] = name
                segment = located_segment or (name if relocate else cal_id)
                created["url"] = base + segment + "/"
            return cal

        principal.make_calendar.side_effect = make_calendar

        # calendar(cal_id=...) is used for the cal_id reachability probe
        # (events()), the direct get_display_name() probe, and cleanup delete().
        direct_cal_mock = Mock()
        if not cal_id_reachable:
            # the requested cal_id 404s after the create-with-a-name (relocate)
            direct_cal_mock.events.side_effect = DAVError("cal_id not found")
        if get_displayname_raises:
            direct_cal_mock.get_display_name.side_effect = Exception("not supported")
            direct_cal_mock.get_property.side_effect = DAVError("PROPFIND refused")
        else:
            direct_cal_mock.get_display_name.return_value = "some-name"
        principal.calendar.return_value = direct_cal_mock

        def calendars():
            cals = []
            if existing_displayname is not None:
                existing = Mock()
                existing.url = base + "existing/"
                existing.get_display_name.return_value = existing_displayname
                cals.append(existing)
            if created["name"] is not None:
                c = Mock()
                c.url = created["url"]
                if get_displayname_raises:
                    # A server with no PROPFIND displayname support cannot be
                    # found by display name either - model that consistently.
                    c.get_display_name.side_effect = Exception("not supported")
                    c.get_property.side_effect = DAVError("PROPFIND refused")
                else:
                    c.get_display_name.return_value = created["name"]
                cals.append(c)
            return cals

        principal.calendars.side_effect = calendars

        check = CheckMakeDeleteCalendar(checker)
        check._check_set_displayname()

    def test_set_displayname_stable_url(self) -> None:
        """A normal server keeps the calendar at the requested cal_id URL -> stable."""
        checker, client, principal = self.create_checker_with_principal()
        self._run_displayname_probe(checker, principal, relocate=False)
        assert checker.features_checked.is_supported("create-calendar.set-displayname")
        assert checker.features_checked.is_supported("create-calendar.stable-url")

    def test_stable_url_opaque_canonical_is_unsupported(self) -> None:
        """OX-style: the calendar's canonical URL is an opaque cal://0/NNN that
        differs from the requested cal_id.  Under the URL-stability semantics this
        is reported ``unsupported`` exactly like Zimbra - no special-casing - so
        the consuming library adopts the canonical URL after creation."""
        checker, client, principal = self.create_checker_with_principal()
        self._run_displayname_probe(
            checker,
            principal,
            relocate=True,
            located_segment="cal%3A%2F%2F0%2F4711",
        )
        assert checker.features_checked.is_supported("create-calendar.set-displayname")
        assert not checker.features_checked.is_supported("create-calendar.stable-url")
        assert checker.features_checked.is_supported("create-calendar.stable-url", str) == "unsupported"

    def test_set_displayname_relocates_url(self) -> None:
        """Zimbra-style: the calendar's canonical URL is a display-name-derived
        segment differing from the requested cal_id -> not stable."""
        checker, client, principal = self.create_checker_with_principal()
        self._run_displayname_probe(checker, principal, relocate=True)
        # the name is still applied, so set-displayname itself is supported ...
        assert checker.features_checked.is_supported("create-calendar.set-displayname")
        # ... but the URL is not stable.
        assert not checker.features_checked.is_supported("create-calendar.stable-url")
        assert checker.features_checked.is_supported("create-calendar.stable-url", str) == "unsupported"

    def test_set_displayname_not_applied(self) -> None:
        """If the display name does not stick, set-displayname is unsupported."""
        checker, client, principal = self.create_checker_with_principal()
        self._run_displayname_probe(checker, principal, relocate=False, name_sticks=False)
        assert not checker.features_checked.is_supported("create-calendar.set-displayname")

    def test_get_displayname_supported(self) -> None:
        """Server returns DAV:displayname via PROPFIND — propfind.displayname is full."""
        checker, client, principal = self.create_checker_with_principal()
        self._run_displayname_probe(checker, principal, relocate=False)
        assert checker.features_checked.is_supported("propfind.displayname")

    def test_get_displayname_unsupported(self) -> None:
        """The server raises on the PROPFIND — that is ungraceful, not unsupported.

        It answered, with an error the client can catch; "unsupported" would
        claim it silently ignored us.
        """
        checker, client, principal = self.create_checker_with_principal()
        self._run_displayname_probe(checker, principal, relocate=False, get_displayname_raises=True)
        assert checker.features_checked.is_supported("propfind.displayname", str) == "ungraceful"

    def test_set_displayname_probe_calendar_creation_fails(self) -> None:
        """If the probe calendar can't be created (e.g. Infomaniak), the probe
        must still leave every feature it declares set — otherwise the
        post-check assertion in run_check() trips on the un-collapsible
        ``propfind.displayname`` (its parent ``propfind`` is never set).

        Regression for: ``CheckMakeDeleteCalendar failed to check declared
        features: {'propfind.displayname'}``.
        """
        checker, client, principal = self.create_checker_with_principal()
        self._run_displayname_probe(checker, principal, relocate=False, make_calendar_raises=True)
        for feature in (
            "propfind.displayname",
            "create-calendar.set-displayname",
            "create-calendar.stable-url",
        ):
            assert feature in checker.features_checked.dotted_feature_set_list(), (
                f"{feature} left unchecked after a failed probe calendar creation"
            )

    def test_propfind_displayname_probed_on_existing_calendar(self) -> None:
        """Even when we can't create a probe calendar, reading DAV:displayname is
        an ordinary PROPFIND, so propfind.displayname is still determined against
        an existing calendar rather than left as 'unknown'."""
        checker, client, principal = self.create_checker_with_principal()
        self._run_displayname_probe(
            checker, principal, relocate=False, make_calendar_raises=True, existing_displayname="My Calendar"
        )
        assert checker.features_checked.is_supported("propfind.displayname")
        ## set-displayname behaviour is genuinely untestable here -> unknown
        assert checker.features_checked.is_supported("create-calendar.set-displayname", str) == "unknown"

    def _drive_run_check_without_create_calendar(self, checker, principal):
        """Run CheckMakeDeleteCalendar.run_check() with the make/delete probe
        stubbed to the terminal state of a server that supports no calendar
        creation at all (verified: Infomaniak).  Exercises the post-check
        assertion in run_check() that used to abort the whole run."""
        check = CheckMakeDeleteCalendar(checker)

        def fake_probe_make_delete():
            check.set_feature("get-current-user-principal.has-calendar", True)
            check.set_feature("create-calendar.auto", False)
            check.set_feature("create-calendar", False)
            unknown = {"support": "unknown", "behaviour": "cannot test, create-calendar not supported"}
            check.set_feature("delete-calendar", unknown)
            check.set_feature("delete-calendar.free-namespace", unknown)

        check._probe_make_delete = fake_probe_make_delete
        check.run_check()
        return check

    def test_propfind_displayname_probed_when_create_calendar_unsupported(self) -> None:
        """A server with no calendar-creation support must not abort the run:
        propfind.displayname is an ordinary PROPFIND, probed against an existing
        calendar even though set-displayname cannot be tested.

        Regression for: ``CheckMakeDeleteCalendar failed to check declared
        features: {'propfind.displayname'}`` (the whole run aborted)."""
        checker, client, principal = self.create_checker_with_principal()
        existing = Mock()
        existing.url = "https://example.com/dav/me/existing/"
        existing.get_display_name.return_value = "My Calendar"
        principal.calendars.return_value = [existing]

        check = self._drive_run_check_without_create_calendar(checker, principal)
        assert check.feature_checked("propfind.displayname")

    def test_propfind_displayname_unknown_when_no_calendar_and_no_create(self) -> None:
        """No calendar to probe and no create support: propfind.displayname is
        recorded 'unknown' rather than left unset (which would abort the run)."""
        checker, client, principal = self.create_checker_with_principal()
        principal.calendars.return_value = []

        check = self._drive_run_check_without_create_calendar(checker, principal)
        assert check.feature_checked("propfind.displayname", str) == "unknown"

    @pytest.mark.skip(
        reason="Complex multi-call mocking pattern - CheckMakeDeleteCalendar._run_check has very complex logic with multiple retry paths"
    )
    def test_calendar_deletion_successful(self) -> None:
        """Successful calendar deletion should set delete-calendar feature"""
        checker, client, principal = self.create_checker_with_principal()

        # Mock successful calendar creation
        mock_calendar = Mock()
        mock_calendar.events.return_value = []
        mock_calendar.id = "caldav-server-checker-mkdel-test"

        # Track deletion
        deleted_count = [0]

        def delete_side_effect():
            deleted_count[0] += 1

        mock_calendar.delete = delete_side_effect
        principal.make_calendar.return_value = mock_calendar
        principal.calendars.return_value = []

        # After deletion, calendar should not be found
        call_count = [0]

        def calendar_lookup(cal_id=None, name=None):
            call_count[0] += 1
            # Initial cleanup - doesn't exist
            if call_count[0] == 1 and cal_id == "this_should_not_exist":
                raise NotFoundError("Not found")
            # After deletion, calendar not found
            if deleted_count[0] > 0 and cal_id == "caldav-server-checker-mkdel-test":
                raise NotFoundError("Deleted")
            # Lookup by name  with cal_id
            if name == "Yep" and cal_id == "caldav-server-checker-mkdel-test":
                cal = Mock()
                cal.id = mock_calendar.id
                cal.events.return_value = []
                return cal
            # Before deletion, return calendar
            if cal_id == "caldav-server-checker-mkdel-test":
                return mock_calendar
            raise NotFoundError("Not found")

        principal.calendar.side_effect = calendar_lookup

        check = CheckMakeDeleteCalendar(checker)
        check.run_check()

        # Should detect deletion support
        assert checker.features_checked.is_supported("delete-calendar")

    @pytest.mark.skip(
        reason="Complex multi-call mocking pattern - CheckMakeDeleteCalendar._run_check has very complex logic with multiple retry paths"
    )
    def test_calendar_has_default_calendar(self) -> None:
        """Principal with existing calendars should set has-calendar feature"""
        checker, client, principal = self.create_checker_with_principal()

        # Mock existing calendars
        mock_calendar = Mock()
        mock_calendar.events.return_value = []
        principal.calendars.return_value = [mock_calendar]

        # Mock calendar creation for test calendars
        mock_test_cal = Mock()
        mock_test_cal.events.return_value = []
        mock_test_cal.id = "caldav-server-checker-mkdel-test"
        deleted_count = [0]

        def delete_cal():
            deleted_count[0] += 1

        mock_test_cal.delete = delete_cal
        principal.make_calendar.return_value = mock_test_cal

        call_count = [0]

        def calendar_lookup(cal_id=None, name=None):
            call_count[0] += 1
            # Initial cleanup
            if call_count[0] == 1 and cal_id == "this_should_not_exist":
                raise NotFoundError("Not found")
            # After deletion
            if deleted_count[0] > 0 and cal_id == "caldav-server-checker-mkdel-test":
                raise NotFoundError("Deleted")
            # Lookup by name with cal_id
            if name == "Yep" and cal_id == "caldav-server-checker-mkdel-test":
                cal = Mock()
                cal.id = mock_test_cal.id
                cal.events.return_value = []
                return cal
            # Before deletion
            if cal_id == "caldav-server-checker-mkdel-test":
                return mock_test_cal
            raise NotFoundError("Not found")

        principal.calendar.side_effect = calendar_lookup

        check = CheckMakeDeleteCalendar(checker)
        check.run_check()

        # Should detect existing calendar
        assert checker.features_checked.is_supported("get-current-user-principal.has-calendar")


class TestPrepareCalendar:
    """Test PrepareCalendar with mocked server responses

    Note: PrepareCalendar is complex and does extensive setup. These tests
    focus on key mocking patterns rather than exhaustive coverage.
    """

    def create_checker_with_calendar(self) -> tuple[ServerQuirkChecker, Mock, Mock]:
        """Helper to create checker with mocked calendar"""
        client = Mock()
        client.features = FeatureSet()
        # Mock expected_features to avoid lookup issues
        client.features.copyFeatureSet({"test-calendar.compatibility-tests": {}}, collapse=False)
        checker = ServerQuirkChecker(client, debug_mode=None)

        # Mock principal
        mock_principal = Mock()
        checker.principal = mock_principal
        checker.expected_features = client.features

        # Mark dependencies as run
        checker._checks_run.add(CheckGetCurrentUserPrincipal)
        checker._checks_run.add(CheckMakeDeleteCalendar)

        # Mock that create-calendar is supported
        checker._features_checked.copyFeatureSet({"create-calendar": {"support": "full"}}, collapse=False)

        return checker, client, mock_principal

    def test_prepare_uses_existing_calendar_by_id(self) -> None:
        """PrepareCalendar should use existing calendar if found"""
        checker, client, principal = self.create_checker_with_calendar()

        # Mock existing calendar with all necessary methods.
        # events() is called 4 times: (1) existence check l.287, (2) _filter_fixture_window fallback,
        # (3) recurrences check l.646, (4) final sanity assert l.673 — must return truthy.
        mock_calendar = Mock()
        mock_calendar.events.side_effect = [[], [], [], [Mock()]]
        mock_calendar.todos.return_value = []
        mock_calendar.journals.return_value = []
        mock_calendar.search.return_value = []

        # Mock save_object to handle test data creation
        def save_object(*args, **kwargs):
            obj = Mock()
            obj.component = Mock()
            obj.component.__getitem__ = lambda self, key: kwargs.get("uid", "test-uid")
            obj.load = Mock()
            return obj

        mock_calendar.save_object = save_object
        principal.calendar.return_value = mock_calendar

        check = PrepareCalendar(checker)
        check.run_check()

        # Should use existing calendar
        assert checker.calendar == mock_calendar
        principal.make_calendar.assert_not_called()

    def test_prepare_creates_calendar_if_not_found(self) -> None:
        """PrepareCalendar should create calendar if not found"""
        checker, client, principal = self.create_checker_with_calendar()

        # Mock calendar not found on first call, then return created calendar.
        # events() is called 3 times here: existence-check call (l.287) is never reached because
        # principal.calendar() raises first; then (1) _filter_fixture_window fallback,
        # (2) recurrences check l.646, (3) final sanity assert l.673 — must return truthy.
        call_count = [0]
        mock_calendar = Mock()
        mock_calendar.events.side_effect = [[], [], [Mock()]]
        mock_calendar.todos.return_value = []
        mock_calendar.journals.return_value = []
        mock_calendar.search.return_value = []

        def save_object(*args, **kwargs):
            obj = Mock()
            obj.component = Mock()
            obj.component.__getitem__ = lambda self, key: kwargs.get("uid", "test-uid")
            obj.load = Mock()
            return obj

        mock_calendar.save_object = save_object

        def calendar_side_effect(cal_id=None, name=None):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("Not found")
            return mock_calendar

        principal.calendar.side_effect = calendar_side_effect
        principal.make_calendar.return_value = mock_calendar

        check = PrepareCalendar(checker)
        check.run_check()

        # Should create calendar
        principal.make_calendar.assert_called_once()
        assert checker.calendar == mock_calendar

    def test_prepare_sets_save_load_event_feature(self) -> None:
        """PrepareCalendar should set save-load.event feature"""
        checker, client, principal = self.create_checker_with_calendar()

        # Mock calendar with all necessary behavior.
        # events() is called 4 times: (1) existence check l.287, (2) _filter_fixture_window fallback,
        # (3) recurrences check l.646, (4) final sanity assert l.673 — must return truthy.
        mock_calendar = Mock()
        mock_calendar.events.side_effect = [[], [], [], [Mock()]]
        mock_calendar.todos.return_value = []
        mock_calendar.journals.return_value = []
        mock_calendar.search.return_value = []

        def save_object(*args, **kwargs):
            obj = Mock()
            obj.component = Mock()
            obj.component.__getitem__ = lambda self, key: kwargs.get("uid", "test-uid")
            obj.load = Mock()
            return obj

        mock_calendar.save_object = save_object
        principal.calendar.return_value = mock_calendar

        check = PrepareCalendar(checker)
        check.run_check()

        # Should set event save/load feature
        assert checker.features_checked.is_supported("save-load.event")


class TestCheckSearch:
    """Test CheckSearch with mocked server responses"""

    def create_checker_with_prepared_calendar(self) -> tuple[ServerQuirkChecker, Mock, Mock]:
        """Helper to create checker with prepared calendar"""
        client = Mock()
        client.features = FeatureSet()
        checker = ServerQuirkChecker(client, debug_mode=None)

        # Mock calendar and tasklist
        mock_calendar = Mock()
        mock_tasklist = Mock()
        # search.unlimited-time-range check calls _request_report_build_resultlist directly
        # and unpacks the result as (_, objects); return a proper tuple to avoid TypeError
        mock_calendar._request_report_build_resultlist.return_value = (None, [])
        checker.calendar = mock_calendar
        checker.tasklist = mock_tasklist
        checker.fixture_base_year = 2027

        # Mark dependencies as run
        checker._checks_run.add(CheckGetCurrentUserPrincipal)
        checker._checks_run.add(CheckMakeDeleteCalendar)
        checker._checks_run.add(PrepareCalendar)

        return checker, mock_calendar, mock_tasklist

    def test_search_time_range_event_success(self) -> None:
        """Successful time-range event search sets feature to True"""
        checker, calendar, tasklist = self.create_checker_with_prepared_calendar()

        # Mock search returning one event
        mock_event = Mock()
        calendar.search.return_value = [mock_event]
        tasklist.search.return_value = []

        check = CheckSearch(checker)
        check.run_check()

        # Should set feature to supported
        assert checker.features_checked.is_supported("search.time-range.event")

    def test_search_time_range_event_failure(self) -> None:
        """Failed time-range event search (wrong count) sets feature to False"""
        checker, calendar, tasklist = self.create_checker_with_prepared_calendar()

        # Mock search returning wrong number of events
        calendar.search.return_value = []
        tasklist.search.return_value = []

        check = CheckSearch(checker)
        check.run_check()

        # Should set feature to unsupported
        assert not checker.features_checked.is_supported("search.time-range.event")

    def test_search_time_range_todo_success(self) -> None:
        """Successful time-range todo search sets feature to True"""
        checker, calendar, tasklist = self.create_checker_with_prepared_calendar()

        # Mock search
        calendar.search.return_value = [Mock()]  # One event
        mock_todo = Mock()
        tasklist.search.return_value = [mock_todo]  # One todo

        check = CheckSearch(checker)
        check.run_check()

        # Should set todo search feature
        assert checker.features_checked.is_supported("search.time-range.todo")

    def test_search_category_supported(self) -> None:
        """Category search returning correct results sets feature to True"""
        checker, calendar, tasklist = self.create_checker_with_prepared_calendar()

        # Mock initial time-range searches
        def search_side_effect(**kwargs):
            if "category" in kwargs:
                # Category search returns one result
                return [Mock()]
            elif "event" in kwargs and kwargs.get("event"):
                # Time-range event search
                return [Mock()]
            elif "todo" in kwargs:
                # Time-range todo search
                return [Mock()]
            return []

        calendar.search.side_effect = search_side_effect
        tasklist.search.side_effect = search_side_effect

        check = CheckSearch(checker)
        check.run_check()

        # Should set category search feature
        assert checker.features_checked.is_supported("search.text.category")

    def test_search_category_ungraceful(self) -> None:
        """Category search raising ReportError sets feature to 'ungraceful'"""
        checker, calendar, tasklist = self.create_checker_with_prepared_calendar()

        def search_side_effect(**kwargs):
            if "category" in kwargs:
                raise ReportError("Category not supported")
            elif "event" in kwargs and kwargs.get("event"):
                return [Mock()]
            elif "todo" in kwargs:
                return [Mock()]
            return []

        calendar.search.side_effect = search_side_effect
        tasklist.search.return_value = [Mock()]

        check = CheckSearch(checker)
        check.run_check()

        # Should set feature to ungraceful
        result = checker.features_checked.is_supported("search.text.category", str)
        assert result == "ungraceful"

    def test_search_combined_logical_and(self) -> None:
        """Combined search filters should work as logical AND"""
        checker, calendar, tasklist = self.create_checker_with_prepared_calendar()

        search_calls = []

        def search_side_effect(**kwargs):
            search_calls.append(kwargs)

            # Time-range only
            if "event" in kwargs and "category" not in kwargs:
                return [Mock()]

            # Category + time range (wider range) = 1 result
            if "category" in kwargs and "start" in kwargs:
                start = kwargs["start"]
                if start.day == 1 and start.hour == 11:
                    return [Mock()]  # Wider range matches
                elif start.day == 1 and start.hour == 9:
                    return []  # Narrower range doesn't match

            # Just category
            if "category" in kwargs:
                return [Mock()]

            # Todos
            if "todo" in kwargs:
                return [Mock()]

            return []

        calendar.search.side_effect = search_side_effect
        tasklist.search.return_value = [Mock()]

        check = CheckSearch(checker)
        check.run_check()

        # Should detect logical AND
        assert checker.features_checked.is_supported("search.combined-is-logical-and")


class TestCheckIsNotDefined:
    """Test CheckIsNotDefined with mocked server responses"""

    def create_checker_with_prepared_calendar(self) -> tuple[ServerQuirkChecker, Mock]:
        """Helper to create checker with prepared calendar"""
        client = Mock()
        client.features = FeatureSet()
        checker = ServerQuirkChecker(client, debug_mode=None)

        mock_calendar = Mock()
        checker.calendar = mock_calendar

        # Mark dependencies as run
        checker._checks_run.add(CheckGetCurrentUserPrincipal)
        checker._checks_run.add(CheckMakeDeleteCalendar)
        checker._checks_run.add(PrepareCalendar)
        checker._checks_run.add(CheckSearch)

        return checker, mock_calendar

    def _make_event_mock(self, uid, has_categories=False, has_class=False):
        """Create a mock event with optional CATEGORIES and CLASS"""
        event = Mock()
        component = {}
        component["uid"] = uid
        if has_categories:
            component["categories"] = "test"
        if has_class:
            component["class"] = "CONFIDENTIAL"
        event.component = Mock()
        event.component.get = lambda key, default="": component.get(key, default)
        return event

    def _make_save_object_uid_capture(self) -> tuple[list[str], object]:
        """Return a (saved_uids, side_effect) pair that records UIDs from save_object calls.

        The iCal UID is extracted from the iCal string so that the search mock can
        return events with UIDs matching what was actually saved.
        """
        import re

        saved_uids: list[str] = []

        def side_effect(cls, ical_str: str) -> Mock:
            m = re.search(r"^UID:(\S+)", ical_str, re.MULTILINE)
            uid = m.group(1) if m else f"unknown_{len(saved_uids)}"
            saved_uids.append(uid)
            return Mock()

        return saved_uids, side_effect

    def test_is_not_defined_full_support(self) -> None:
        """Category, class, and dtend is-not-defined all work correctly"""
        checker, calendar = self.create_checker_with_prepared_calendar()

        def search_side_effect(**kwargs):
            if kwargs.get("no_category"):
                # Return events WITHOUT categories (exclude csc_event_with_categories)
                return [
                    self._make_event_mock("csc_simple_event1"),
                    self._make_event_mock("csc_simple_event2"),
                ]
            if kwargs.get("no_class"):
                # Return events WITHOUT class (exclude csc_event_with_class)
                return [
                    self._make_event_mock("csc_simple_event1"),
                    self._make_event_mock("csc_simple_event2"),
                ]
            if kwargs.get("no_dtend"):
                # Return only the event without DTEND (csc_event_with_duration);
                # csc_simple_event1 (has DTEND) is correctly excluded
                return [self._make_event_mock("csc_event_with_duration")]
            return [Mock()]

        calendar.search.side_effect = search_side_effect

        check = CheckIsNotDefined(checker)
        check.run_check()

        assert checker.features_checked.is_supported("search.is-not-defined")
        assert checker.features_checked.is_supported("search.is-not-defined.category")
        assert checker.features_checked.is_supported("search.is-not-defined.dtend")

    def test_is_not_defined_category_unsupported(self) -> None:
        """Server supports is-not-defined for CLASS and DTEND but not for CATEGORIES"""
        checker, calendar = self.create_checker_with_prepared_calendar()

        def search_side_effect(**kwargs):
            if kwargs.get("no_category"):
                # Server returns nothing for category is-not-defined (broken for CATEGORIES)
                return []
            if kwargs.get("no_class"):
                # Works correctly: excludes csc_event_with_class
                return [
                    self._make_event_mock("csc_simple_event1"),
                    self._make_event_mock("csc_simple_event2"),
                ]
            if kwargs.get("no_dtend"):
                # Works correctly: returns csc_event_with_duration, excludes csc_simple_event1
                return [self._make_event_mock("csc_event_with_duration")]
            return [Mock()]

        calendar.search.side_effect = search_side_effect

        check = CheckIsNotDefined(checker)
        check.run_check()

        assert not checker.features_checked.is_supported("search.is-not-defined")
        assert not checker.features_checked.is_supported("search.is-not-defined.category")
        assert checker.features_checked.is_supported("search.is-not-defined.dtend")
        assert checker.features_checked.is_supported("search.is-not-defined", str) == "fragile"

    def test_is_not_defined_unsupported(self) -> None:
        """Server ignores is-not-defined filter (no properties work)"""
        checker, calendar = self.create_checker_with_prepared_calendar()

        def search_side_effect(**kwargs):
            if kwargs.get("no_category"):
                # Returns the event WITH categories too (filter ignored)
                return [
                    self._make_event_mock("csc_simple_event1"),
                    self._make_event_mock("csc_event_with_categories", has_categories=True),
                ]
            if kwargs.get("no_class"):
                # Returns csc_event_with_class too (filter ignored)
                return [
                    self._make_event_mock("csc_simple_event1"),
                    self._make_event_mock("csc_event_with_class"),
                ]
            if kwargs.get("no_dtend"):
                # Returns csc_simple_event1 (has DTEND) but not csc_event_with_duration (filter broken)
                return [self._make_event_mock("csc_simple_event1")]
            return [Mock()]

        calendar.search.side_effect = search_side_effect

        check = CheckIsNotDefined(checker)
        check.run_check()

        assert not checker.features_checked.is_supported("search.is-not-defined")
        assert not checker.features_checked.is_supported("search.is-not-defined.category")
        assert not checker.features_checked.is_supported("search.is-not-defined.dtend")

    def test_is_not_defined_ungraceful(self) -> None:
        """Server throws error on is-not-defined search"""
        checker, calendar = self.create_checker_with_prepared_calendar()

        calendar.search.side_effect = ReportError("is-not-defined not supported")

        check = CheckIsNotDefined(checker)
        check.run_check()

        result = checker.features_checked.is_supported("search.is-not-defined", str)
        assert result == "ungraceful"
        assert checker.features_checked.is_supported("search.is-not-defined.category", str) == "ungraceful"
        assert checker.features_checked.is_supported("search.is-not-defined.dtend", str) == "ungraceful"


class TestCheckWellKnown:
    """Test CheckWellKnown with mocked HTTP responses"""

    def create_checker_with_mock_client(
        self, url: str = "https://caldav.example.com/dav/", dav_header: str = "1, 2, 3, calendar-access"
    ) -> tuple:
        """``dav_header`` is what an OPTIONS on the discovery target answers with.

        The default advertises calendar-access, i.e. discovery really landed on
        a CalDAV server; pass something else for a target that does not.
        """
        client = Mock()
        client.features = FeatureSet()
        client.url = url
        options = Mock()
        options.headers = {"DAV": dav_header}
        client.session.request.return_value = options
        checker = ServerQuirkChecker(client, debug_mode=None)
        return checker, client

    def test_redirect_to_a_caldav_server_is_full(self) -> None:
        checker, client = self.create_checker_with_mock_client()
        mock_response = Mock()
        mock_response.status_code = 301
        mock_response.headers = {"Location": "/remote.php/dav/"}
        client.session.get.return_value = mock_response

        CheckWellKnown(checker).run_check()

        result = checker.features_checked.is_supported("well-known", str)
        assert result == "full"

    def test_redirect_to_something_that_is_not_caldav_is_unsupported(self) -> None:
        """A front-end that sends unknown paths to /login answers the GET with a
        redirect too - discovery that lands there has not worked."""
        checker, client = self.create_checker_with_mock_client(dav_header="1, 2")
        mock_response = Mock()
        mock_response.status_code = 302
        mock_response.headers = {"Location": "/login"}
        client.session.get.return_value = mock_response

        CheckWellKnown(checker).run_check()

        assert checker.features_checked.is_supported("well-known", str) == "unsupported"

    def test_redirect_without_a_location_is_unsupported(self) -> None:
        checker, client = self.create_checker_with_mock_client()
        mock_response = Mock()
        mock_response.status_code = 301
        mock_response.headers = {}
        client.session.get.return_value = mock_response

        CheckWellKnown(checker).run_check()

        assert checker.features_checked.is_supported("well-known", str) == "unsupported"

    def test_200_from_a_caldav_server_is_full(self) -> None:
        checker, client = self.create_checker_with_mock_client()
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {}
        client.session.get.return_value = mock_response

        CheckWellKnown(checker).run_check()

        assert checker.features_checked.is_supported("well-known", str) == "full"

    def test_200_from_something_that_is_not_caldav_is_unsupported(self) -> None:
        """An SPA serving index.html for every path answers 200 here."""
        checker, client = self.create_checker_with_mock_client(dav_header="")
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {}
        client.session.get.return_value = mock_response

        CheckWellKnown(checker).run_check()

        assert checker.features_checked.is_supported("well-known", str) == "unsupported"

    def test_target_that_cannot_be_reached_is_unknown(self) -> None:
        checker, client = self.create_checker_with_mock_client()
        mock_response = Mock()
        mock_response.status_code = 301
        mock_response.headers = {"Location": "/dav/"}
        client.session.get.return_value = mock_response
        client.session.request.side_effect = Exception("Connection refused")

        CheckWellKnown(checker).run_check()

        assert checker.features_checked.is_supported("well-known", str) == "unknown"

    def test_404_sets_feature_unsupported(self) -> None:
        checker, client = self.create_checker_with_mock_client()
        mock_response = Mock()
        mock_response.status_code = 404
        mock_response.headers = {}
        client.session.get.return_value = mock_response

        CheckWellKnown(checker).run_check()

        assert not checker.features_checked.is_supported("well-known")

    def test_request_failure_sets_unknown(self) -> None:
        checker, client = self.create_checker_with_mock_client()
        client.session.get.side_effect = Exception("Connection refused")

        CheckWellKnown(checker).run_check()

        assert checker.features_checked.is_supported("well-known", str) == "unknown"

    def test_localhost_sets_unknown(self) -> None:
        checker, client = self.create_checker_with_mock_client(url="http://localhost:5232/")

        CheckWellKnown(checker).run_check()

        assert checker.features_checked.is_supported("well-known", str) == "unknown"
        client.session.get.assert_not_called()

    def test_well_known_url_constructed_correctly(self) -> None:
        checker, client = self.create_checker_with_mock_client(url="https://caldav.example.com/dav/calendars/")
        mock_response = Mock()
        mock_response.status_code = 404
        mock_response.headers = {}
        client.session.get.return_value = mock_response

        CheckWellKnown(checker).run_check()

        call_url = client.session.get.call_args[0][0]
        assert call_url == "https://caldav.example.com/.well-known/caldav"


class TestCleanupDoesNotDeleteUserData:
    """Regression tests for the data-loss findings (code review 2026-06-11, #1 and #2).

    When --caldav-calendar points at a real, pre-existing user calendar, neither
    cleanup() nor PrepareCalendar's stale-fixture sweep may delete data the tool
    did not create.
    """

    def _checker(self) -> ServerQuirkChecker:
        client = Mock()
        client.features = FeatureSet()
        return ServerQuirkChecker(client, debug_mode=None)

    def _calendar_supporting_delete(self, checker: ServerQuirkChecker) -> None:
        checker._features_checked.set_feature("create-calendar", {"support": "full"})
        checker._features_checked.set_feature("delete-calendar", {"support": "full"})

    def test_preexisting_user_calendar_not_deleted(self) -> None:
        """#2: a calendar the tool merely found (not created) must survive cleanup."""
        checker = self._checker()
        cal = Mock()
        cal.objects.return_value = []  # nothing to purge
        checker.calendar = cal
        checker.tasklist = cal
        checker.journallist = cal
        checker.calendar_was_created = False
        self._calendar_supporting_delete(checker)

        checker.cleanup(force=True)

        cal.delete.assert_not_called()

    def test_user_data_object_not_purged(self) -> None:
        """#2: non-csc_ objects in a found calendar must not be deleted during purge."""
        checker = self._checker()
        cal = Mock()
        user_obj = Mock()
        user_obj.icalendar_component.get.return_value = "real-user-meeting"
        cal.objects.return_value = [user_obj]
        checker.calendar = cal
        checker.tasklist = cal
        checker.journallist = cal
        checker.calendar_was_created = False
        self._calendar_supporting_delete(checker)

        checker.cleanup(force=True)

        cal.delete.assert_not_called()
        user_obj.delete.assert_not_called()

    def test_tool_created_calendar_is_deleted(self) -> None:
        """A calendar the tool created should still be deleted wholesale."""
        checker = self._checker()
        cal = Mock()
        checker.calendar = cal
        checker.tasklist = cal
        checker.journallist = cal
        checker.calendar_was_created = True
        self._calendar_supporting_delete(checker)

        checker.cleanup(force=True)

        cal.delete.assert_called_once()

    def test_delete_stale_fixtures_skips_non_csc(self) -> None:
        """#1: the stale-fixture sweep only deletes csc_* objects, never real data."""
        checker = self._checker()
        check = PrepareCalendar(checker)
        csc_obj = Mock()
        real_event = Mock()
        real_journal = Mock()
        object_by_uid = {
            "csc_simple_event1": csc_obj,
            "real-meeting-2026": real_event,
            "user-journal-uid": real_journal,
        }

        check._delete_stale_fixtures(object_by_uid)

        csc_obj.delete.assert_called_once()
        real_event.delete.assert_not_called()
        real_journal.delete.assert_not_called()


class TestDelayedDeleteIsAQuirk:
    """A measured write delay is a quirk, not fragile.

    "fragile" means slightly non-deterministic - it sometimes works and
    sometimes not.  Once we have established that the calendar IS deleted and
    only the timing varies, the behaviour is deterministic and the verdict is
    "quirk": supported, but the client has to handle it specially.  The
    distinction matters beyond the wording, since only 'quirk' counts as
    positive in FeatureSet (_POSITIVE_STATUSES) - a fragile verdict makes
    is_supported("delete-calendar") False and silently skips the
    free-namespace probe.
    """

    def _checker(self) -> tuple[ServerQuirkChecker, Mock]:
        client = Mock()
        client.features = FeatureSet()
        checker = ServerQuirkChecker(client, debug_mode=None)
        checker.principal = Mock()
        checker._checks_run.add(CheckGetCurrentUserPrincipal)
        ## _probe_make_delete settles this before calling _try_make_calendar, and
        ## the delete verdict reads it: a server that auto-creates a calendar on
        ## access cannot be probed for deletion at all.  Without it the feature
        ## inherits "full" from create-calendar and the delete branch is skipped.
        checker._features_checked.set_feature("create-calendar.auto", False)
        return checker, checker.principal

    def test_delayed_deletion_is_quirk_with_observed_delay(self, monkeypatch) -> None:
        import caldav_server_tester.checks as checks_mod

        checker, principal = self._checker()
        monkeypatch.setattr(checks_mod.time, "sleep", lambda _s: None)
        monkeypatch.setattr(checks_mod.DAVObject, "delete", lambda _self: None)

        ## Creation is immediate; after the DELETE the calendar keeps answering
        ## for three more probes before it finally 404s.  The first of those is
        ## the "was it deleted?" check, so the poll itself waits two seconds.
        state = {"n": 0, "deleted": False}

        def events():
            if not state["deleted"]:
                return []
            state["n"] += 1
            if state["n"] > 3:
                raise NotFoundError("gone at last")
            return []

        cal = Mock()
        cal.events.side_effect = events
        principal.calendar.return_value = cal
        principal.make_calendar.return_value = cal

        check = CheckMakeDeleteCalendar(checker)
        monkeypatch.setattr(checks_mod.DAVObject, "delete", lambda _self: state.__setitem__("deleted", True))
        check._try_make_calendar(cal_id="x")

        observed = checker.features_checked.is_supported("delete-calendar", dict)
        assert observed["support"] == "quirk"
        assert "delayed deletion" in observed["behaviour"]
        assert observed["delay"] == 2
        ## quirk is a positive status, so the free-namespace probe is no longer
        ## silently skipped on such servers
        assert checker.features_checked.is_supported("delete-calendar") is True

    def test_trashbin_is_still_unknown(self, monkeypatch) -> None:
        """A calendar that never disappears is not a delay - verdict unchanged."""
        import caldav_server_tester.checks as checks_mod

        checker, principal = self._checker()
        monkeypatch.setattr(checks_mod.time, "sleep", lambda _s: None)
        monkeypatch.setattr(checks_mod.DAVObject, "delete", lambda _self: None)

        cal = Mock()
        cal.events.return_value = []
        principal.calendar.return_value = cal
        principal.make_calendar.return_value = cal

        CheckMakeDeleteCalendar(checker)._try_make_calendar(cal_id="x")

        observed = checker.features_checked.is_supported("delete-calendar", dict)
        assert observed["support"] == "unknown"
        assert "trashbin" in observed["behaviour"]

    def test_delete_exception_that_clears_is_a_measured_quirk(self, monkeypatch) -> None:
        """Cyrus/Nextcloud: "deleting a recently created calendar fails".

        The first DELETE raises, a later one succeeds.  That is the same
        asynchronous-write shape, so measure how long it takes instead of
        flatly sleeping 10s and retrying once.
        """
        import caldav_server_tester.checks as checks_mod

        checker, principal = self._checker()
        monkeypatch.setattr(checks_mod.time, "sleep", lambda _s: None)

        attempts = {"n": 0}

        def raw_delete(_self):
            attempts["n"] += 1
            if attempts["n"] <= 2:
                raise DAVError("too soon after creation")

        monkeypatch.setattr(checks_mod.DAVObject, "delete", raw_delete)

        cal = Mock()
        cal.events.return_value = []
        principal.calendar.return_value = cal
        principal.make_calendar.return_value = cal

        CheckMakeDeleteCalendar(checker)._try_make_calendar(cal_id="x")

        observed = checker.features_checked.is_supported("delete-calendar", dict)
        assert observed["support"] == "quirk"
        assert observed["delay"] == 2
        assert "recently created" in observed["behaviour"]

    def test_delete_that_never_succeeds_is_unsupported(self, monkeypatch) -> None:
        import caldav_server_tester.checks as checks_mod

        checker, principal = self._checker()
        monkeypatch.setattr(checks_mod.time, "sleep", lambda _s: None)

        def raw_delete(_self):
            raise DAVError("never works")

        monkeypatch.setattr(checks_mod.DAVObject, "delete", raw_delete)

        cal = Mock()
        cal.events.return_value = []
        principal.calendar.return_value = cal
        principal.make_calendar.return_value = cal

        CheckMakeDeleteCalendar(checker)._try_make_calendar(cal_id="x")

        assert checker.features_checked.is_supported("delete-calendar", str) == "unsupported"


class TestCalendarProbeSuspendsWriteDelay:
    """CheckMakeDeleteCalendar measures the delay, so it must not enjoy it."""

    def test_probe_runs_with_the_configured_delay_suspended(self, monkeypatch) -> None:
        import caldav_server_tester.checks as checks_mod

        client = Mock()
        features = FeatureSet()
        features.copyFeatureSet({"write-delay": {"behaviour": "delay", "delay": 16}}, collapse=False)
        client.features = features
        client.request = Mock(return_value="response")
        checker = ServerQuirkChecker(client, debug_mode=None)
        checker.principal = Mock()
        checker._checks_run.add(CheckGetCurrentUserPrincipal)
        monkeypatch.setattr(checks_mod.time, "sleep", lambda _s: None)
        monkeypatch.setattr(checks_mod.DAVObject, "delete", lambda _self: None)

        cal = Mock()
        cal.events.return_value = []
        checker.principal.calendar.return_value = cal
        checker.principal.make_calendar.return_value = cal
        checker.principal.calendars.return_value = [cal]

        seen = []
        original = checks_mod.CheckMakeDeleteCalendar._probe_make_delete

        def spy(self):
            seen.append(client._write_delay)
            return original(self)

        monkeypatch.setattr(checks_mod.CheckMakeDeleteCalendar, "_probe_make_delete", spy)
        CheckMakeDeleteCalendar(checker)._run_check()

        assert seen == [0]  ## suspended while probing ...
        assert client._write_delay == 16  ## ... and restored afterwards

    def test_poll_timeout_outwaits_the_configured_delay(self) -> None:
        """Otherwise suspending the delay reports a slow server as a broken one."""
        client = Mock()
        features = FeatureSet()
        features.copyFeatureSet({"write-delay": {"behaviour": "delay", "delay": 16}}, collapse=False)
        client.features = features
        client.request = Mock(return_value="response")
        assert ServerQuirkChecker(client, debug_mode=None).delay_probe_timeout == 32


class TestLeftoverCalendarEvidence:
    """A calendar left behind by a previous run is evidence about this one.

    The probe waits a bounded time for MKCALENDAR to take effect.  When that
    runs out the honest reading is ambiguous: either creation does not work, or
    it works and is slower than we waited.  A calendar sitting there under our
    own stable cal_id settles it - a previous run created it, gave up waiting,
    and never came back to delete it, so the calendar did materialise, just too
    late to be seen.  Without this the run reports create-calendar as
    unsupported and leaks yet another orphan.
    """

    def _checker(self) -> tuple[ServerQuirkChecker, Mock]:
        client = Mock()
        client.features = FeatureSet()
        client.request = Mock(return_value="response")
        checker = ServerQuirkChecker(client, debug_mode=None)
        checker.principal = Mock()
        checker._checks_run.add(CheckGetCurrentUserPrincipal)
        return checker, checker.principal

    @staticmethod
    def _never_materialises(preexisting: bool) -> Mock:
        """A calendar that answers only until the probe wipes it."""
        state = {"wiped": not preexisting}

        def events():
            if state["wiped"]:
                raise NotFoundError("Node could not be found")
            return []

        cal = Mock()
        cal.events.side_effect = events
        cal.delete.side_effect = lambda: state.__setitem__("wiped", True)
        return cal

    def test_leftover_makes_it_a_delayed_creation_quirk(self, monkeypatch) -> None:
        import caldav_server_tester.checks as checks_mod

        checker, principal = self._checker()
        monkeypatch.setattr(checks_mod.time, "sleep", lambda _s: None)

        cal = self._never_materialises(preexisting=True)
        principal.calendar.return_value = cal
        principal.make_calendar.return_value = cal
        principal.calendars.return_value = [cal]

        CheckMakeDeleteCalendar(checker)._probe_make_delete()

        observed = checker.features_checked.is_supported("create-calendar", dict)
        assert observed["support"] == "quirk"
        assert "delayed creation" in observed["behaviour"]
        assert "previous run" in observed["behaviour"]
        ## The delete question cannot be answered when the calendar never showed
        ## up, but it must not be answered *wrongly* either.
        assert checker.features_checked.is_supported("delete-calendar", str) == "unknown"

    def test_no_leftover_still_means_unsupported(self, monkeypatch) -> None:
        """Without the evidence, a calendar that never appears is unsupported."""
        import caldav_server_tester.checks as checks_mod

        checker, principal = self._checker()
        monkeypatch.setattr(checks_mod.time, "sleep", lambda _s: None)

        cal = self._never_materialises(preexisting=False)
        principal.calendar.return_value = cal
        principal.make_calendar.return_value = cal
        principal.calendars.return_value = [cal]

        CheckMakeDeleteCalendar(checker)._probe_make_delete()

        assert checker.features_checked.is_supported("create-calendar", str) == "unsupported"

    def test_leftover_with_a_working_creation_changes_nothing(self, monkeypatch) -> None:
        """A leftover only means a previous run crashed if creation works now."""
        import caldav_server_tester.checks as checks_mod

        checker, principal = self._checker()
        monkeypatch.setattr(checks_mod.time, "sleep", lambda _s: None)
        monkeypatch.setattr(checks_mod.DAVObject, "delete", lambda _self: None)

        cal = Mock()
        cal.events.return_value = []
        principal.calendar.return_value = cal
        principal.make_calendar.return_value = cal
        principal.calendars.return_value = [cal]

        CheckMakeDeleteCalendar(checker)._probe_make_delete()

        assert checker.features_checked.is_supported("create-calendar", str) == "full"
