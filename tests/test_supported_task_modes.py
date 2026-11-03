"""Whether a server advertises RFC 10051 with CALDAV:supported-task-mode-set (section 13.1).

Neither Ox nor Zimbra has the property, on the calendar home or a calendar.
"""

from unittest.mock import Mock

from caldav.compatibility_hints import FeatureSet
from lxml import etree

from caldav_server_tester.checks import CheckSupportedTaskModes

HOME = "http://example.com/home/"
CAL = "http://example.com/home/tasks/"


def _answer(modes):
    if modes is None:
        prop, status = "<C:supported-task-mode-set/>", "404 Not Found"
    else:
        inner = "".join(f"<C:supported-task-mode>{m}</C:supported-task-mode>" for m in modes)
        prop, status = f"<C:supported-task-mode-set>{inner}</C:supported-task-mode-set>", "200 OK"
    xml = (
        '<?xml version="1.0"?><D:multistatus xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">'
        f"<D:response><D:href>/x/</D:href><D:propstat><D:prop>{prop}</D:prop>"
        f"<D:status>HTTP/1.1 {status}</D:status></D:propstat></D:response></D:multistatus>"
    )
    return Mock(status=207, tree=etree.fromstring(xml.encode()), raw=xml)


def _run(home=None, calendar=None, fails=False, home_fails=False, tasklist=True):
    """``home`` and ``calendar``: the modes each lists, None where the property is absent"""
    client = Mock()

    def request(url, method="GET", body="", headers=None):
        assert method == "PROPFIND" and "supported-task-mode-set" in body
        if fails:
            return Mock(status=500, tree=None, raw="")
        return _answer(home if url == HOME else calendar)

    client.request.side_effect = request
    checker = Mock()
    checker._features_checked = FeatureSet()
    checker.debug_mode = None
    checker._client_obj = client
    if home_fails:
        type(checker.principal).calendar_home_set = property(lambda self: (_ for _ in ()).throw(OSError("gone")))
    else:
        checker.principal.calendar_home_set.url = HOME
    if tasklist:
        checker.tasklist.url = CAL
    else:
        checker.tasklist = None
    CheckSupportedTaskModes(checker)._run_check()
    return checker._features_checked.is_supported("supported-task-modes", return_type=dict)


def test_absent_everywhere_is_unsupported():
    """Ox, Zimbra"""
    verdict = _run()
    assert verdict["support"] == "unsupported"
    assert "supported-task-mode-set" in verdict["behaviour"]


def test_on_the_home_is_full_with_the_modes():
    assert _run(home=["AUTOMATIC-COMPLETION", "AUTOMATIC-FAILURE"]) == {
        "support": "full",
        "values": ["AUTOMATIC-COMPLETION", "AUTOMATIC-FAILURE"],
    }


def test_on_a_calendar_only_is_full():
    assert _run(calendar=["AUTOMATIC-COMPLETION"])["support"] == "full"


def test_an_empty_set_is_unsupported():
    """No supported-task-mode inside: RFC 10051 says to assume no support"""
    assert _run(home=[])["support"] == "unsupported"


def test_a_failing_propfind_is_unknown():
    assert _run(fails=True)["support"] == "unknown"


def test_a_failing_home_lookup_asks_the_calendar_only():
    assert _run(calendar=["AUTOMATIC-COMPLETION"], home_fails=True)["support"] == "full"


def test_without_a_task_calendar_the_home_is_asked():
    assert _run(home=["AUTOMATIC-COMPLETION"], tasklist=False)["support"] == "full"
