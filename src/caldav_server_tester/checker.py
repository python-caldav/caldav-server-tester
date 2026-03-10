import inspect
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

    def __init__(self, client_obj, debug_mode="logging"):
        self._client_obj = client_obj
        self._features_checked = FeatureSet()
        self._default_calendar = None
        self._checks_run = set()  ## checks that has already been running
        self.expected_features = self._client_obj.features
        self.principal = self._client_obj.principal()
        self.debug_mode = debug_mode

        ## Handle search-cache delay if configured
        search_cache_config = self._client_obj.features.is_supported("search-cache", return_type=dict)
        if search_cache_config.get("behaviour") == "delay":
            delay = search_cache_config.get("delay", 1)
            ## Wrap Calendar.search with delay decorator
            from caldav.collection import Calendar

            if not hasattr(Calendar, "_original_search"):
                Calendar._original_search = Calendar.search

                def delayed_search(self, *args, **kwargs):
                    time.sleep(delay)
                    return Calendar._original_search(self, *args, **kwargs)

                Calendar.search = delayed_search

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

    def cleanup(self, force=True):
        """
        Remove anything added by the PrepareCalendar check.

        force=True (default): always clean up.
        force=False: only clean up if 'test-calendar.compatibility-tests' config has cleanup=True.
        """
        if not hasattr(self, "calendar"):
            return  ## PrepareCalendar never ran; nothing to clean up

        if not force:
            test_cal_info = self.expected_features.is_supported("test-calendar.compatibility-tests", return_type=dict)
            if not test_cal_info.get("cleanup", False):
                return
        if self.features_checked.is_supported("create-calendar") and self.features_checked.is_supported(
            "delete-calendar"
        ):
            self.calendar.delete()
            if self.tasklist != self.calendar:
                self.tasklist.delete()
            if self.journallist != self.calendar:
                self.journallist.delete()
        else:
            for uid in (
                "csc_simple_task1",
                "csc_simple_event1",
                "csc_simple_event2",
                "csc_simple_event3",
                "csc_simple_event4",
                "csc_event_with_categories",
                "csc_event_with_class",
                "csc_event_with_duration",
                "csc_simple_task2",
                "csc_simple_task3",
                "csc_simple_journal1",
                "csc_monthly_recurring_event",
                "csc_monthly_recurring_task",
                "csc_monthly_recurring_with_exception",
                "csc_recurring_count_task",
                "csc_url_check",
            ):
                try:
                    self.calendar.object_by_uid(uid).delete()
                except:
                    try:
                        self.tasklist.object_by_uid(uid).delete()
                    except:
                        try:
                            self.journallist.object_by_uid(uid).delete()
                        except:
                            pass

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
            exp_support = expected_all.get(key, {}).get("support", "unknown")
            if obs_support != exp_support:
                diff[key] = {"expected": exp_support, "observed": obs_support}
        return diff

    def report(self, verbose=False, show_diff=False, return_what=str):
        features = self._features_checked.dotted_feature_set_list(compact=True)
        ret = {
            "caldav_version": caldav.__version__,
            "ts": time.time(),
            "name": getattr(self._client_obj, "server_name", "(noname)"),
            "url": str(self._client_obj.url),
            "features": features,
        }
        if show_diff:
            ret["diff"] = self._compute_diff()

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
            support_marker = {
                "full": "[ok]      ",
                "unsupported": "[no]      ",
                "quirk": "[quirk]   ",
                "fragile": "[fragile] ",
                "broken": "[broken]  ",
                "ungraceful": "[error]   ",
            }
            lines = [
                f"Server: {ret['name']} ({ret['url']})",
                f"caldav library version: {ret['caldav_version']}",
                "",
                "Feature compatibility (non-verbose: showing only deviations from expected):"
                if not verbose
                else "Feature compatibility:",
            ]
            display_features = (
                self._get_deviating_features()
                if not verbose
                else self._features_checked.dotted_feature_set_list(compact=False)
            )
            for feature, info in sorted(display_features.items()):
                support = info.get("support", "?")
                marker = support_marker.get(support, f"[{support}]  ")
                extras = {k: v for k, v in info.items() if k != "support"}
                extra_str = "  " + "  ".join(f"{k}={v}" for k, v in extras.items()) if extras else ""
                lines.append(f"  {marker} {feature}{extra_str}")
                description = FeatureSet.FEATURES.get(feature, {}).get("description", "")
                if description:
                    lines.append(f"             {description}")
            if not display_features:
                lines.append("  (no issues detected)" if not verbose else "  (no features checked)")

            if show_diff:
                diff = self._compute_diff()
                lines.append("")
                lines.append("Diff (expected vs observed):" if diff else "Diff: no deviations from expectations")
                for feature, change in sorted(diff.items()):
                    lines.append(f"  {feature}: expected={change['expected']}  observed={change['observed']}")

            return "\n".join(lines)
        else:
            raise NotImplementedError("return types accepted: dict, str, 'json', 'yaml', 'hints'")
