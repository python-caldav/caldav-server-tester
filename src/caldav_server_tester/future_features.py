"""Feature definitions the tester probes before a caldav release has them.

caldav's ``compatibility_hints.py`` changes only with a caldav release, so a
new probe would otherwise have to wait for one.  The definitions here are
added to ``caldav.compatibility_hints.FeatureSet.FEATURES`` when the package
is imported.  Where caldav already defines a feature, as caldav's master
branch may, caldav's definition wins.

This changes caldav's class for the whole process, not only for the tester.

Once the caldav release that defines a feature is the floor in
``pyproject.toml``, delete it here (see ``docs/RELEASE-HOWTO.md``).
"""

from caldav.compatibility_hints import FeatureSet

FUTURE_FEATURES: dict[str, dict] = {
    "move": {
        "description": "WebDAV MOVE (RFC 4918 section 9.9) of a calendar object into another calendar of the same principal: the object ends up in the target only.  RFC 4918 says every WebDAV resource MUST support MOVE.  Without it, a client moves by copying and deleting, which fails where save.duplicate-uid.cross-calendar is not supported.  See https://github.com/python-caldav/caldav/issues/841",
        ## Not derived from move.overwrite and move.if-match: a server ignoring them still moves.
        "default": {"support": "full"},
    },
    "move.overwrite": {
        "description": "A MOVE with 'Overwrite: F' onto an object that already exists at the destination fails with 412 and changes nothing, as RFC 4918 section 10.6 requires.  Where unsupported, the MOVE replaces the object at the destination."
    },
    "move.if-match": {
        "description": "A MOVE with an If-Match the object no longer has fails with 412 and moves nothing (RFC 9110 section 13.1.1).  Where unsupported, a MOVE cannot guard against an object changed since it was read."
    },
}


def register() -> list[str]:
    """Add the definitions caldav lacks to its feature tree; returns their names."""
    added = [name for name in FUTURE_FEATURES if name not in FeatureSet.FEATURES]
    for name in added:
        FeatureSet.FEATURES[name] = dict(FUTURE_FEATURES[name])
    if added:
        ## find_feature() and feature_tree() cache the tree: a parent looked up
        ## before now would never list its new child.
        for definition in FeatureSet.FEATURES.values():
            definition.pop("subfeatures", None)
        if "_feature_tree" in vars(FeatureSet):
            del FeatureSet._feature_tree
    return added
