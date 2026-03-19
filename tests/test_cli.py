"""Tests for the CLI (caldav_server_tester.py click application)"""

import sys
import types
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from caldav_server_tester.caldav_server_tester import (
    _find_caldav_test_registry,
    check_server_compatibility,
)


class TestCliConfigSection:
    """Test --config-section CLI option"""

    def test_config_section_option_exists(self) -> None:
        """--config-section should be a valid CLI option"""
        runner = CliRunner()
        result = runner.invoke(check_server_compatibility, ["--help"])
        assert "--config-section" in result.output

    def test_config_section_is_passed_to_get_davclient(self) -> None:
        """--config-section value should be passed through to get_davclient"""
        runner = CliRunner()
        with (
            patch(
                "caldav_server_tester.caldav_server_tester._find_caldav_test_registry",
                return_value=None,
            ),
            patch(
                "caldav_server_tester.caldav_server_tester.get_davclient",
                return_value=None,
            ) as mock_get,
        ):
            runner.invoke(
                check_server_compatibility,
                ["--config-section", "myserver"],
            )
            mock_get.assert_called_once()
            call_kwargs = mock_get.call_args
            assert call_kwargs.kwargs.get("config_section") == "myserver" or (
                len(call_kwargs.args) > 0 and "myserver" in call_kwargs.args
            )


class TestCliListChecks:
    """Test --list-checks CLI option"""

    def test_list_checks_option_exists(self) -> None:
        """--list-checks should be a valid CLI option"""
        runner = CliRunner()
        result = runner.invoke(check_server_compatibility, ["--help"])
        assert "--list-checks" in result.output

    def test_list_checks_prints_class_names_and_exits(self) -> None:
        """--list-checks should print check class names without connecting to a server"""
        runner = CliRunner()
        result = runner.invoke(check_server_compatibility, ["--list-checks"])
        assert result.exit_code == 0
        assert "CheckSearch" in result.output
        assert "PrepareCalendar" not in result.output  # internal helper, not a real check

    def test_list_checks_output_is_sorted(self) -> None:
        """--list-checks output should be alphabetically sorted"""
        runner = CliRunner()
        result = runner.invoke(check_server_compatibility, ["--list-checks"])
        names = [line.strip() for line in result.output.splitlines() if line.strip()]
        assert names == sorted(names)


class TestCliCalendarOption:
    """Test --caldav-calendar CLI option"""

    def test_caldav_calendar_option_exists(self) -> None:
        """--caldav-calendar should be a valid CLI option"""
        runner = CliRunner()
        result = runner.invoke(check_server_compatibility, ["--help"])
        assert "--caldav-calendar" in result.output


class TestCliRunFeature:
    """Test --run-feature CLI option"""

    def test_run_feature_option_exists(self) -> None:
        """--run-feature should be a valid CLI option"""
        runner = CliRunner()
        result = runner.invoke(check_server_compatibility, ["--help"])
        assert "--run-feature" in result.output


class TestCliNameFallback:
    """Test --name falls back to config file when not found in registry"""

    def test_name_not_in_registry_falls_back_to_config(self) -> None:
        """When --name is given and not found in registry, should try config file"""
        runner = CliRunner()
        mock_registry = MagicMock()
        mock_registry.get.return_value = None  # name not found in registry

        with (
            patch(
                "caldav_server_tester.caldav_server_tester._find_caldav_test_registry",
                return_value=mock_registry,
            ),
            patch(
                "caldav_server_tester.caldav_server_tester.get_davclient",
                return_value=None,
            ) as mock_get,
        ):
            runner.invoke(
                check_server_compatibility,
                ["--name", "myserver"],
            )
            # Should have called get_davclient (config file path), not raised UsageError
            mock_get.assert_called_once()
            call_kwargs = mock_get.call_args
            assert call_kwargs.kwargs.get("name") == "myserver"

    def test_name_in_registry_uses_registry(self) -> None:
        """When --name is given and found in registry, should use registry server"""
        runner = CliRunner()
        mock_server = MagicMock()
        mock_server.get_sync_client.return_value.__enter__ = lambda s: MagicMock()
        mock_server.get_sync_client.return_value.__exit__ = MagicMock(return_value=False)
        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_server

        with (
            patch(
                "caldav_server_tester.caldav_server_tester._find_caldav_test_registry",
                return_value=mock_registry,
            ),
            patch("caldav_server_tester.caldav_server_tester._check_server") as mock_check,
        ):
            runner.invoke(
                check_server_compatibility,
                ["--name", "knownserver"],
            )
            mock_check.assert_called_once()

    def test_find_registry_works_when_tests_shadowed_in_sys_modules(self) -> None:
        """Registry discovery must succeed even when sys.modules['tests'] points elsewhere.

        When the CLI is installed and run, its own tests/ package ends up in
        sys.modules before _find_caldav_test_registry() runs.  That used to
        shadow the caldav project's tests/test_servers and cause the function
        to return None.
        """
        # Only meaningful if caldav is checked out as source (has tests/test_servers)
        from pathlib import Path

        import caldav

        caldav_root = Path(caldav.__file__).parent.parent
        if not (caldav_root / "tests" / "test_servers" / "__init__.py").exists():
            return  # skip — caldav is not a source checkout

        # Inject a fake conflicting 'tests' module that has no test_servers attr
        fake_tests = types.ModuleType("tests")
        fake_tests.__path__ = ["/some/unrelated/tests"]

        saved_tests = {k: sys.modules.pop(k) for k in list(sys.modules) if k == "tests" or k.startswith("tests.")}
        sys.modules["tests"] = fake_tests
        try:
            registry = _find_caldav_test_registry()
        finally:
            for k in list(sys.modules):
                if k == "tests" or k.startswith("tests."):
                    del sys.modules[k]
            sys.modules.update(saved_tests)

        assert registry is not None, (
            "_find_caldav_test_registry() returned None even though caldav source is available; "
            "the 'tests' shadowing bug was not fixed"
        )

    def test_name_lookup_is_case_insensitive(self) -> None:
        """--name radicale should match a registry entry named 'Radicale'"""
        runner = CliRunner()
        mock_server = MagicMock()
        mock_server.name = "Radicale"

        mock_registry = MagicMock()
        mock_registry.get.return_value = None  # exact match fails
        mock_registry.all_servers.return_value = [mock_server]

        with (
            patch(
                "caldav_server_tester.caldav_server_tester._find_caldav_test_registry",
                return_value=mock_registry,
            ),
            patch("caldav_server_tester.caldav_server_tester._check_server") as mock_check,
        ):
            runner.invoke(
                check_server_compatibility,
                ["--name", "radicale"],
            )
            mock_check.assert_called_once()
