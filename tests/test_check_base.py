"""Unit tests for the Check base class"""

## DISCLAIMER: those tests are AI-generated, gone through a very quick human QA

from unittest.mock import Mock

import pytest
from caldav.compatibility_hints import FeatureSet

from caldav_server_tester.checks_base import Check


class TestCheckSetFeature:
    """Test the Check.set_feature method"""

    def create_mock_checker(self, debug_mode='logging') -> Mock:
        """Helper to create a mock checker object"""
        checker = Mock()
        checker._features_checked = FeatureSet()
        checker.debug_mode = debug_mode
        checker._client_obj = Mock()
        checker._client_obj.features = FeatureSet()
        return checker

    def create_check_instance(self, debug_mode='logging') -> Check:
        """Helper to create a Check instance with mocked dependencies"""
        checker = self.create_mock_checker(debug_mode=debug_mode)
        check = Check(checker)
        check.expected_features = FeatureSet()
        return check

    def test_set_feature_with_true_converts_to_full_support(self) -> None:
        """set_feature(feature, True) should set support to 'full'"""
        check = self.create_check_instance(debug_mode=None)
        check.set_feature("create-calendar", True)

        result = check.checker._features_checked.is_supported("create-calendar", dict)
        assert result == {"support": "full"}

    def test_set_feature_with_false_converts_to_unsupported(self) -> None:
        """set_feature(feature, False) should set support to 'unsupported'"""
        check = self.create_check_instance(debug_mode=None)
        check.set_feature("create-calendar", False)

        result = check.checker._features_checked.is_supported("create-calendar", dict)
        assert result == {"support": "unsupported"}

    def test_set_feature_with_none_converts_to_unknown(self) -> None:
        """set_feature(feature, None) should set support to 'unknown'"""
        check = self.create_check_instance(debug_mode=None)
        check.set_feature("create-calendar", None)

        result = check.checker._features_checked.is_supported("create-calendar", dict)
        assert result == {"support": "unknown"}

    def test_set_feature_with_string_converts_to_dict(self) -> None:
        """set_feature(feature, 'value') should set support to 'value'"""
        check = self.create_check_instance(debug_mode=None)
        check.set_feature("create-calendar", "fragile")

        result = check.checker._features_checked.is_supported("create-calendar", dict)
        assert result == {"support": "fragile"}

    def test_set_feature_with_dict_passes_through(self) -> None:
        """set_feature(feature, dict) should pass the dict through"""
        check = self.create_check_instance(debug_mode=None)
        feature_dict = {"support": "quirk", "behaviour": "test behaviour"}
        check.set_feature("create-calendar", feature_dict)

        result = check.checker._features_checked.is_supported("create-calendar", dict)
        assert result == feature_dict

    def test_set_feature_with_invalid_type_raises_assertion(self) -> None:
        """set_feature with unsupported type should raise AssertionError"""
        check = self.create_check_instance(debug_mode=None)

        with pytest.raises(AssertionError):
            check.set_feature("create-calendar", 123)

        with pytest.raises(AssertionError):
            check.set_feature("create-calendar", [])

    def test_set_feature_debug_mode_none_skips_validation(self) -> None:
        """With debug_mode=None, validation should be skipped"""
        check = self.create_check_instance(debug_mode=None)
        # Should not raise even if expectations don't match
        check.set_feature("create-calendar", True)
        # If we got here without exception, test passed

    def test_set_feature_with_nested_feature_names(self) -> None:
        """Features with dotted names should work correctly"""
        check = self.create_check_instance(debug_mode=None)
        check.set_feature("create-calendar.auto", True)

        result = check.checker._features_checked.is_supported(
            "create-calendar.auto", dict
        )
        assert result == {"support": "full"}


class TestCheckFeatureChecked:
    """Test the Check.feature_checked method"""

    def create_check_instance(self) -> Check:
        """Helper to create a Check instance"""
        checker = Mock()
        checker._features_checked = FeatureSet()
        checker._client_obj = Mock()
        return Check(checker)

    def test_feature_checked_delegates_to_featureset(self) -> None:
        """feature_checked should delegate to the FeatureSet.is_supported method"""
        check = self.create_check_instance()
        check.checker._features_checked.copyFeatureSet(
            {"create-calendar": {"support": "full"}}, collapse=False
        )

        result = check.feature_checked("create-calendar", bool)
        assert result is True

    def test_feature_checked_returns_bool_by_default(self) -> None:
        """feature_checked without return_type should return bool"""
        check = self.create_check_instance()
        check.checker._features_checked.copyFeatureSet(
            {"create-calendar": {"support": "full"}}, collapse=False
        )

        result = check.feature_checked("create-calendar")
        assert isinstance(result, bool)
        assert result is True

    def test_feature_checked_can_return_dict(self) -> None:
        """feature_checked with return_type=dict should return dict"""
        check = self.create_check_instance()
        check.checker._features_checked.copyFeatureSet(
            {"create-calendar": {"support": "full", "behaviour": "test"}}, collapse=False
        )

        result = check.feature_checked("create-calendar", dict)
        assert isinstance(result, dict)
        assert result == {"support": "full", "behaviour": "test"}


