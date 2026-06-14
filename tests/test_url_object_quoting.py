"""url_object() must build the same URL the library PUT the object to."""


class _Recorder:
    """Stands in for Event/Todo: records the url url_object() built."""

    def __init__(self, client, url=None, parent=None):
        self.client, self.url, self.parent = client, url, parent


class TestUrlObjectMatchesTheLibrary:
    """url_object() reaches an object at the URL the library PUT it to.

    The library builds that URL with _quote_uid(); url_object() joined the raw
    UID.  They agree for every plain csc_* fixture and diverge the moment a UID
    contains a character that needs quoting -- at which point the direct-URL
    lookups (used to reach objects a sliding window hides from REPORT) silently
    address a resource that is not there.
    """

    @staticmethod
    def _cal():
        from unittest.mock import Mock

        from caldav.lib.url import URL

        cal = Mock()
        cal.url = URL.objectify("http://dav.example.com/cal/")
        cal.client = Mock()
        return cal

    def test_url_object_quotes_the_uid_like_the_library(self) -> None:
        from caldav.calendarobjectresource import _quote_uid

        from caldav_server_tester.checks import url_object

        cal = self._cal()
        uid = "csc_needs quoting@example.com"
        obj = url_object(cal, uid, _Recorder)
        assert str(obj.url) == str(cal.url.join(_quote_uid(uid) + ".ics"))

    def test_plain_uids_are_unaffected(self) -> None:
        from caldav_server_tester.checks import url_object

        cal = self._cal()
        obj = url_object(cal, "csc_simple_event1", _Recorder)
        assert str(obj.url).endswith("/csc_simple_event1.ics")
