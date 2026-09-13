"""The severity scale, and what a second look at a feature may overwrite.

A probe that takes a second look at something an earlier probe already graded
is there to catch bad news.  Letting it record whatever it happens to see means
the *easier* case can overwrite the harder one — and, because
`FeatureSet.set_feature` merges into the existing node rather than replacing
it, that a verdict can also inherit a `delay` measured by an observation it
just displaced.
"""

from caldav.compatibility_hints import FeatureSet

from caldav_server_tester.checks_base import SUPPORT_SEVERITY, is_worse


def test_non_determinism_is_worse_than_a_predictable_error() -> None:
    """`ungraceful` is rude; `fragile` is unreliable, which is harder.

    A client can catch an error it gets every single time.  `fragile` is also
    the one level `Calendar.delete()` reads to switch its retry loop on, so
    ranking it as the milder of the two suppresses the observation that would
    actually change what a client does.
    """
    assert is_worse("fragile", "ungraceful")
    assert not is_worse("ungraceful", "fragile")


def test_an_observation_displaces_the_absence_of_one() -> None:
    """ "unknown" is not a grade, it is the lack of one."""
    assert is_worse("fragile", "unknown")
    assert is_worse("full", "unknown")
    ## ...and never the other way round: nothing observed must not overwrite
    ## something observed.
    assert not is_worse("unknown", "fragile")
    assert not is_worse("unknown", "full")


def test_the_scale_is_monotone_for_every_pair() -> None:
    for i, worse in enumerate(SUPPORT_SEVERITY):
        for better in SUPPORT_SEVERITY[i + 1 :]:
            assert is_worse(worse, better), (worse, better)
            assert not is_worse(better, worse), (better, worse)


class TestRecordWorse:
    def _check(self):
        from unittest.mock import Mock

        from caldav_server_tester.checker import ServerQuirkChecker
        from caldav_server_tester.checks_base import Check

        client = Mock()
        client.features = FeatureSet()
        checker = ServerQuirkChecker(client, debug_mode=None)
        check = Check(checker)
        check.expected_features = client.features
        return check, checker

    def test_a_milder_verdict_is_not_recorded(self) -> None:
        check, checker = self._check()
        checker._features_checked.set_feature(
            "delete-calendar", {"support": "fragile", "behaviour": "the first probe saw this", "delay": 3}
        )

        recorded = check.record_worse(
            "delete-calendar", {"support": "quirk", "behaviour": "the second one", "delay": 1}
        )

        assert recorded is False
        observed = checker.features_checked.is_supported("delete-calendar", dict)
        assert observed["support"] == "fragile"
        assert observed["delay"] == 3

    def test_a_worse_verdict_does_not_inherit_the_old_delay(self) -> None:
        """set_feature merges, so a stale `delay` survives an overwrite.

        A timing claim belonging to an observation that has just been displaced
        is fed to _check_observed_delay and complained about against the
        configured write delay — a number nobody measured.
        """
        check, checker = self._check()
        checker._features_checked.set_feature(
            "delete-calendar", {"support": "quirk", "behaviour": "delayed deletion", "delay": 7}
        )

        check.record_worse("delete-calendar", {"support": "fragile", "behaviour": "kept failing"})

        observed = checker.features_checked.is_supported("delete-calendar", dict)
        assert observed["support"] == "fragile"
        assert "delay" not in observed, f"the displaced observation's delay came along: {observed}"
