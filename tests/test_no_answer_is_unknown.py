"""A failed request that got no answer from the server is no verdict.

``DAVError`` is the base of every error the caldav library raises, so a probe
catching it also catches a gateway error (502, 504), an overloaded server
(503), rate limiting, a stale-session 401 and a dropped connection.  None of
them say anything about the feature probed, and this suite's verdicts are
copied into server profiles: a server having a bad minute must not get an
authoritative claim written about it.  ``require_answer()`` re-raises such an
error, and ``Check.run_check`` records what the check had not probed yet as
unknown.

The AST test at the bottom keeps every grading DAVError handler in
``checks.py`` going through that classification.
"""

import ast
import inspect
from pathlib import Path
from unittest.mock import Mock

import caldav.lib.error as caldav_error
import pytest
from caldav.compatibility_hints import FeatureSet
from caldav.lib.error import (
    AuthorizationError,
    DAVError,
    NotFoundError,
    PropfindError,
    PutError,
    RateLimitError,
    ReportError,
    ResponseError,
)

from caldav_server_tester.checker import ServerQuirkChecker
from caldav_server_tester.checks import CheckPropfindAllprop, require_answer

URL = "https://dav.example.com/p/"

NO_ANSWER = [
    PutError("502 Bad Gateway"),
    ReportError("503 Service Unavailable"),
    PropfindError("504 Gateway Timeout"),
    RateLimitError(reason="429 Too Many Requests"),
    AuthorizationError(url=URL, reason="Unauthorized"),
    ConnectionError("connection reset by peer"),
    ## caldav >= 3.7 keeps the status: a 401 whatever its reason phrase says
    AuthorizationError(url=URL, reason="None given", status=401),
    ResponseError("HTTP/1.1 503 Service Unavailable", status=503),
    ResponseError("HTTP/1.1 502 Bad Gateway"),
]
NO_ANSWER_IDS = ["502", "503", "504", "rate-limited", "401", "connection", "401-status", "503-status", "502-http"]

ANSWERS = [
    PutError("400 Bad Request"),
    ReportError("403 Forbidden"),
    AuthorizationError(url=URL, reason="Forbidden"),
    ReportError("500 Internal Server Error"),
    PutError("501 Not Implemented"),
    NotFoundError("no such calendar"),
    AuthorizationError(url=URL, reason="Unauthorized", status=403),
    ResponseError("HTTP/1.1 500 Internal Server Error", status=500),
    ResponseError("HTTP/1.1 507 Insufficient Storage"),
    ReportError("calendar-query answered 500", status=500),
    ## caldav raises these without a status: the server answered, but not
    ## with what the client could use, or the client refused it
    DAVError("no iCalendar data from the server"),
    ReportError("Sync tokens are not supported"),
]
ANSWER_IDS = [
    "400",
    "403",
    "403-auth",
    "500",
    "501",
    "404-without-status",
    "403-status",
    "500-status",
    "507-http",
    "500-own",
    "no-status",
    "no-status-report",
]


@pytest.mark.parametrize("error", NO_ANSWER, ids=NO_ANSWER_IDS)
def test_require_answer_reraises_no_answer(error) -> None:
    with pytest.raises(type(error)) as raised:
        require_answer(error)
    assert raised.value is error


@pytest.mark.parametrize("error", ANSWERS, ids=ANSWER_IDS)
def test_require_answer_passes_an_answer(error) -> None:
    assert require_answer(error) is error


def _response(status, raw=b""):
    response = Mock()
    response.status = status
    response.raw = raw
    return response


def _run_principal_depth1(depth1):
    client = Mock()
    client.features = FeatureSet()
    checker = ServerQuirkChecker(client, debug_mode=None)
    checker.principal = Mock()
    checker.principal.url = URL

    def propfind(url, props=None, depth=0):
        if depth == 1:
            if isinstance(depth1, Exception):
                raise depth1
            return depth1
        return _response(207, b'<D:multistatus xmlns:D="DAV:"><D:response/></D:multistatus>')

    client.propfind.side_effect = propfind
    CheckPropfindAllprop(checker).run_check()
    return checker._features_checked.is_supported("propfind.principal-depth-1", str)


@pytest.mark.parametrize("error", NO_ANSWER, ids=NO_ANSWER_IDS)
def test_principal_depth1_without_an_answer_is_unknown(error) -> None:
    assert _run_principal_depth1(error) == "unknown"


@pytest.mark.parametrize("status", [429, 502, 503, 504])
def test_principal_depth1_answered_by_a_gateway_is_unknown(status) -> None:
    assert _run_principal_depth1(_response(status)) == "unknown"


