"""What a sync-collection report lists, member by member.

RFC 6578 section 3.4: an initial sync (empty token) lists the current members
and no removed ones.  Radicale lists deleted members with a 404, Stalwart also
lists the collection itself.  Section 3.5.2: a member created and deleted
between two reports is reported as removed; Xandikos leaves it out, and so
does Stalwart where nothing else it had changed.  Sections 3.5.1 and 3.2: a member deleted and re-created at one href is
reported once, as changed; Stalwart reports it as removed and then changed.
"""

import re
from unittest.mock import Mock

import pytest
from caldav.compatibility_hints import FeatureSet
from caldav.lib.error import DAVError
from caldav.lib.url import URL
from lxml import etree

from caldav_server_tester.checks import CheckSyncMembers

BASE = "http://example.com/cal/"
INITIAL = "sync-token.initial-sync"
TRANSIENT = "sync-token.delete.created-and-deleted"
RECREATED = "sync-token.recreated"


class SyncServer:
    """A calendar keeping a change log; a token is the log's length.

    ``removed_in_initial`` lists deleted members with a 404 in an initial
    sync (Radicale), ``collection_in_initial`` lists the collection itself
    (Stalwart), ``skip_transient`` leaves out a member created and deleted
    since the token (Xandikos), ``skip_lone_transient`` does so only where no
    member that existed at the token changed too (Stalwart), and
    ``recreate_twice`` reports a re-created member as removed and then
    changed (Stalwart).
    """

    def __init__(
        self,
        removed_in_initial=False,
        collection_in_initial=False,
        skip_transient=False,
        skip_lone_transient=False,
        recreate_twice=False,
        recreate_as_removed=False,
        report_raises=None,
        reports_before_raising=0,
        refuse_recreate=False,
    ):
        self.refuse_recreate = refuse_recreate
        self.removed_in_initial = removed_in_initial
        self.collection_in_initial = collection_in_initial
        self.skip_transient = skip_transient
        self.skip_lone_transient = skip_lone_transient
        self.recreate_twice = recreate_twice
        self.recreate_as_removed = recreate_as_removed
        self.report_raises = report_raises
        self.reports_before_raising = reports_before_raising
        self.log = []
        self.objects = set()
        self.deleted = set()

    @staticmethod
    def _name(url):
        return url.rstrip("/").rsplit("/", 1)[-1]

    def put(self, url, body, headers=None):
        name = self._name(url)
        if self.refuse_recreate and name in self.deleted:
            return Mock(status=409, headers={})
        self.log.append((name, "put"))
        self.objects.add(name)
        self.deleted.discard(name)
        return Mock(status=201, headers={})

    def delete(self, url, headers=None):
        name = self._name(url)
        if name in self.objects:
            self.log.append((name, "delete"))
            self.objects.discard(name)
            self.deleted.add(name)
            return Mock(status=204)
        return Mock(status=404)

    def _state_at(self, seq):
        state = set()
        for name, action in self.log[:seq]:
            (state.add if action == "put" else state.discard)(name)
        return state

    def report(self, url, body, depth=0):
        if self.report_raises:
            if not self.reports_before_raising:
                raise self.report_raises
            self.reports_before_raising -= 1
        token = re.search(r"<D:sync-token>(.*?)</D:sync-token>", body)[1]
        entries = []
        if not token:
            entries = [(name, None) for name in sorted(self.objects)]
            if self.removed_in_initial:
                entries += [(name, 404) for name in sorted(self.deleted)]
            if self.collection_in_initial:
                entries.append(("", None))
        else:
            since = int(token)
            before = self._state_at(since)
            window = self.log[since:]
            ## Stalwart reports a transient member only beside a change to one it had
            skip_transient = self.skip_transient or (
                self.skip_lone_transient and not any(n in before for n, _ in window)
            )
            for name in dict.fromkeys(n for n, _ in window):
                deleted_in_window = any(n == name and a == "delete" for n, a in window)
                if name in self.objects:
                    if deleted_in_window and name in before and self.recreate_as_removed:
                        entries.append((name, 404))
                    elif deleted_in_window and name in before and self.recreate_twice:
                        entries += [(name, 404), (name, None)]
                    else:
                        entries.append((name, None))
                elif name in before or not skip_transient:
                    entries.append((name, 404))
        return self._multistatus(entries, str(len(self.log)))

    @staticmethod
    def _multistatus(entries, token):
        parts = []
        for name, status in entries:
            href = f"<D:href>/cal/{name}</D:href>"
            if status:
                parts.append(f"<D:response>{href}<D:status>HTTP/1.1 {status} Not Found</D:status></D:response>")
            else:
                parts.append(
                    f'<D:response>{href}<D:propstat><D:prop><D:getetag>"x"</D:getetag></D:prop>'
                    "<D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response>"
                )
        xml = (
            '<?xml version="1.0" encoding="utf-8"?><D:multistatus xmlns:D="DAV:">'
            + "".join(parts)
            + f"<D:sync-token>{token}</D:sync-token></D:multistatus>"
        )
        return Mock(status=207, tree=etree.fromstring(xml.encode()), raw=xml)


