#!/usr/bin/env python
"""A feature that could not be probed must stay 'unknown', never 'unsupported'.

When a check raises - a dropped connection, a server that went away mid-run -
``run_check()`` marks its features 'unknown'.  Dependent checks used to ask
``feature_checked(parent)``, which is False for 'unknown' exactly as it is for
'unsupported', and then recorded their own features as *unsupported*.  A
transient outage therefore became an authoritative "this server does not
support scheduling", which is what a compatibility comparison then fails on.
"""

import pytest
from caldav.compatibility_hints import FeatureSet

from caldav_server_tester.checks import (
    CheckFreeBusyQueryRFC6638,
    CheckScheduleTag,
    CheckSchedulingDetails,
)


class _FakeChecker:
    """Just enough checker for a Check to record features against."""

    def __init__(self, observed):
        self._features_checked = FeatureSet(observed)
        self._checks_run = set()
        self._client_obj = None
        self.principal = None
        self.extra_principals = []
        self.debug_mode = "logging"


def _run(check_cls, observed):
    checker = _FakeChecker(observed)
    check = check_cls(checker)
    check.expected_features = FeatureSet()
    check._run_check()
    return checker._features_checked


@pytest.mark.parametrize(
    "check_cls,feature",
    [
        (CheckSchedulingDetails, "scheduling.mailbox"),
        (CheckSchedulingDetails, "scheduling.calendar-user-address-set"),
        (CheckFreeBusyQueryRFC6638, "scheduling.freebusy-query"),
        (CheckScheduleTag, "scheduling.schedule-tag"),
    ],
)
def test_unprobed_parent_leaves_children_unknown(check_cls, feature):
    """An unprobed parent must not be reported as 'unsupported' downstream."""
    observed = _run(check_cls, {"scheduling": {"support": "unknown"}})
    assert observed.is_supported(feature, str) == "unknown"


@pytest.mark.parametrize(
    "check_cls,feature",
    [
        (CheckSchedulingDetails, "scheduling.mailbox"),
        (CheckSchedulingDetails, "scheduling.calendar-user-address-set"),
        (CheckFreeBusyQueryRFC6638, "scheduling.freebusy-query"),
        (CheckScheduleTag, "scheduling.schedule-tag"),
    ],
)
def test_genuinely_unsupported_parent_still_propagates(check_cls, feature):
    """A parent that really was probed and found missing still shuts them off."""
    observed = _run(check_cls, {"scheduling": {"support": "unsupported"}})
    assert observed.is_supported(feature, str) == "unsupported"