@pytest.mark.parametrize("error", ANSWERS, ids=ANSWER_IDS)
def test_principal_depth1_refused_is_unsupported(error) -> None:
    assert _run_principal_depth1(error) == "unsupported"


## --- every grading handler goes through the classification ---

CHECKS = Path(inspect.getfile(require_answer))

## A 404 and a UID conflict are answers by construction.
ALWAYS_ANSWERS = {"NotFoundError"} | {n for n in vars(caldav_error) if n.startswith("UidConflict")}
CAUGHT = {
    name
    for name, cls in vars(caldav_error).items()
    if inspect.isclass(cls) and issubclass(cls, DAVError) and name not in ALWAYS_ANSWERS
} | {"Exception", "BaseException"}

CLASSIFIERS = {"require_answer", "refusal_status", "graded_refusal", "_deviation_or_outage"}

## Handlers left as they are, by function name, each with the reason.  Kept
## short: an entry is a place an outage could still become a verdict.
EXEMPT = {
    ## The DELETE grading polls and retries; whether an outage there may end
    ## the check, which every other check depends on, is still open
    ## (docs/TODO.md).
    "_probe_delete",
    ## Keeps the last error for an unknown; the verdict comes from a control
    ## creation on a fresh cal_id, which an outage fails too.
    "_probe_free_namespace",
    ## A status means the PROPFIND failed (None, no reading); no status is
    ## caldav's way of saying the property is absent.
    "_reported",
    ## Grades a 4xx as refused and a server failure by its status; anything
    ## else, a gateway or overload included, is None.
    "_store",
}
GRADERS = {"set_feature", "_graded", "record_worse"}
VERDICT_WORDS = {"ungraceful", "unsupported", "broken", "quirk", "fragile"}


def _names(node) -> set:
    if node is None:
        return {"BaseException"}
    if isinstance(node, ast.Tuple):
        return set().union(*(_names(e) for e in node.elts))
    if isinstance(node, ast.Attribute):
        return {node.attr}
    if isinstance(node, ast.Name):
        return {node.id}
    return set()


def _walk_shallow(nodes):
    """Walk ``nodes`` without descending into nested functions or classes."""
    stack = list(nodes)
    while stack:
        node = stack.pop()
        yield node
        for child in ast.iter_child_nodes(node):
            if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                stack.append(child)


def _called(nodes) -> set:
    called = set()
    for node in _walk_shallow(nodes):
        if isinstance(node, ast.Call):
            called |= _names(node.func)
    return called


def _claims_nothing(call) -> bool:
    """``set_feature(f, None)``, or a value whose support is unknown."""
    value = call.args[1] if len(call.args) > 1 else None
    if value is None:
        return False  ## set_feature(f) records "full"
    if isinstance(value, ast.Constant):
        return value.value is None or value.value == "unknown"
    if isinstance(value, ast.Dict):
        return any(
            isinstance(k, ast.Constant)
            and k.value == "support"
            and isinstance(v, ast.Constant)
            and v.value == "unknown"
            for k, v in zip(value.keys, value.values)
        )
    return False


def _is_unknown_grade(call) -> bool:
    """``_graded("unknown", ...)`` or a setter given unknown or None."""
    if _names(call.func) & {"_graded"}:
        return bool(call.args) and isinstance(call.args[0], ast.Constant) and call.args[0].value == "unknown"
    return bool(_names(call.func) & GRADERS) and _claims_nothing(call)


def _keeps_the_error(handler) -> bool:
    """Whether a handler stores or returns the error, or something read off
    it (its status, its class), to be graded elsewhere."""
    if handler.name is None:
        return False
    for node in _walk_shallow(handler.body):
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Return)) and node.value is not None:
            if isinstance(node.value, ast.Call) and _is_unknown_grade(node.value):
                continue
            if any(isinstance(n, ast.Name) and n.id == handler.name for n in ast.walk(node.value)):
                return True
    return False


def _grades(handler) -> bool:
    """Whether a handler records a verdict other than unknown, or hands the
    error on to be graded."""
    if _keeps_the_error(handler):
        return True
    for node in _walk_shallow(handler.body):
        if isinstance(node, ast.Constant) and (node.value in VERDICT_WORDS or node.value is False):
            return True
        if isinstance(node, ast.Call) and _names(node.func) & GRADERS:
            if _names(node.func) & {"_graded"}:
                if not (node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == "unknown"):
                    return True
            elif not _claims_nothing(node):
                return True
    return False


