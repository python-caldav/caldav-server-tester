import caldav
import time
import inspect
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

    def __init__(self, client_obj, debug_mode='logging'):
        self._client_obj = client_obj
        self._features_checked = FeatureSet()
        self._default_calendar = None
        self._checks_run = set()  ## checks that has already been running
        self.expected_features = self._client_obj.features
        self.debug_mode = debug_mode

    def check_all(self):
        classes = [
            obj
            for name, obj in inspect.getmembers(checks, inspect.isclass)
            if obj.__module__ == checks.__name__
            and issubclass(obj, Check)
            and obj is not Check
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
        Remove anything added by the PrepareCalendar check - if 
        """
        if not force:
            test_cal_info = self.expected_features.is_supported('test-calendar.compatibility-tests', return_type=dict)
            if not test_cal_info.get("cleanup", False):
                return
        if self.features_checked.is_supported("create-calendar") and self.features_checked.is_supported("delete-calendar"):
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
                    "csc_simple_task2",
                    "csc_simple_task3",
                    "csc_simple_journal1",
                    "csc_monthly_recurring_event",
                    "csc_monthly_recurring_task",
                    "csc_monthly_recurring_with_exception",
                    "csc_url_check"):
                try:
                    self.calendar.object_by_uid(uid).delete()
                except:
                    try:
                        self.tasklist.object_by_uid(uid).delete()
                    except:
                        try:
                            self.journallist.object_by_uid(uid).delete()
                        except:
                            ## TODO: investigate
                            pass

    def report(self, verbose=False, return_what=str):
        ret = {
            "caldav_version": caldav.__version__,
            "ts": time.time(),
            "name": getattr(self._client_obj, "server_name"),
            "url": str(self._client_obj.url),
            "features": self._features_checked.dotted_feature_set_list(compact=True),
            "error": "Not fully implemnted yet - TODO",
            # "flags_checked": self.flags_checked,
            # "diff1": list(self.diff1),
            # "diff2": list(self.diff2),
        }

        if return_what == "json":
            from json import dumps

            return dumps(ret, indent=4)
        elif return_what == dict:
            return ret
        elif return_what == str:
            raise NotImplementedError(
                "work in progress - can't print out a human-readable report yet"
            )
        else:
            raise NotImplementedError(
                "return types dict, str and 'json' accepted as for now"
            )
