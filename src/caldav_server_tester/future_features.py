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

FUTURE_FEATURES: dict[str, dict] = {}


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
