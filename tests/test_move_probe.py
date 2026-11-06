"""Grading of the WebDAV MOVE probe"""

## DISCLAIMER: those tests are AI-generated

import re
from unittest.mock import Mock

from caldav.compatibility_hints import FeatureSet
from caldav.lib.error import DAVError

from caldav_server_tester.checks import CheckMove, grade_move, grade_precondition


def test_the_move_features_are_defined() -> None:
    for name in ("move", "move.overwrite", "move.if-match"):
        assert "description" in FeatureSet.find_feature(name)


def test_move_stands_on_its_own_beside_its_preconditions() -> None:
    ## a server that moves but ignores If-Match still moves: move is not derived from its children
    fs = FeatureSet()
    fs.set_feature("move.if-match", {"support": "unsupported"})
    assert fs.is_supported("move", str) == "full"


def test_a_move_that_moves_is_full() -> None:
    assert grade_move(204, in_source=False, in_target=True) is True
    assert grade_move(201, in_source=False, in_target=True) is True


def test_a_server_without_move_is_unsupported() -> None:
    for status in (405, 501):
        assert grade_move(status, in_source=True, in_target=False)["support"] == "unsupported"


def test_a_refused_move_is_ungraceful() -> None:
    assert grade_move(403, in_source=True, in_target=False) == {
        "support": "ungraceful",
        "behaviour": "MOVE answered 403",
    }


def test_a_multi_status_answer_is_a_failure() -> None:
    ## RFC 4918 section 9.9.4: a 207 reports errors on resources other than the request-URI
    assert grade_move(207, in_source=True, in_target=False)["support"] == "ungraceful"


def test_a_move_that_copies_is_fragile() -> None:
    assert grade_move(204, in_source=True, in_target=True)["support"] == "fragile"


def test_a_move_that_loses_the_object_is_broken() -> None:
    assert grade_move(204, in_source=False, in_target=False)["support"] == "broken"


def test_a_precondition_is_honoured_with_412() -> None:
    assert grade_precondition(412, "Overwrite: F", moved=False) is True


def test_an_ignored_precondition_is_unsupported() -> None:
    graded = grade_precondition(204, "an outdated If-Match", moved=True)
    assert graded["support"] == "unsupported"
    assert "an outdated If-Match" in graded["behaviour"]
    assert "204" in graded["behaviour"]


def test_a_refusal_other_than_412_that_moved_nothing_is_ungraceful() -> None:
    for status in (403, 409, 500):
        graded = grade_precondition(status, "Overwrite: F onto an existing object", moved=False)
        assert graded["support"] == "ungraceful"
        assert str(status) in graded["behaviour"]


def test_an_error_status_after_moving_anyway_is_unsupported() -> None:
    assert grade_precondition(500, "an outdated If-Match", moved=True)["support"] == "unsupported"


class _Calendar:
    def __init__(self, server: "_Server", name: str) -> None:
        self.server, self.name, self.url = server, name, f"http://example.invalid/{name}/"
        self.objects: dict[str, str] = {}  ## name -> UID
        self.deleted = False

    def add_event(self, ical: str) -> Mock:
        uid = re.search(r"UID:(.*)", ical)[1].strip()
        name = uid + ".ics"
        self.objects[name] = uid
        obj = Mock(url=self.url + name)
        obj.delete.side_effect = lambda: self.objects.pop(name, None)
        return obj

    def children(self) -> list:
        return [(self.url + name, None, None) for name in self.objects]

    def delete(self) -> None:
        self.deleted = True


