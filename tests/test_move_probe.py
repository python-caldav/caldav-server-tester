"""Grading of the WebDAV MOVE probe"""

## DISCLAIMER: those tests are AI-generated

from caldav.compatibility_hints import FeatureSet

from caldav_server_tester.checks import grade_move, grade_precondition


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
    assert grade_precondition(412, "Overwrite: F") is True


def test_an_ignored_precondition_is_unsupported() -> None:
    graded = grade_precondition(204, "an outdated If-Match")
    assert graded["support"] == "unsupported"
    assert "an outdated If-Match" in graded["behaviour"]
    assert "204" in graded["behaviour"]