def _run(server, sync=None):
    checker = Mock()
    checker._features_checked = FeatureSet({"sync-token": sync or {"support": "full"}})
    checker.features_checked = checker._features_checked
    checker.debug_mode = None
    checker._client_obj = server
    checker.fixture_base_year = 2027
    checker.calendar.url = URL.objectify(BASE)
    CheckSyncMembers(checker)._run_check()
    fs = checker._features_checked
    return {f: fs.is_supported(f, return_type=dict) for f in (INITIAL, TRANSIENT, RECREATED)}


def test_an_rfc_server_is_full_on_all_three():
    server = SyncServer()
    verdicts = _run(server)
    assert {v["support"] for v in verdicts.values()} == {"full"}
    assert not server.objects


def test_deleted_members_in_an_initial_sync():
    """Radicale"""
    verdict = _run(SyncServer(removed_in_initial=True))[INITIAL]
    assert verdict["support"] == "quirk"
    assert verdict["behaviour"] == "lists deleted members with a 404"


def test_the_collection_in_an_initial_sync():
    """Stalwart"""
    verdict = _run(SyncServer(collection_in_initial=True))[INITIAL]
    assert verdict["support"] == "quirk"
    assert verdict["behaviour"] == "lists the collection itself"


def test_a_created_and_deleted_member_left_out():
    """Xandikos, Stalwart"""
    verdict = _run(SyncServer(skip_transient=True))[TRANSIENT]
    assert verdict["support"] == "unsupported"
    assert "not reported" in verdict["behaviour"]


def test_a_created_and_deleted_member_alone_is_not_reported():
    """Stalwart 0.16: reported only where a member it had changed in the same interval"""
    verdict = _run(SyncServer(skip_lone_transient=True))[TRANSIENT]
    assert verdict["support"] == "unsupported"


def test_a_recreated_member_reported_twice():
    """Stalwart"""
    verdict = _run(SyncServer(recreate_twice=True))[RECREATED]
    assert verdict["support"] == "quirk"
    assert verdict["behaviour"] == "reported twice: as removed, then as changed"


def test_a_recreated_member_reported_as_removed_only():
    verdict = _run(SyncServer(recreate_as_removed=True))[RECREATED]
    assert verdict["support"] == "broken"


def test_a_later_failing_report_keeps_the_verdicts_already_given():
    verdicts = _run(SyncServer(skip_transient=True, report_raises=DAVError("500"), reports_before_raising=2))
    assert verdicts[INITIAL] == {"support": "full"}
    assert verdicts[TRANSIENT]["support"] == "unsupported"
    assert verdicts[RECREATED]["support"] == "ungraceful"


def test_a_failing_report_is_ungraceful():
    verdicts = _run(SyncServer(report_raises=DAVError("500")))
    assert {v["support"] for v in verdicts.values()} == {"ungraceful"}


@pytest.mark.parametrize("support", ["unsupported", "broken"])
def test_nothing_is_probed_without_sync_support(support):
    server = SyncServer()
    verdicts = _run(server, sync={"support": support})
    assert not server.log
    ## Not probed, so derived from sync-token
    assert {v["support"] for v in verdicts.values()} == {support}


def test_a_member_that_cannot_be_recreated_is_not_graded():
    """Nextcloud keeps a deleted object's UID in its trashbin and refuses it."""
    verdicts = _run(SyncServer(refuse_recreate=True))
    assert verdicts[RECREATED]["support"] == "unknown"
    assert "re-created" in verdicts[RECREATED]["behaviour"]
    assert verdicts[TRANSIENT]["support"] == "full"
