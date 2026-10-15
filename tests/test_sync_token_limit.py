"""DAV:limit in a sync-collection report, and paging by checkpoint tokens.

RFC 6578 section 3.7 lets a client limit a sync-collection reply; section 3.6
has a truncated reply end in a 507 for the collection and a checkpoint token
to continue from.  Stalwart's checkpoint of a truncated *initial* sync skips
the remaining members (and it truncates on its own at maxResults), Xandikos
cuts the reply without a 507 and hands out its newest token, and Radicale
ignores the limit.  https://github.com/pycalendar/calendaring-sync/pull/19
"""

import re
from unittest.mock import Mock

import pytest
from caldav.compatibility_hints import FeatureSet
from caldav.lib.url import URL
from caldav.response import DAVResponse

from caldav_server_tester.checks import CheckSyncTokenLimit

BASE = "/dav/cal/"
CAL = "https://caldav.example.com" + BASE


class SyncServer:
    """A calendar answering sync-collection the ways the probe tells apart.

    A token ``tok-S`` asks for changes after sequence number S, and the
    checkpoint ``tok-S:O`` for those after skipping O of them.  ``mode`` is
    "rfc", "stalwart" (an initial sync lists the collection, and its
    checkpoint is the newest token), "xandikos" (cut without a 507, newest
    token), "cut" (cut without a 507, but with a working checkpoint), "stuck"
    (a 507 that keeps the old token) or "ignore" (no limit).  ``cap`` limits
    every reply, asked for or not, as Stalwart's maxResults does, and
    ``slash=False`` drops the trailing slash from the collection's href.
    ``alias`` names the calendar by another path in every href, as OX does,
    and ``refuse`` ("all", "initial" or "bare", the latter without the
    precondition) fails a limited request with a 507, as RFC 6578 section
    3.7 says (CCS; Nextcloud on an initial sync).
    """

    def __init__(
        self,
        mode: str,
        members: int = 5,
        cap: int | None = None,
        slash: bool = True,
        alias: str | None = None,
        refuse: str | None = None,
    ) -> None:
        self.mode = mode
        self.cap = cap
        self.refuse = refuse
        self.href_base = alias or BASE
        self.base = self.href_base if slash else self.href_base.rstrip("/")
        self.seq = 0
        self.changes: dict[str, int] = {}
        for i in range(members):
            self.change(f"m{i}.ics")

    def change(self, name: str) -> None:
        self.seq += 1
        self.changes[name] = self.seq

    def report(self, url: str, body: str, depth: int = 0) -> DAVResponse:
        token = re.search(r"<D:sync-token>([^<]*)</D:sync-token>", body).group(1)
        limit = re.search(r"<D:nresults>(\d+)</D:nresults>", body)
        if limit and (self.refuse in ("all", "bare") or (self.refuse == "initial" and not token)):
            precondition = "" if self.refuse == "bare" else "<D:number-of-matches-within-limits/>"
            return DAVResponse.from_bytes(
                f'<?xml version="1.0"?><D:error xmlns:D="DAV:">{precondition}</D:error>'.encode(), status_code=507
            )
        since, skip = 0, 0
        if token:
            (since_s, _, skip_s) = token.removeprefix("tok-").partition(":")
            since, skip = int(since_s), int(skip_s or 0)
        names = sorted((n for n, s in self.changes.items() if s > since), key=self.changes.get)
        if self.mode == "stalwart" and not token:
            names.insert(1, None)
        names = names[skip:]
        truncated = False
        new_token = f"tok-{self.seq}"
        n = int(limit.group(1)) if limit and self.mode != "ignore" else None
        if self.cap is not None:
            n = self.cap if n is None else min(n, self.cap)
        if n is not None and len(names) > n:
            names = names[:n]
            if self.mode in ("rfc", "cut") or (self.mode == "stalwart" and token):
                truncated = self.mode != "cut"
                new_token = f"tok-{since}:{skip + n}"
            elif self.mode == "stalwart":
                truncated = True
            elif self.mode == "stuck":
                truncated = True
                new_token = token
        parts = ['<?xml version="1.0"?><D:multistatus xmlns:D="DAV:">']
        for name in names:
            parts.append(
                f"<D:response><D:href>{self.base if name is None else self.href_base + name}</D:href><D:propstat>"
                f'<D:prop><D:getetag>"e{name}"</D:getetag></D:prop>'
                "<D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response>"
            )
        if truncated:
            parts.append(
                f"<D:response><D:href>{self.base}</D:href>"
                "<D:status>HTTP/1.1 507 Insufficient Storage</D:status></D:response>"
            )
        parts.append(f"<D:sync-token>{new_token}</D:sync-token></D:multistatus>")
        return DAVResponse.from_bytes("".join(parts).encode())

    def save_object(self, comp_class, uid: str, **kwargs) -> Mock:
        self.change(f"{uid}.ics")
        obj = Mock()
        obj.url = URL(CAL).join(f"{uid}.ics")
        obj.delete = Mock(side_effect=lambda: self.changes.pop(f"{uid}.ics"))
        return obj

    def children(self) -> list:
        ## OX lists the calendar itself, by its other name
        own = [(URL(CAL).join(self.href_base), None, None)] if self.href_base != BASE else []
        return own + [(URL(CAL).join(self.href_base + name), None, None) for name in self.changes]


