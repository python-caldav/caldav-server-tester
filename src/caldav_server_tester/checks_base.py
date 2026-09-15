import copy
import logging
import time

from caldav.compatibility_hints import write_delay
from caldav.lib.error import DAVError

## Keys that record timing rather than a verdict.  Left out when an observation
## is compared with the profile: a measured delay is never exactly the number
## somebody configured, and _check_observed_delay compares the two properly.
TIMING_KEYS = ("delay", "save-load-delay", "delay-is-lower-bound", "note")

## How much of a configured write delay an observation may take up before the
## configured value is called into question.  A server measured at 9s against a
## 10s setting is not comfortably covered - the setting was somebody's estimate,
## and the next run on a busier day is the one that breaks.
DELAY_MARGIN_RATIO = 0.85


def asynchronous_delay(observed) -> int:
    """The delay in an observed verdict that stands for asynchronous processing, or 0.

    A fragile verdict's delay is a retry window: how long a request kept failing
    before a re-sent one went through (Cyrus answers 500 for ~1s when a
    just-re-created calendar is deleted).  That says nothing about when a
    successful write becomes observable, so it is not a write delay.
    """
    if not isinstance(observed, dict) or observed.get("support") == "fragile":
        return 0
    return observed.get("delay") or 0


## Support levels that say the operation *does* happen, however badly.
## `is_supported()` is True for full and quirk only, and `accept_fragile=True`
## widens it to fragile; neither covers "ungraceful", which means the server
## reports an error and does the thing anyway.  A gate asking "is this still
## worth probing / may we still delete what we made?" wants this question, not
## the plain boolean - a probe that grades a server honestly and thereby
## switches off the probes below it is worse than one that never looked.
EFFECTIVE_STATUSES = frozenset({"full", "quirk", "fragile", "ungraceful"})

## Verdicts from worst to best, for deciding whether a second look at the same
## feature is bad enough news to overwrite the first.  "fragile" ranks below
## "ungraceful" deliberately: an error the client gets every single time can be
## caught every single time, where an operation that works four times out of
## five is the harder thing to write against - and "fragile" is the one level
## caldav's Calendar.delete() reads to switch its retry loop on, so ranking it
## as the milder of the two would suppress the observation that actually
## changes what a client does.  "unknown" is not on the scale at all: it is the
## absence of an observation, and is handled separately in is_worse.
SUPPORT_SEVERITY = ("unsupported", "broken", "fragile", "ungraceful", "quirk", "full")


def takes_effect(features, feature) -> bool:
    """Does `feature` happen on this server, even if unreliably or rudely?"""
    return features.is_supported(feature, str) in EFFECTIVE_STATUSES