class _Server:
    """A client moving objects between calendars.  ``move_status`` answers a
    plain MOVE; a MOVE that should fail with 412 gets ``precondition``, and
    what happens to it is ``mode``: "refuse" (nothing), "move" (moved anyway),
    "copy" (copied into the target as <uid>.ics, the source kept), or
    "overwrite" (the destination replaced, the source kept)"""

    def __init__(
        self, move_status: int = 201, precondition: int = 412, mode: str = "refuse", second_calendar: bool = True
    ) -> None:
        self.move_status, self.precondition, self.mode = move_status, precondition, mode
        self.cal1 = _Calendar(self, "cal1")
        self.cal2 = _Calendar(self, "csc_move_cal2")
        self.principal = Mock()
        self.principal.calendars.return_value = []
        if second_calendar:
            self.principal.make_calendar.return_value = self.cal2
        else:
            self.principal.make_calendar.side_effect = DAVError("403 Forbidden")

    def _by_url(self, url: str) -> tuple[_Calendar, str]:
        base, name = url.rsplit("/", 1)
        return (self.cal1 if base + "/" == self.cal1.url else self.cal2), name

    def request(self, url: str, method: str = "GET", body: str = "", headers: dict | None = None) -> Mock:
        if method == "GET":
            calendar, name = self._by_url(url)
            uid = calendar.objects.get(name)
            return Mock(status=200 if uid else 404, raw=f"BEGIN:VEVENT\r\nUID:{uid}\r\nEND:VEVENT\r\n" if uid else "")
        (source, name), (target, new) = self._by_url(url), self._by_url(headers["Destination"])
        guarded = "If-Match" in headers or new in target.objects
        status, mode = (self.precondition, self.mode) if guarded else (self.move_status, "move")
        if not guarded and not 200 <= status < 300:
            mode = "refuse"
        uid = source.objects.get(name)
        if mode == "move":
            source.objects.pop(name, None)
            target.objects[new] = uid
        elif mode == "copy":
            target.objects[uid + ".ics"] = uid
        elif mode == "overwrite":
            target.objects[new] = uid
        return Mock(status=status)


def _run(server: _Server) -> dict:
    checker = Mock()
    checker._features_checked = FeatureSet()
    checker.debug_mode = None
    checker._client_obj = Mock()
    checker._client_obj.principal.return_value = server.principal
    checker._client_obj.request.side_effect = server.request
    checker.calendar = server.cal1
    CheckMove(checker)._run_check()
    return {
        name: checker._features_checked.is_supported(name, return_type=dict)
        for name in ("move", "move.overwrite", "move.if-match")
    }


def test_a_server_honouring_move_and_its_preconditions_is_full_and_left_clean() -> None:
    server = _Server()
    assert {name: verdict["support"] for name, verdict in _run(server).items()} == dict.fromkeys(
        ("move", "move.overwrite", "move.if-match"), "full"
    )
    assert server.cal2.deleted
    assert not server.cal1.objects


def test_only_one_calendar_leaves_everything_unknown() -> None:
    verdicts = _run(_Server(second_calendar=False))
    assert all(verdict["support"] == "unknown" for verdict in verdicts.values())
    assert "only one calendar" in verdicts["move"]["behaviour"]


def test_without_move_its_preconditions_are_unknown() -> None:
    server = _Server(move_status=405)
    verdicts = _run(server)
    assert verdicts["move"]["support"] == "unsupported"
    assert verdicts["move.overwrite"]["support"] == "unknown"
    assert verdicts["move.if-match"]["support"] == "unknown"
    assert server.cal2.deleted


def test_preconditions_refused_with_409_are_ungraceful() -> None:
    verdicts = _run(_Server(precondition=409))
    assert verdicts["move"]["support"] == "full"
    assert verdicts["move.overwrite"]["support"] == "ungraceful"
    assert verdicts["move.if-match"]["support"] == "ungraceful"


def test_preconditions_ignored_are_unsupported() -> None:
    verdicts = _run(_Server(precondition=204, mode="move"))
    assert verdicts["move.overwrite"]["support"] == "unsupported"
    assert verdicts["move.if-match"]["support"] == "unsupported"


def test_moving_anyway_while_answering_an_error_is_unsupported() -> None:
    verdicts = _run(_Server(precondition=500, mode="move"))
    assert verdicts["move.overwrite"]["support"] == "unsupported"
    assert verdicts["move.if-match"]["support"] == "unsupported"


def test_a_copy_under_the_uid_name_is_not_moving_nothing() -> None:
    """Zimbra names every object <uid>.ics, wherever it is put"""
    verdicts = _run(_Server(precondition=500, mode="copy"))
    assert verdicts["move.overwrite"]["support"] == "unsupported"
    assert verdicts["move.if-match"]["support"] == "unsupported"


def test_an_overwritten_destination_is_not_moving_nothing() -> None:
    assert _run(_Server(precondition=500, mode="overwrite"))["move.overwrite"]["support"] == "unsupported"


def test_a_multi_status_answer_that_moved_nothing_is_ungraceful() -> None:
    ## RFC 4918 section 9.9.4: a 207 reports a failure, here the 412 inside it
    assert grade_precondition(207, "Overwrite: F", moved=False)["support"] == "ungraceful"