def run_check(server: SyncServer, sync_token: object = "full") -> FeatureSet:
    checker = Mock()
    checker._features_checked = FeatureSet()
    checker.features_checked = checker._features_checked
    checker.features_checked.set_feature("sync-token", sync_token)
    checker.debug_mode = None
    checker.expected_features = FeatureSet()
    checker._client_obj = Mock()
    checker._client_obj.report = Mock(side_effect=server.report)
    checker.calendar = Mock()
    checker.calendar.url = URL(CAL)
    checker.calendar.save_object = Mock(side_effect=server.save_object)
    checker.calendar.children = Mock(side_effect=server.children)
    checker.fixture_base_year = 2027
    CheckSyncTokenLimit(checker)._run_check()
    return checker._features_checked


def support(fs: FeatureSet, feature: str) -> dict:
    return fs.is_supported(feature, return_type=dict, return_defaults=False)


@pytest.mark.parametrize(
    "mode, limit, initial",
    [
        ("rfc", "full", "full"),
        ("stalwart", "full", "broken"),
        ("xandikos", "broken", "broken"),
        ("ignore", "unsupported", "unsupported"),
    ],
)
def test_grading(mode: str, limit: str, initial: str) -> None:
    server = SyncServer(mode)
    fs = run_check(server)
    assert support(fs, "sync-token.limit")["support"] == limit
    assert support(fs, "sync-token.limit.initial-sync")["support"] == initial
    ## the probe events are gone again
    assert sorted(server.changes) == [f"m{i}.ics" for i in range(5)]


def test_stalwart_behaviour_text() -> None:
    initial = support(run_check(SyncServer("stalwart")), "sync-token.limit.initial-sync")
    assert "checkpoint token skips them" in initial["behaviour"]
    assert "collection itself" in initial["behaviour"]


def test_xandikos_behaviour_text() -> None:
    limit = support(run_check(SyncServer("xandikos")), "sync-token.limit")
    assert "without a 507" in limit["behaviour"]
    assert limit["behaviour"].count("without a 507") == 1


def test_big_calendar_stays_within_the_page_cap() -> None:
    server = SyncServer("rfc", members=500)
    server.report = Mock(side_effect=server.report)
    fs = run_check(server)
    assert support(fs, "sync-token.limit.initial-sync")["support"] == "full"
    assert server.report.call_count < 2 * CheckSyncTokenLimit.MAX_PAGES


@pytest.mark.parametrize("sync_token", [False, {"support": "ungraceful"}])
def test_skipped_without_sync_tokens(sync_token: object) -> None:
    server = SyncServer("rfc")
    server.report = Mock(side_effect=server.report)
    run_check(server, sync_token)
    assert not server.report.called


def test_507_href_without_trailing_slash() -> None:
    """The request-URI may come back without its trailing slash."""
    fs = run_check(SyncServer("rfc", slash=False))
    assert support(fs, "sync-token.limit")["support"] == "full"
    initial = support(run_check(SyncServer("stalwart", slash=False)), "sync-token.limit.initial-sync")
    assert "collection itself" in initial["behaviour"]


@pytest.mark.parametrize(
    "mode, note",
    [("cut", "without a 507"), ("stuck", "without a new token")],
)
def test_lossless_but_off_rfc_is_fragile(mode: str, note: str) -> None:
    limit = support(run_check(SyncServer(mode)), "sync-token.limit")
    assert limit["support"] == "fragile"
    assert note in limit["behaviour"]


def test_page_cap_and_server_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Out of pages on a server capping every reply: the final report is
    paged too, and nothing is lost, but caldav would stop short."""
    monkeypatch.setattr(CheckSyncTokenLimit, "MAX_PAGES", 2)
    initial = support(run_check(SyncServer("rfc", members=8, cap=3)), "sync-token.limit.initial-sync")
    assert initial["support"] == "fragile"
    assert "still truncated" in initial["behaviour"]


def test_calendar_by_another_path() -> None:
    """OX names the calendar by an id path in every href, the 507 marker
    included, and lists itself among its children by that path."""
    fs = run_check(SyncServer("rfc", alias="/caldav/Y2FsOi8vMC81MQ/"))
    assert support(fs, "sync-token.limit")["support"] == "full"
    assert support(fs, "sync-token.limit.initial-sync")["support"] == "full"


@pytest.mark.parametrize(
    "refuse, limit, initial",
    [("all", "unsupported", "unsupported"), ("initial", "broken", "unsupported"), ("bare", "ungraceful", "ungraceful")],
)
def test_507_refusal(refuse: str, limit: str, initial: str) -> None:
    """A limit refused with a 507 and DAV:number-of-matches-within-limits is
    what RFC 6578 section 3.7 asks of a server that cannot truncate (CCS,
    Nextcloud on an initial sync); a bare 507 is not."""
    mode = "xandikos" if refuse == "initial" else "rfc"
    fs = run_check(SyncServer(mode, refuse=refuse))
    assert support(fs, "sync-token.limit")["support"] == limit
    assert support(fs, "sync-token.limit.initial-sync")["support"] == initial
    if initial == "unsupported":
        assert "number-of-matches-within-limits" in support(fs, "sync-token.limit.initial-sync")["behaviour"]
