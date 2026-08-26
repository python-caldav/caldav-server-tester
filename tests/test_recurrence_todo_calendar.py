"""The expanded-todo recurrence probe must search the *task* calendar.

CheckRecurrenceSearch resolves both a calendar and a tasklist, and every other
todo query in it goes to the tasklist.  One `todo=True` search went to the
event calendar instead, so on a server that keeps tasks in a separate
collection (the reason `checker.tasklist` exists at all) the search returned
nothing and `search.recurrences.expanded.todo` was reported unsupported for a
server that supports it perfectly well.
"""

import ast
import inspect

from caldav_server_tester.checks import CheckRecurrenceSearch


def _todo_searches() -> list[str]:
    """Receiver name of every `.search(..., todo=True, ...)` in the check."""
    tree = ast.parse(inspect.getsource(CheckRecurrenceSearch))
    receivers = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "search":
            continue
        todo = any(
            kw.arg == "todo" and isinstance(kw.value, ast.Constant) and kw.value.value is True for kw in node.keywords
        )
        if todo and isinstance(node.func.value, ast.Name):
            receivers.append(node.func.value.id)
    return receivers


class TestTodoSearchesUseTheTasklist:
    def test_there_are_todo_searches_to_check(self) -> None:
        assert _todo_searches(), "probe restructured — this guard needs rewriting"

    def test_every_todo_search_goes_to_the_tasklist(self) -> None:
        assert set(_todo_searches()) == {"tl"}, (
            f"a todo=True search targets {sorted(set(_todo_searches()))}; "
            "on servers with a separate task collection that returns nothing"
        )
