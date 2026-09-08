import copy
import logging
import time

from caldav.lib.error import DAVError

## How much of a configured write-delay an observation may take up before the
## configured value is called into question.  A server measured at 9s against a
## 10s setting is not comfortably covered - the setting was somebody's estimate,
## and the next run on a busier day is the one that breaks.
DELAY_MARGIN_RATIO = 0.85


class Check:
    """
    A "check" may check zero, one or multiple features, as listed in
    caldav.compatibility_hints.FeatureSet.FEATURES.

    A "check" may provision test data for other checks.

    Every check has it's own class.  This is the base class.
    """

    depends_on = set()

    def __init__(self, checker):
        self.checker = checker
        self.client = checker._client_obj

    def set_feature(self, feature, value=True):
        fs = self.checker._features_checked
        fs.set_feature(feature, value)

        ## verifying that the expectations are met.

        ## We skip this if debug_mode is None
        if self.checker.debug_mode is None:
            return

        feat_def = self.checker._features_checked.find_feature(feature)
        feat_type = feat_def.get("type", "server-feature")

        if feat_type not in ("server-peculiarity", "server-feature"):
            ## client-behaviour, tests-behaviour or client-feature
            ## cannot be checked for reliably (and is not supposed to
            ## be checked by the script).  server-observation is unreliable.
            if feat_type not in ("server-observation",):
                logging.error("Unexpected feature type %r for feature %r", feat_type, feature)
            return

        self._check_observed_delay(feature, fs)

        value_str = fs.is_supported(feature, str)

        ## Fragile support is ... fragile and should be ignored
        ## same with unknown
        if value_str in ("fragile", "unknown") or self.expected_features.is_supported(feature, str) in (
            "fragile",
            "unknown",
        ):
            return

        expected_ = self.expected_features.is_supported(feature, dict)
        expected = copy.deepcopy(expected_)
        observed_ = fs.is_supported(feature, dict)
        observed = copy.deepcopy(observed_)

        ## Strip all free-text information from both observed and expected
        for stripdict in observed, expected:
            for y in ("behaviour", "description"):
                if y in stripdict:
                    stripdict.pop(y)

        if self.checker.debug_mode == "assert":
            assert observed == expected
            return

        if observed != expected:
            if self.checker.debug_mode == "logging":
                logging.error(
                    f"Server checker found something unexpected for {feature}.  Expected: {expected_}, observed: {observed_}"
                )
            elif self.checker.debug_mode == "pdb":
                breakpoint()
            else:
                raise ValueError(f"Unknown debug_mode {self.checker.debug_mode!r}")

    def _check_observed_delay(self, feature, fs):
        """Complain when a measured delay outgrows the configured one.

        A `write-delay` in a server profile is a number written by hand, and the
        only way to learn that it is too small is to measure the server.  Any
        probe that records a `delay` therefore gets it compared against what the
        profile asks a client to sleep, and the complaint goes out through the
        same debug_mode machinery as any other unmet expectation.

        Deliberately placed above the fragile/unknown early return below: that
        return exists because a fragile *support level* is not worth comparing,
        which says nothing about a timing observation carried alongside it.
        """
        observed = fs.is_supported(feature, dict)
        if not isinstance(observed, dict):
            return
        delay = observed.get("delay") or 0
        if not delay:
            return

        write_delay = self.expected_features.is_supported("write-delay", dict)
        configured = write_delay.get("delay", 0) if write_delay.get("behaviour") == "delay" else 0

        if not configured:
            complaint = f"{feature}: observed delay of ~{delay}s, but no write-delay is configured for this server"
        elif observed.get("delay-is-lower-bound"):
            ## The probe stopped waiting, so the server is slower than this - by
            ## an unknown amount, which no ratio can be computed against.
            complaint = (
                f"{feature}: observed delay is at least ~{delay}s, longer than the probe waited, "
                f"against a configured write-delay of {configured}s"
            )
        elif delay / configured > DELAY_MARGIN_RATIO:
            complaint = (
                f"{feature}: observed delay of ~{delay}s takes up more than "
                f"{DELAY_MARGIN_RATIO:.0%} of the configured write-delay of {configured}s"
            )
        else:
            return

        if self.checker.debug_mode == "assert":
            raise AssertionError(complaint)
        if self.checker.debug_mode == "pdb":
            logging.error(complaint)
            breakpoint()
        else:
            logging.error(complaint)

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

    def _poll_calendar(self, cal_id=None, cal=None, until_accessible=True, timeout=10):
        """Poll a calendar until it materialises (or disappears).

        Returns ``(cal_or_None, waited_seconds)`` - the calendar when it is
        accessible at the end of the poll, ``None`` when it is not, either way
        with the number of seconds spent waiting.

        Some servers process writes asynchronously (Infomaniak/SabreDAV):
        MKCALENDAR and DELETE both return before the change is queryable, so a
        newly created collection 404s for a few seconds and a deleted one keeps
        answering for a few seconds.  Anything that creates or deletes a calendar
        and immediately uses the result has to wait here - otherwise it both
        mis-probes the server AND leaks the calendar (it does get created
        server-side; we just never wait around to use or delete it).

        Pass ``cal_id`` to re-resolve the calendar on every iteration (right when
        waiting for a collection to appear under a known id), or ``cal`` to poll
        an object we already hold - a calendar handed back by make_calendar()
        need not live at the requested cal_id at all (Zimbra returns an opaque
        cal://0/NNN URL; see create-calendar.stable-url), so re-resolving it
        would poll the wrong URL.

        Exactly one accessibility probe is issued per iteration.
        """
        assert (cal_id is None) != (cal is None), "pass exactly one of cal_id / cal"
        waited = 0
        while True:
            probe = cal if cal is not None else self.checker.principal.calendar(cal_id=cal_id)
            accessible = self._calendar_is_accessible(probe)
            if accessible == until_accessible or waited >= timeout:
                return (probe if accessible else None), waited
            time.sleep(1)
            waited += 1

    def feature_check_result(self, feature, return_type=bool):
        """The value we've found for the feature through checking -
        as opposed to the configured value.
        """
        return self.checker._features_checked.is_supported(feature, return_type)

    ## The method above was earlier named `feature_checked()`, but
    ## I read that as "was the feature checked or not?", so not
    ## good.  Adding this for backward compatibility:
    feature_checked = feature_check_result

    ## The AI suggested "feature_unprobed", but I found it silly
    def feature_undeterminated(self, feature) -> bool:
        """True when the we don't know the state.

        * None: check has not been run
        * "unknown": check has been run, but could not probe it
        * "fragile": check has been run, and the results from it is inconclusive
        """
        return self.feature_checked(feature, str) in (None, "unknown", "fragile")

    ## Inspired by "doublespeak" ... but I think this is a good method name:
    def feature_ungood(self, feature) -> bool:
        """Returns true on non-good states

        Both unknown, not probed, flaky and unsupported is considered
        "ungood" here.  It's often used in the checks to check a
        parent feature - a child would typically depend on a parent,
        so if the parent feature is "ungood", it's often no point
        trying to probe the children.

        """
        return self.feature_undeterminated(feature) or not self.feature_check_result(feature)

    def run_check(self, only_once=True):
        if only_once:
            if self.__class__ in self.checker._checks_run:
                return
        for foo in self.depends_on:
            foo(self.checker).run_check(only_once=only_once)

        keys_before = set(self.checker._features_checked.dotted_feature_set_list().keys())

        ## expected_features is the preconfigured feature set for this server.
        self.expected_features = self.checker._client_obj.features
        try:
            ## we should blank out the non-checked features -
            ## otherwise various workarounds may be invoked in the
            ## code, and we'll check nothing
            self.checker._client_obj.features = self.checker._features_checked
            self._run_check()
        except (AssertionError, NotImplementedError, RuntimeError):
            ## RuntimeError is how a check reports a *configuration* problem the
            ## user has to act on - PrepareCalendar raises it when no test
            ## calendar exists and the server will not create one, telling the
            ## user to pass --caldav-calendar.  Swallowing it leaves
            ## checker.calendar unset, so every dependent check fails too and
            ## the actionable message is buried under a report of "unknown".
            raise
        except Exception as exc:
            logging.warning(
                "%s raised an unexpected exception — marking unprobed features as unknown. Error: %s",
                self.__class__.__name__,
                exc,
            )
            ## Ensure the post-check assert below passes by filling in missing features
            keys_so_far = set(self.checker._features_checked.dotted_feature_set_list().keys())
            for feature in self.features_to_be_checked - (keys_so_far - keys_before):
                self.set_feature(feature, {"support": "unknown"})
        finally:
            self.checker._client_obj.features = self.expected_features

        ## Check that all the declared checking has been done
        keys_after = set(self.checker._features_checked.dotted_feature_set_list().keys())
        new_keys = keys_after - keys_before
        missing_keys = self.features_to_be_checked - new_keys
        parent_keys = set()

        ## Missing keys aren't missing if their parents are included.
        ## feature.subfeature.* gets collapsed to feature.subfeature
        to_remove = set()
        for missing in missing_keys:
            feature_ = missing
            while "." in feature_:
                feature_ = feature_[: feature_.rfind(".")]
                if feature_ in keys_after:
                    to_remove.add(missing)
                    parent_keys.add(feature_)
                    break
        missing_keys -= to_remove
        assert not missing_keys, f"{self.__class__.__name__} failed to check declared features: {missing_keys}"

        ## Everything checked should be declared
        extra_keys = new_keys - self.features_to_be_checked
        extra_keys -= {x for x in extra_keys if any(x.startswith(y) for y in parent_keys)}
        assert not extra_keys, f"{self.__class__.__name__} checked undeclared features: {extra_keys}"

        self.checker._checks_run.add(self.__class__)

    def _run_check(self):
        raise NotImplementedError(f"A subclass {self.__class__} hasn't implemented the _run_check method")