def _unclassified_handlers():
    tree = ast.parse(CHECKS.read_text())
    found = []
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in _walk_shallow(func.body):
            if not isinstance(node, ast.ExceptHandler):
                continue
            caught = _names(node.type)
            if not caught & CAUGHT or "Exception" in caught and not caught & (CAUGHT - {"Exception"}):
                continue
            if not _grades(node) or _called(node.body) & CLASSIFIERS:
                continue
            if func.name in EXEMPT:
                continue
            found.append(f"{CHECKS.name}:{node.lineno} in {func.name}")
    return found


def test_every_grading_dav_error_handler_classifies_the_error() -> None:
    """A handler that grades a caught DAVError must first tell an answer from
    an outage: call ``require_answer()`` (or ``refusal_status()`` /
    ``graded_refusal()``) in it."""
    assert _unclassified_handlers() == []


## --- the checks that must not stop on a request nobody answered ---


def test_auto_create_probe_without_an_answer_is_unknown_and_goes_on(monkeypatch) -> None:
    """The first request of the make/delete probing: a 503 there must not end
    the check, or PrepareCalendar finds no creation verdict and gives up."""
    import caldav_server_tester.checks as checks_mod
    from caldav_server_tester.checks import CheckMakeDeleteCalendar

    monkeypatch.setattr(checks_mod.time, "sleep", lambda _s: None)
    client = Mock()
    client.features = FeatureSet()
    checker = ServerQuirkChecker(client, debug_mode=None)
    checker.principal = Mock()
    checker.principal.calendar.return_value.events.side_effect = ReportError("503 Service Unavailable")
    check = CheckMakeDeleteCalendar(checker)
    check.expected_features = client.features
    later = []
    check._try_make_calendar = lambda cal_id, **kwargs: later.append(cal_id) or False
    check._probe_make_delete()
    assert checker._features_checked.is_supported("create-calendar.auto", str) == "unknown"
    assert later, "the make/delete probing stopped at the auto-create probe"


@pytest.mark.parametrize(
    "error", [RateLimitError(reason="429 Too Many Requests"), AuthorizationError(url=URL, reason="x", status=401)]
)
def test_a_lookup_without_an_answer_is_no_deviation(error) -> None:
    from caldav_server_tester.checks import CheckNonExistingResource

    verdict = CheckNonExistingResource._deviation_or_outage(error, "raises {name} instead of NotFoundError")
    assert verdict["support"] == "unknown"


def test_a_raw_request_refused_with_a_stale_401_is_no_answer() -> None:
    from caldav_server_tester.checks import _RawPut

    def request():
        raise AuthorizationError(url=URL, reason="Unauthorized", status=401)

    assert _RawPut._answer(request) is None


def test_a_raw_request_refused_with_403_is_an_answer() -> None:
    from caldav_server_tester.checks import _RawPut

    def request():
        raise AuthorizationError(url=URL, reason="Forbidden", status=403)

    assert _RawPut._answer(request).status == 403


## --- the fixture setup grades and carries on ---


def _prepare_with_refused(component, error):
    """Run the task or journal fixture setup against a server that answers
    ``error`` to every save, and return what it recorded."""
    import caldav_server_tester.checks as checks_mod
    from caldav_server_tester.checks import PrepareCalendar

    client = Mock()
    client.features = FeatureSet()
    checker = ServerQuirkChecker(client, debug_mode=None)
    checker.principal = Mock()
    checker.fixture_base_year = 2000
    check = PrepareCalendar(checker)
    check.expected_features = client.features

    def add_if_not_existing(*largs, **kwargs):
        raise error

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(check, "_own_calendar", Mock(side_effect=checks_mod.NotFoundError("404 Not Found")))
        if component == "VTODO":
            check._prepare_task_calendar("csc_cal", "csc", add_if_not_existing)
        else:
            check._prepare_journal_calendar("csc_cal", "csc", add_if_not_existing)
    return checker._features_checked


@pytest.mark.parametrize(("component", "feature"), [("VTODO", "save-load.todo"), ("VJOURNAL", "save-load.journal")])
def test_a_fixture_saved_without_an_answer_is_unknown(component, feature) -> None:
    features = _prepare_with_refused(component, PutError("503 Service Unavailable"))
    assert features.is_supported(feature, str) == "unknown"


@pytest.mark.parametrize(("component", "feature"), [("VTODO", "save-load.todo"), ("VJOURNAL", "save-load.journal")])
def test_a_fixture_refused_is_ungraceful(component, feature) -> None:
    features = _prepare_with_refused(component, PutError("403 Forbidden"))
    assert features.is_supported(feature, str) == "ungraceful"


def test_a_client_side_failure_of_the_journal_fixture_is_still_ungraceful() -> None:
    features = _prepare_with_refused("VJOURNAL", ValueError("not a VJOURNAL"))
    assert features.is_supported("save-load.journal", str) == "ungraceful"
