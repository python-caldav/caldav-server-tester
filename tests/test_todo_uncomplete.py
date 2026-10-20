"""A completed task made pending again.

GMX was found keeping a task completed after Todo.uncomplete() saved it back
with STATUS:NEEDS-ACTION and without COMPLETED.
"""

from datetime import datetime, timezone
from unittest.mock import Mock

import icalendar
import pytest
from caldav.compatibility_hints import FeatureSet
from caldav.lib.error import AuthorizationError, DAVError, PutError

from caldav_server_tester.checks import CheckTodoUncomplete

FEATURE = "save-load.todo.uncomplete"
URL = "http://example.com/cal/csc_uncomplete_task.ics"


def _completed():
    todo = icalendar.Todo()
    todo.add("uid", "csc_uncomplete_task")
    todo.add("status", "COMPLETED")
    todo.add("completed", datetime(2027, 1, 1, tzinfo=timezone.utc))
    return todo


def _pending():
    todo = icalendar.Todo()
    todo.add("uid", "csc_uncomplete_task")
    todo.add("status", "NEEDS-ACTION")
    return todo


def _run(loaded=None, add_todo=None, uncomplete=None, complete=None, tasklist=True, observed=None):
    checker = Mock()
    checker._features_checked = FeatureSet(observed)
    checker.features_checked = checker._features_checked
    checker.debug_mode = None
    task = Mock()
    task.icalendar_component = _completed()

    def _load():
        task.icalendar_component = loaded() if loaded else _pending()

    task.load.side_effect = _load
    if uncomplete is not None:
        task.uncomplete.side_effect = uncomplete
    if complete is not None:
        task.complete.side_effect = complete
    if not tasklist:
        checker.tasklist = None
    elif add_todo is not None:
        checker.tasklist.add_todo.side_effect = add_todo
    else:
        checker.tasklist.add_todo.return_value = task
    CheckTodoUncomplete(checker)._run_check()
    return checker, task, checker._features_checked.is_supported(FEATURE, dict)


def test_the_task_is_completed_the_way_a_client_does_it():
    """Stored pending, then completed: Bedework 5 refuses a new task that is
    already STATUS:COMPLETED with 403, while completing a stored one works."""
    checker, task, _ = _run()
    kwargs = checker.tasklist.add_todo.call_args.kwargs
    assert "status" not in kwargs
    assert kwargs["uid"].startswith("csc_uncomplete_task_")
    task.complete.assert_called_once()


def test_a_fresh_uid_per_run():
    """A task left over by a crashed run must not make the next one fail."""
    uids = {_run()[0].tasklist.add_todo.call_args.kwargs["uid"] for _ in range(2)}
    assert len(uids) == 2


def test_pending_after_reload_is_full_and_cleaned_up():
    _, task, observed = _run()
    assert observed["support"] == "full"
    task.uncomplete.assert_called_once()
    task.delete.assert_called_once()


def test_still_completed_after_reload_is_unsupported():
    _, task, observed = _run(loaded=_completed)
    assert observed["support"] == "unsupported"
    task.delete.assert_called_once()


def test_completed_timestamp_kept_is_unsupported():
    def _half():
        todo = _completed()
        todo.pop("status")
        todo.add("status", "NEEDS-ACTION")
        return todo

    _, _, observed = _run(loaded=_half)
    assert observed["support"] == "unsupported"
    assert "COMPLETED" in observed["behaviour"]


@pytest.mark.parametrize(
    "error",
    [PutError("400 Bad Request"), AuthorizationError(url=URL, reason="Forbidden")],
    ids=["400", "401/403"],
)
def test_a_refused_uncomplete_is_ungraceful(error):
    _, task, observed = _run(uncomplete=error)
    assert observed["support"] == "ungraceful"
    task.delete.assert_called_once()


def test_an_outage_during_uncomplete_is_unknown():
    _, _, observed = _run(uncomplete=PutError("503 Service Unavailable"))
    assert observed["support"] == "unknown"


def test_a_completed_task_that_cannot_be_stored_is_unknown():
    """Nothing about uncompleting was observed."""
    _, _, observed = _run(add_todo=DAVError("500 Internal Server Error"))
    assert observed["support"] == "unknown"


def test_a_task_that_cannot_be_completed_is_unknown():
    _, task, observed = _run(complete=DAVError("403 Forbidden"))
    assert observed["support"] == "unknown"
    task.delete.assert_called_once()


def test_no_task_calendar_is_unknown():
    _, _, observed = _run(tasklist=False)
    assert observed["support"] == "unknown"


def test_a_server_without_known_task_support_is_not_probed():
    """Bedework: no CalDAV client can make a task list there, so save-load.todo
    is unknown - and so is this, by inheritance rather than by a failed PUT."""
    checker, _, observed = _run(observed={"save-load.todo": {"support": "unknown"}})
    checker.tasklist.add_todo.assert_not_called()
    assert observed["support"] == "unknown"
