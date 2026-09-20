"""Unit tests for the ``scheduling.calendar-user-address-set.populated`` probe.

RFC6638 section 2.4.1 has the principal's ``calendar-user-address-set`` carry
the addresses a calendar user is known by.  A server can serve the property and
still leave it empty: Xandikos 0.4.7 does, while advertising
``calendar-auto-schedule`` and serving schedule-inbox and schedule-outbox.  The
principal then has no address of its own, and the same section has the URI of
the principal resource stand in - which is what the caldav library puts in
ORGANIZER and ATTENDEE.

So "the property is served" and "the property has an address in it" are two
different questions, and the second is this feature.  The first stays supported
on such a server, since the property does resolve.
"""

from unittest.mock import Mock

from caldav.compatibility_hints import FeatureSet
from caldav.lib.error import NotFoundError

from caldav_server_tester.checker import ServerQuirkChecker
from caldav_server_tester.checks import CheckScheduling, CheckSchedulingDetails


class TestSchedulingAddressSetPopulated:
    """A server can serve calendar-user-address-set and still leave it empty -
    Xandikos 0.4.7 does - so "the property is there" and "the principal has an
    address" are two different features."""

    @staticmethod
    def _was_recorded(checker) -> bool:
        """is_supported() walks up to the nearest recorded ancestor, so a child
        that was never set still answers whatever its parent got.  These tests
        are about the probe, so they have to ask whether the value was
        recorded, not what it resolves to."""
        return "scheduling.calendar-user-address-set.populated" in (
            checker.features_checked.dotted_feature_set_list(compact=False)
        )

    def create_checker_with_principal(self, addresses) -> tuple[ServerQuirkChecker, Mock]:
        client = Mock()
        client.features = FeatureSet()
        client.supports_scheduling.return_value = True
        checker = ServerQuirkChecker(client, debug_mode=None)

        principal = Mock()
        principal.calendar_user_address_set.return_value = addresses
        client.principal.return_value = principal
        checker.principal = principal

        CheckScheduling(checker).run_check()
        return checker, principal

    def test_populated_address_set(self) -> None:
        checker, _ = self.create_checker_with_principal(["mailto:someone@example.com"])
        CheckSchedulingDetails(checker).run_check()

        assert checker.features_checked.is_supported("scheduling.calendar-user-address-set")
        assert self._was_recorded(checker)
        assert checker.features_checked.is_supported("scheduling.calendar-user-address-set.populated")

    def test_empty_address_set_is_served_but_unpopulated(self) -> None:
        """The property resolves, so the parent feature stays supported; only the
        new sub-feature says there is no address in it."""
        checker, _ = self.create_checker_with_principal([])
        CheckSchedulingDetails(checker).run_check()

        assert checker.features_checked.is_supported("scheduling.calendar-user-address-set")
        assert self._was_recorded(checker)
        assert not checker.features_checked.is_supported("scheduling.calendar-user-address-set.populated")

    def test_absent_address_set_marks_both_unsupported(self) -> None:
        checker, principal = self.create_checker_with_principal([])
        principal.calendar_user_address_set.side_effect = NotFoundError("no such property")
        CheckSchedulingDetails(checker).run_check()

        assert not checker.features_checked.is_supported("scheduling.calendar-user-address-set")
        assert self._was_recorded(checker)
        assert not checker.features_checked.is_supported("scheduling.calendar-user-address-set.populated")
