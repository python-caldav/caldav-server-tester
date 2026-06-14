import copy
import inspect
import logging
import time

import caldav
from caldav.compatibility_hints import FeatureSet

from . import checks
from .checks_base import Check


class ServerQuirkChecker:
    """This class will ...

    * Keep the connection details to the server
    * Keep the state of what checks have run
    * Keep the results of all checks that have run
    * Methods for checking all features or a specific feature
    """

    def __init__(self, client_obj, debug_mode="logging", extra_clients=None):
        self._client_obj = client_obj
        self._extra_clients = list(extra_clients or [])
        self._features_checked = FeatureSet()
        self._default_calendar = None
        self._checks_run = set()  ## checks that has already been running
        self.expected_features = self._client_obj.features
        self.principal = self._client_obj.principal()
        self.extra_principals = []
        for ec in self._extra_clients:
            try:
                self.extra_principals.append(ec.principal())
            except Exception:
                pass  ## Skip clients that fail to authenticate or connect
        self.debug_mode = debug_mode

        ## Handle search-cache delay if configured.
        ## NOTE: This is a process-global side effect — Calendar.search is
        ## patched on the class, affecting every Calendar in the process.
        ## The delay value is stored as a class attribute so that subsequent
        ## ServerQuirkChecker constructions with a different delay will update it.
        search_cache_config = self._client_obj.features.is_supported("search-cache", return_type=dict)
        if search_cache_config.get("behaviour") == "delay":
            delay = search_cache_config.get("delay", 1)
            ## Wrap Calendar.search with delay decorator
            from caldav.collection import Calendar

            if not hasattr(Calendar, "_original_search"):
                Calendar._original_search = Calendar.search

                def delayed_search(self, *args, **kwargs):
                    time.sleep(Calendar._search_delay)
                    return Calendar._original_search(self, *args, **kwargs)

                Calendar.search = delayed_search

            Calendar._search_delay = delay

    def check_all(self):
        classes = [
            obj
            for name, obj in inspect.getmembers(checks, inspect.isclass)
            if obj.__module__ == checks.__name__ and issubclass(obj, Check) and obj is not Check
        ]
        for cl in classes:
            cl(self).run_check(only_once=True)

    def check_one(self, check_name):
        check = getattr(checks, check_name)(self)
        check.run_check()

    @property
    def features_checked(self):
        return self._features_checked

    ## Probe objects PrepareCalendar deliberately PUT far in the past (year 2000).
    ## Sliding-window servers (e.g. OX) hide these from listings/REPORT, so they
    ## must be removed by direct URL rather than discovered via objects().
    HIDDEN_PROBE_UIDS = ("csc_olddate_event", "csc_olddate_task")

    def _purge_csc_objects(self, calendars):
        """Delete every ``csc_*`` object from the given calendars.

        Visible objects are found via ``objects()`` listing; the year-2000
        probes that a sliding window hides are additionally deleted by direct
        URL.  Returns a ``(removed, errors)`` tuple: ``removed`` is the number of
        objects deleted, ``errors`` is the number of listing/deletion failures
        (each also logged as a warning).  Callers that report cleanup success to
        the user must not claim success when ``errors`` is non-zero — otherwise a
        server answering e.g. 403 to ``objects()`` looks like "nothing to clean".
        Failures of the direct-URL probe load are NOT counted: an absent probe
        (the common case) legitimately fails to load.
        """
        import caldav

        removed = 0
        errors = 0
        seen = set()
        for cal in calendars:
            if cal is None or id(cal) in seen:
                continue
            seen.add(id(cal))
            ## listing-based: catches all visible csc_* objects
            try:
                for obj in cal.objects():
                    try:
                        uid = str(obj.icalendar_component.get("UID", ""))
                        if uid.startswith("csc_"):
                            obj.delete()
                            removed += 1
                    except Exception as exc:
                        errors += 1
                        logging.warning("Failed to delete a csc_* object: %s", exc)
            except Exception as exc:
                errors += 1
                logging.warning("Failed to list objects while purging test data: %s", exc)
            ## direct-URL: catches probes hidden from listing by a sliding window.
            ## load() first so we only count (and DELETE) probes that actually
            ## exist - a DELETE to an absent URL succeeds silently on some servers.
            for uid in self.HIDDEN_PROBE_UIDS:
                try:
                    obj = caldav.Event(cal.client, url=cal.url.join(uid + ".ics"), parent=cal)
                    obj.load()
                    obj.delete()
                    removed += 1
                except Exception:
                    pass
        return removed, errors

    def cleanup(self, force=True):
        """
        Remove anything added by the PrepareCalendar check.

        force=True (default): always clean up.
        force=False: only clean up if 'test-calendar.compatibility-tests' config has cleanup=True.

        The main calendar is deleted wholesale ONLY when PrepareCalendar created
        it (``calendar_was_created``).  When ``--caldav-calendar`` points the tool
        at a pre-existing user calendar, that calendar is never deleted; only its
        ``csc_*`` fixtures are purged.  The dedicated task/journal sibling
        calendars are always ones the tool created, so they are deleted wholesale
        when the server supports it.
        """
        if not hasattr(self, "calendar"):
            return  ## PrepareCalendar never ran; nothing to clean up

        if not force:
            test_cal_info = self.expected_features.is_supported("test-calendar.compatibility-tests", return_type=dict)
            if not test_cal_info.get("cleanup", False):
                return

        can_delete_calendars = self.features_checked.is_supported(
            "create-calendar"
        ) and self.features_checked.is_supported("delete-calendar")
        ## Default to False (the safe choice) when PrepareCalendar didn't record
        ## ownership: never delete a calendar we can't prove we created.
        calendar_was_created = getattr(self, "calendar_was_created", False)

        purge_targets = []
        if can_delete_calendars and calendar_was_created:
            self.calendar.delete()
        else:
            purge_targets.append(self.calendar)

        ## tasklist/journallist are separate calendars only when PrepareCalendar
        ## created them itself, so deleting those wholesale is always safe.
        for sibling in (self.tasklist, self.journallist):
            if sibling is self.calendar:
                continue
            if can_delete_calendars:
                sibling.delete()
            else:
                purge_targets.append(sibling)

        if purge_targets:
            self._purge_csc_objects(purge_targets)

    def cleanup_test_data(self, calendar_name=None):
        """Purge caldav-server-tester fixtures without running any checks.

        Used by the CLI ``--cleanup-only`` flag to remove fixtures that have aged
        out of a sliding window (the near-future fixtures shift forward each
        calendar year, so leftovers from previous years accumulate when the
        ``--no-cleanup`` reuse feature is used).  Locates the test calendar and
        its task/journal siblings by id and display name and deletes every
        ``csc_*`` object found, including the hidden year-2000 probes.

        Returns a ``(removed, errors)`` tuple (see ``_purge_csc_objects``).
        """
        cal_id = "caldav-server-checker-calendar"
        candidates = []

        ## by calendar id (and the dedicated task/journal siblings)
        for cid in (cal_id, f"{cal_id}_tasks", f"{cal_id}_journals"):
            try:
                c = self.principal.calendar(cal_id=cid)
                c.get_display_name()  ## force a request so non-existent calendars raise
                candidates.append(c)
            except Exception:
                pass

        ## by display name
        wanted_names = {n for n in (calendar_name, "Calendar for checking server feature support") if n}
        try:
            for c in self.principal.calendars():
                try:
                    if c.get_display_name() in wanted_names:
                        candidates.append(c)
                except Exception:
                    pass
        except Exception:
            pass

        return self._purge_csc_objects(candidates)

    def _get_deviating_features(self) -> dict:
        """Return observed features where support differs from the spec default.

        The default for each feature comes from FeatureSet.FEATURES[feature]['default'].
        Features with no explicit default are assumed to be "full" (standard CalDAV compliance).
        """
        all_observed = self._features_checked.dotted_feature_set_list(compact=False)
        deviating = {}
        for feature, info in all_observed.items():
            obs_support = info.get("support", "unknown")
            feature_default = FeatureSet.FEATURES.get(feature, {}).get("default", {})
            default_support = feature_default.get("support", "full") if isinstance(feature_default, dict) else "full"
            if obs_support != default_support:
                deviating[feature] = info
        return deviating

    def _compute_diff(self) -> dict:
        """Compare expected (configured) features against observed features.

        Returns a dict mapping feature name to {"expected": ..., "observed": ...}
        for every feature where the support level differs.
        """
        observed = self._features_checked.dotted_feature_set_list(compact=False)
        expected_all = self.expected_features.dotted_feature_set_list(compact=False)
        diff = {}
        all_keys = set(observed) | set(expected_all)
        for key in all_keys:
            obs_support = observed.get(key, {}).get("support", "unknown")
            exp_support = self.expected_features.is_supported(key, str)
            if obs_support != exp_support:
                diff[key] = {"expected": exp_support, "observed": obs_support}
        return diff

    def report(self, verbose=False, show_diff=False, return_what=str):
        diff = self._compute_diff() if show_diff else None
        ## compact=True collapses sibling sub-features into their parent and
        ## PERMANENTLY mutates the feature set it runs on (dropping per-child
        ## behaviour notes).  Run it on a throwaway copy so the lossless branches
        ## below (hints, verbose text, _get_deviating_features) and any later
        ## caller still see the full, un-collapsed data.
        features = copy.deepcopy(self._features_checked).dotted_feature_set_list(compact=True)
        ret = {
            "caldav_version": caldav.__version__,
            "ts": time.time(),
            "name": getattr(self._client_obj, "server_name", "(noname)"),
            "url": str(self._client_obj.url),
            "features": features,
        }
        if show_diff:
            ret["diff"] = diff

        if return_what == "json":
            from json import dumps

            return dumps(ret, indent=4)
        elif return_what == "yaml":
            import yaml

            return yaml.dump(ret, default_flow_style=False, allow_unicode=True)
        elif return_what == "hints":
            ## Output as a Python dict literal suitable for pasting into compatibility_hints.py
            ## Use compact=False to include all observed features, even those with full support
            all_features = self._features_checked.dotted_feature_set_list(compact=False)
            lines = ["{"]
            for feature, info in sorted(all_features.items()):
                lines.append(f"    {feature!r}: {info!r},")
            lines.append("}")
            return "\n".join(lines)
        elif return_what == dict:
            return ret
        elif return_what == str:
            lines = [
                f"Server: {ret['name']} ({ret['url']})",
                f"caldav library version: {ret['caldav_version']}",
                "",
                "Feature compatibility (non-verbose: showing only deviations from the standard):"
                if not verbose
                else "Feature compatibility:",
            ]
            display_features = (
                self._get_deviating_features()
                if not verbose
                else self._features_checked.dotted_feature_set_list(compact=False)
            )
            lines.append("")
            for feature, info in sorted(display_features.items()):
                support = info.get("support", "?")
                extras = {k: v for k, v in info.items() if k != "support"}
                extra_str = "  " + "  ".join(f"{k}={v}" for k, v in extras.items()) if extras else ""
                description = FeatureSet.FEATURES.get(feature, {}).get("description", "")

                lines.append(f"## {feature}")
                lines.append(f"Feature support level found: {support}")
                if extras:
                    lines.append("Extra check information:")
                    lines.append(extra_str)
                if description:
                    lines.append(f"Description of the feature: {description}")
                lines.append("")
            if not display_features:
                lines.append("  (no issues detected)" if not verbose else "  (no features checked)")

            if show_diff:
                lines.append("Diff (expected vs observed):" if diff else "Diff: no deviations from expectations")
                for feature, change in sorted(diff.items()):
                    lines.append(f"  {feature}: expected={change['expected']}  observed={change['observed']}")

            return "\n".join(lines)
        else:
            raise NotImplementedError("return types accepted: dict, str, 'json', 'yaml', 'hints'")
