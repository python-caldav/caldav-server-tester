"""Feature definitions the tester adds to caldav ahead of a caldav release"""

## DISCLAIMER: those tests are AI-generated

import copy
from collections.abc import Iterator

import pytest
from caldav.compatibility_hints import FeatureSet

from caldav_server_tester import future_features


@pytest.fixture
def features(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict]:
    """caldav's feature tree, restored after the test, cached tree included"""
    tree = copy.deepcopy(FeatureSet.FEATURES)
    monkeypatch.setattr(FeatureSet, "FEATURES", tree)
    if "_feature_tree" in vars(FeatureSet):
        monkeypatch.delattr(FeatureSet, "_feature_tree")
    yield tree
    ## a test that looked something up cached a tree built from the copy
    if "_feature_tree" in vars(FeatureSet):
        del FeatureSet._feature_tree


def test_a_definition_caldav_lacks_is_added(features: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    FeatureSet.find_feature("save")  ## caches save's subfeatures before the new one exists
    monkeypatch.setattr(future_features, "FUTURE_FEATURES", {"save.csc-future": {"description": "not in caldav yet"}})
    assert future_features.register() == ["save.csc-future"]
    assert FeatureSet.find_feature("save.csc-future")["description"] == "not in caldav yet"
    assert "csc-future" in FeatureSet.find_feature("save")["subfeatures"]
    fs = FeatureSet()
    fs.set_feature("save.csc-future", False)
    assert fs.is_supported("save.csc-future", str) == "unsupported"


def test_caldavs_own_definition_wins(features: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    ## caldav master may already have it: no error, and caldav's text stays
    caldavs = features["save"].get("description")
    monkeypatch.setattr(future_features, "FUTURE_FEATURES", {"save": {"description": "outdated copy"}})
    assert future_features.register() == []
    assert features["save"].get("description") == caldavs


def test_the_package_registers_its_future_features() -> None:
    for name in future_features.FUTURE_FEATURES:
        assert name in FeatureSet.FEATURES


def test_no_fake_feature_is_left_in_caldav() -> None:
    """Last in the file, so pytest runs it after the tests that add one"""
    assert "save.csc-future" not in FeatureSet.FEATURES
    assert "csc-future" not in FeatureSet.find_feature("save")["subfeatures"]