def is_worse(candidate, current) -> bool:
    """Is the `candidate` support level worse news than `current`?

    "unknown" is not a grade but the lack of one, so it is not compared on the
    scale: any real observation displaces it, and it never displaces a real
    observation.  A level neither on the scale nor "unknown" is left alone.
    """
    if current == "unknown":
        return candidate in SUPPORT_SEVERITY
    if candidate not in SUPPORT_SEVERITY or current not in SUPPORT_SEVERITY:
        return False
    return SUPPORT_SEVERITY.index(candidate) < SUPPORT_SEVERITY.index(current)


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

        ## Strip all free-text and timing information from both observed and expected
        for stripdict in observed, expected:
            for y in ("behaviour", "description", *TIMING_KEYS):
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

        The `delay` of `synchronous-write` in a server profile is a number written by hand, and the
        only way to learn that it is too small is to measure the server.  Any
        probe that records a `delay` therefore gets it compared against what the
        profile asks a client to sleep, and the complaint goes out through the
        same debug_mode machinery as any other unmet expectation.

        Deliberately placed above the fragile/unknown early return below: that
        return exists because a fragile *support level* is not worth comparing,
        which says nothing about a timing observation carried alongside it.
        The delay of a fragile verdict itself is left out, though: see
        asynchronous_delay().
        """
        observed = fs.is_supported(feature, dict)
        delay = asynchronous_delay(observed)
        if not delay:
            return

        configured = write_delay(self.expected_features)

        if not configured:
            complaint = (
                f"{feature}: observed delay of ~{delay}s, but no synchronous-write delay is configured for this server"
            )
        elif observed.get("delay-is-lower-bound"):
            ## The probe stopped waiting, so the server is slower than this - by
            ## an unknown amount, which no ratio can be computed against.
            complaint = (
                f"{feature}: observed delay is at least ~{delay}s, longer than the probe waited, "
                f"against a configured write delay of {configured}s"
            )
        elif delay / configured > DELAY_MARGIN_RATIO:
            complaint = (
                f"{feature}: observed delay of ~{delay}s takes up more than "
                f"{DELAY_MARGIN_RATIO:.0%} of the configured write delay of {configured}s"
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

    def takes_effect(self, feature) -> bool:
        """Does the server do `feature`, however unreliably or rudely?

        See `takes_effect()` - this is the observed feature set's answer, which
        is what every gate inside a check means.
        """
        return takes_effect(self.checker.features_checked, feature)

    def record_worse(self, feature, verdict) -> bool:
        """Record `verdict` only if it is worse than what is already recorded.

        For a probe that takes a second look at a feature some earlier probe
        has already graded: the second look is there to catch bad news, and
        must not overwrite a harder verdict with an easier one, nor keep the
        `delay` measured alongside the verdict it displaces.

        That second part is why the old node is dropped first: `set_feature`
        *merges* into whatever is already there, so a key the new verdict does
        not carry survives - most damagingly a `delay`, which then belongs to
        an observation that is no longer recorded and gets compared against the
        configured write delay as if it were this one's.  The library offers no
        way to replace a node, hence the reach into it here.
        """
        candidate = verdict.get("support", "full") if isinstance(verdict, dict) else "full"
        if not is_worse(candidate, self.checker.features_checked.is_supported(feature, str)):
            return False
        self.checker._features_checked._server_features.pop(feature, None)
        self.set_feature(feature, verdict)
        return True

    ## How many times a request that may be flaky is asked before the answer is
    ## taken at face value.  Cyrus fails "delete a calendar re-created on a
    ## just-deleted id" 17 times in 20, so one attempt is not enough to clear a
    ## server; three brings a miss below a percent at the price of three
    ## create/delete pairs on a server that has nothing wrong with it.
    RETRY_ATTEMPTS = 3

    def make_calendar_with_retries(self, cal_id, **kwargs):
        """Create a calendar, trying a few times before giving up.

        Returns ``(cal_or_None, attempts_made, last_error)``.  A creation that
        fails and then works on the next ask is a fragility worth reporting
        rather than a failure worth acting on, and one attempt cannot tell the
        two apart - so ask a few times, the way a client has to.

        Lives on Check rather than on the probe that measures create-calendar,
        because every caller that creates a calendar has to survive a server
        the probe grades fragile - the run is aborted by the *caller* that
        cannot, not by the grading.

        Only a refusal that could plausibly go the other way next time is
        retried - see _worth_retrying.
        """
        error = None
        for attempt in range(1, self.RETRY_ATTEMPTS + 1):
            try:
                return self.checker.principal.make_calendar(cal_id=cal_id, **kwargs), attempt, error
            except DAVError as e:
                error = e
                if attempt == self.RETRY_ATTEMPTS or not self._worth_retrying(e):
                    return None, attempt, error
                time.sleep(1)
        return None, self.RETRY_ATTEMPTS, error

    @staticmethod
    def _worth_retrying(error) -> bool:
        """Could this refusal plausibly go the other way on the next ask?

        A 5xx is the server having a bad moment, and 408/429 say as much
        outright.  A 4xx says the request itself is wrong - a read-only account
        answers 403 as often as you like, and 405 means the method is not the
        one this server wants.  An error carrying no status at all never
        reached the network: the library refuses a creation by itself when the
        profile says the server cannot do it.

        The exceptions carry no status code of their own - the library formats
        the whole response into a string and hands it over as the exception's
        ``url`` - so the status line is read off the front of that.
        """
        status = str(getattr(error, "url", "") or "").split(" ", 1)[0]
        if not status.isdigit():
            return False
        return status.startswith("5") or status in ("408", "429")

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