class TestCheckRunCheck:
    """Test the Check.run_check method and dependency resolution"""

    def test_run_check_executes_dependencies_first(self) -> None:
        """run_check should execute all dependencies before running main check"""
        # Create a dependency check
        class DependencyCheck(Check):
            executed = False
            features_to_be_checked = set()  # Must define this attribute

            def _run_check(self) -> None:
                DependencyCheck.executed = True

        # Create main check that depends on DependencyCheck
        class MainCheck(Check):
            depends_on = {DependencyCheck}
            features_to_be_checked = set()

            def _run_check(self) -> None:
                # Verify dependency was executed first
                assert DependencyCheck.executed

        checker = Mock()
        checker._features_checked = FeatureSet()
        checker._checks_run = set()
        checker._client_obj = Mock()
        checker._client_obj.features = FeatureSet()

        main_check = MainCheck(checker)
        main_check.run_check()

        assert DependencyCheck.executed

    def test_run_check_only_once_prevents_duplicate_execution(self) -> None:
        """run_check with only_once=True should not re-execute same check"""

        class TestCheck(Check):
            execution_count = 0
            features_to_be_checked = set()

            def _run_check(self) -> None:
                TestCheck.execution_count += 1

        checker = Mock()
        checker._features_checked = FeatureSet()
        checker._checks_run = set()
        checker._client_obj = Mock()
        checker._client_obj.features = FeatureSet()

        check1 = TestCheck(checker)
        check1.run_check(only_once=True)
        check1.run_check(only_once=True)

        assert TestCheck.execution_count == 1

    def test_run_check_only_once_false_allows_multiple_executions(self) -> None:
        """run_check with only_once=False should allow re-execution"""

        class TestCheck(Check):
            execution_count = 0
            features_to_be_checked = set()

            def _run_check(self) -> None:
                TestCheck.execution_count += 1

        checker = Mock()
        checker._features_checked = FeatureSet()
        checker._checks_run = set()
        checker._client_obj = Mock()
        checker._client_obj.features = FeatureSet()

        check1 = TestCheck(checker)
        check1.run_check(only_once=False)
        check2 = TestCheck(checker)
        check2.run_check(only_once=False)

        assert TestCheck.execution_count == 2

    def test_run_check_tracks_executed_checks(self) -> None:
        """run_check should add check class to _checks_run set"""

        class TestCheck(Check):
            features_to_be_checked = set()

            def _run_check(self) -> None:
                pass

        checker = Mock()
        checker._features_checked = FeatureSet()
        checker._checks_run = set()
        checker._client_obj = Mock()
        checker._client_obj.features = FeatureSet()

        check = TestCheck(checker)
        check.run_check()

        assert TestCheck in checker._checks_run

    def test_run_check_restores_client_features(self) -> None:
        """run_check should restore original client features after execution"""
        original_features = FeatureSet()
        original_features.copyFeatureSet({"create-calendar": {"support": "full"}}, collapse=False)

        class TestCheck(Check):
            features_to_be_checked = set()

            def _run_check(self) -> None:
                # Modify features during check
                self.checker._client_obj.features = FeatureSet()

        checker = Mock()
        checker._features_checked = FeatureSet()
        checker._checks_run = set()
        checker._client_obj = Mock()
        checker._client_obj.features = original_features

        check = TestCheck(checker)
        check.run_check()

        # Features should be restored to original
        assert checker._client_obj.features == original_features

    @pytest.mark.filterwarnings("ignore:Unknown feature:UserWarning:caldav")
    def test_run_check_verifies_declared_features_checked(self) -> None:
        """run_check should verify all declared features were checked"""
        # Uses intentionally fake feature names (feature1, feature2) to test
        # AssertionError logic; the UserWarning from caldav about unknown features is expected.

        class TestCheck(Check):
            features_to_be_checked = {"feature1", "feature2"}

            def _run_check(self) -> None:
                # Only check feature1, not feature2
                self.set_feature("feature1", True)

        checker = Mock()
        checker._features_checked = FeatureSet()
        checker._checks_run = set()
        checker._client_obj = Mock()
        checker._client_obj.features = FeatureSet()
        checker.debug_mode = None

        check = TestCheck(checker)

        # Should raise AssertionError for missing feature2
        with pytest.raises(AssertionError):
            check.run_check()

    def test_run_check_base_class_raises_not_implemented(self) -> None:
        """Calling run_check on base Check class should raise NotImplementedError"""
        checker = Mock()
        checker._features_checked = FeatureSet()
        checker._checks_run = set()
        checker._client_obj = Mock()
        checker._client_obj.features = FeatureSet()

        check = Check(checker)

        with pytest.raises(NotImplementedError):
            check.run_check()
