#!/usr/bin/env python

"""
This is the CLI - the "click" application

TODO: make a new cli.py file with the bare-bones click logic.
"""

import importlib.metadata
import inspect
import logging
import sys
from pathlib import Path

import click
from caldav.davclient import get_davclient

from . import checks as checks_module
from .checker import ServerQuirkChecker
from .checks_base import Check

try:
    __version__ = importlib.metadata.version("caldav-server-tester")
except importlib.metadata.PackageNotFoundError:
    __version__ = "unknown"


def _find_caldav_test_registry():
    """
    Try to import the caldav test server registry.

    Searches for a caldav source checkout by looking at:
    1. The directory containing the installed caldav package
    2. The current working directory

    Returns a ServerRegistry instance, or None if not found.
    """
    import caldav

    candidates = [
        Path(caldav.__file__).parent.parent,  # e.g. ~/caldav/ when caldav pkg is ~/caldav/caldav/
        Path.cwd(),
    ]

    for root in candidates:
        ts_init = root / "tests" / "test_servers" / "__init__.py"
        if not ts_init.exists():
            continue
        ## Use importlib to load directly from the file path, bypassing
        ## sys.path resolution entirely.  A plain `from tests.test_servers
        ## import …` fails when another tests/ directory (e.g. this project's
        ## own tests/ via CWD or editable install) appears earlier in sys.path.
        import importlib.util

        tests_spec = importlib.util.spec_from_file_location(
            "tests",
            str(root / "tests" / "__init__.py"),
            submodule_search_locations=[str(root / "tests")],
        )
        ts_spec = importlib.util.spec_from_file_location(
            "tests.test_servers",
            str(ts_init),
            submodule_search_locations=[str(ts_init.parent)],
        )
        if tests_spec is None or ts_spec is None:
            continue

        _saved = {k: sys.modules.pop(k) for k in list(sys.modules) if k == "tests" or k.startswith("tests.")}
        try:
            tests_mod = importlib.util.module_from_spec(tests_spec)
            sys.modules["tests"] = tests_mod
            tests_spec.loader.exec_module(tests_mod)  # type: ignore[union-attr]

            ts_mod = importlib.util.module_from_spec(ts_spec)
            sys.modules["tests.test_servers"] = ts_mod
            ts_spec.loader.exec_module(ts_mod)  # type: ignore[union-attr]

            return ts_mod.get_registry()
        except Exception:
            for k in list(sys.modules):
                if k == "tests" or k.startswith("tests."):
                    del sys.modules[k]
            sys.modules.update(_saved)

    return None


def _list_check_classes() -> list[str]:
    """Return sorted list of available (non-internal) check class names."""
    return sorted(
        name
        for name, obj in inspect.getmembers(checks_module, inspect.isclass)
        if obj.__module__ == checks_module.__name__
        and issubclass(obj, Check)
        and obj is not Check
        and name != "PrepareCalendar"
    )


def _feature_to_check_name(feature: str) -> str | None:
    """Return the check class name whose features_to_be_checked covers the given feature."""
    for cls_name, obj in inspect.getmembers(checks_module, inspect.isclass):
        if (
            obj.__module__ == checks_module.__name__
            and issubclass(obj, Check)
            and obj is not Check
            and feature in getattr(obj, "features_to_be_checked", set())
        ):
            return cls_name
    return None


def _resolve_check_list(run_checks, run_features):
    """Resolve the explicit checks plus any checks covering --run-feature.

    Raises click.UsageError for an unknown feature (before any server work), so
    that error stays clean and never triggers cleanup/report.
    """
    all_checks = list(run_checks)
    for feature in run_features:
        check_name = _feature_to_check_name(feature)
        if check_name is None:
            raise click.UsageError(
                f"No check found for feature {feature!r}. Use --list-checks to see available checks."
            )
        if check_name not in all_checks:
            all_checks.append(check_name)
    return all_checks


def _run_lifecycle(
    conn, run_checks, run_features, verbose, output_format, show_diff, no_cleanup, calendar=None, extra_clients=None
):
    """Run the configured checks, then ALWAYS clean up and emit the report.

    The run/cleanup/report sequence is wrapped in try/finally so that:
      * a check raising (e.g. an AssertionError) does not skip cleanup or
        discard the results gathered so far, and
      * a cleanup that itself raises (e.g. a server answering the calendar
        DELETE with 500) does not swallow the report.
    The original exception, if any, still propagates after cleanup+report.
    """
    all_checks = _resolve_check_list(run_checks, run_features)

    obj = ServerQuirkChecker(conn, extra_clients=extra_clients)
    if calendar:
        obj.expected_features.set_feature("test-calendar.compatibility-tests", {"name": calendar})

    try:
        if not all_checks:
            obj.check_all()
        for check in all_checks:
            obj.check_one(check)
    finally:
        if not no_cleanup:
            try:
                obj.cleanup(force=True)
            except Exception as exc:
                logging.warning("Cleanup failed; test fixtures may remain on the server: %s", exc)
        _emit_report(obj, verbose, output_format, show_diff)


def _emit_report(obj, verbose, output_format, show_diff):
    """Print the report in the requested format."""
    return_what = output_format if output_format in ("json", "yaml", "hints") else str
    click.echo(obj.report(verbose=verbose, show_diff=show_diff, return_what=return_what))


def _do_cleanup_only(conn, calendar_name):
    """Purge leftover test fixtures (incl. aged-out probes) and report the count.

    Fails loudly (non-zero exit) when the purge hit errors, so the user is never
    told the server is clean while listing or deletion actually failed.
    """
    obj = ServerQuirkChecker(conn)
    removed, errors = obj.cleanup_test_data(calendar_name=calendar_name)
    click.echo(f"Removed {removed} caldav-server-tester object(s).")
    if errors:
        raise click.ClickException(
            f"{errors} error(s) occurred during cleanup; some test data may remain on the server. "
            "See the warnings above for details."
        )


def _check_server(
    server, run_checks, run_features, verbose, output_format, show_diff, no_cleanup, calendar=None, cleanup_only=False
):
    """Start a TestServer (if needed), run checks, stop it, and print the report."""
    from caldav.davclient import DAVClient

    server.start()
    try:
        main_client = server.get_sync_client()
        if cleanup_only:
            with main_client:
                _do_cleanup_only(main_client, calendar)
            return
        ## Create extra clients from scheduling_users (skipping index 0 which may be the primary user)
        scheduling_users = server.config.get("scheduling_users", [])
        extra_clients = []
        for user_params in scheduling_users[1:]:
            params = {k: v for k, v in user_params.items() if k in ("url", "username", "password", "ssl_verify_cert")}
            try:
                ec = DAVClient(**params)
                ec.__enter__()
                extra_clients.append(ec)
            except Exception:
                pass
        try:
            with main_client:
                _run_lifecycle(
                    main_client,
                    run_checks,
                    run_features,
                    verbose,
                    output_format,
                    show_diff,
                    no_cleanup,
                    calendar=calendar,
                    extra_clients=extra_clients,
                )
        finally:
            for ec in extra_clients:
                try:
                    ec.__exit__(None, None, None)
                except Exception:
                    pass
    finally:
        server.stop()


@click.command()
@click.version_option(version=__version__, prog_name="caldav-server-tester")
@click.option("--name", type=str, help="Server name (from test registry or config)", default=None)
@click.option("--verbose/--quiet", default=None, help="More output")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["text", "json", "yaml", "hints"], case_sensitive=False),
    default="text",
    help="Output format (text, json, yaml, or hints for compatibility_hints.py snippet)",
)
@click.option(
    "--diff", "show_diff", is_flag=True, default=False, help="Show diff between expected and observed features"
)
@click.option("--no-cleanup", is_flag=True, default=False, help="Do not remove test data after run")
@click.option(
    "--cleanup-only",
    is_flag=True,
    default=False,
    help="Only remove leftover test data (incl. fixtures aged out of a sliding window), then exit without running checks",
)
@click.option(
    "--config-section",
    multiple=True,
    default=(),
    help="Section name in caldav config file (default: 'default'). Repeat to add extra user accounts for multi-user checks.",
    metavar="SECTION",
)
@click.option("--caldav-url", help="Full URL to the caldav server", metavar="URL")
@click.option(
    "--caldav-username",
    "--caldav-user",
    help="Username for the caldav server",
    metavar="USERNAME",
)
@click.option(
    "--caldav-password",
    "--caldav-pass",
    help="Password for the caldav server",
    metavar="PASSWORD",
)
@click.option(
    "--caldav-features",
    help="Server compatibility features preset (e.g., 'bedework', 'zimbra', 'sogo')",
    metavar="FEATURES",
)
@click.option(
    "--caldav-calendar",
    help="Calendar display name to use for testing (required for servers without MKCALENDAR support)",
    metavar="CALENDAR",
)
@click.option("--list-checks", is_flag=True, default=False, help="List available check class names and exit")
@click.option("--run-checks", help="Specific check class(es) to run", multiple=True)
@click.option("--run-feature", "run_features", help="Run check(s) covering a specific feature", multiple=True)
def check_server_compatibility(
    verbose,
    output_format,
    show_diff,
    no_cleanup,
    cleanup_only,
    name,
    config_section,
    list_checks,
    run_checks,
    run_features,
    caldav_calendar,
    **kwargs,
):
    if list_checks:
        for cls_name in _list_check_classes():
            click.echo(cls_name)
        return

    ## Collect explicit connection keys from --caldav-* options (excluding --caldav-calendar)
    conn_keys = {k[7:]: v for k, v in kwargs.items() if k.startswith("caldav_") and v}

    ## If an explicit URL was given, use it directly
    if conn_keys.get("url"):
        conn = get_davclient(**conn_keys)
        if conn is None:
            raise click.UsageError(f"Could not connect to {conn_keys['url']}")
        with conn:
            if cleanup_only:
                _do_cleanup_only(conn, caldav_calendar)
                return
            _run_lifecycle(
                conn,
                run_checks,
                run_features,
                verbose,
                output_format,
                show_diff,
                no_cleanup,
                calendar=caldav_calendar,
            )
        return

    ## If `--name` is used, we should try to look up the name in the
    ## caldav test server registry ... if such a thing exists
    if name:
        registry = _find_caldav_test_registry()
        if registry is not None:
            server = registry.get(name)
            if server is None:
                ## Case-insensitive fallback — registry names may be capitalised
                ## (e.g. "Radicale") while users naturally type lowercase
                for s in registry.all_servers():
                    if s.name.lower() == name.lower():
                        server = s
                        break
            if server is not None:
                _check_server(
                    server,
                    run_checks,
                    run_features,
                    verbose,
                    output_format,
                    show_diff,
                    no_cleanup,
                    calendar=caldav_calendar,
                    cleanup_only=cleanup_only,
                )
                return

    ## Fall back to the caldav config-file / testconfig path
    primary_section = config_section[0] if config_section else name
    conn = get_davclient(name=name, config_section=primary_section, **conn_keys)
    if conn is None:
        raise click.UsageError(
            f"No configuration found for {name!r}. "
            "Check your caldav client config file (~/.config/caldav/calendar.conf)."
            if name
            else "No server specified. Use --name, --caldav-url, or configure ~/.config/caldav/calendar.conf."
        )

    ## Build extra clients from additional --config-section values
    extra_clients = []
    for section in config_section[1:]:
        ec = get_davclient(config_section=section)
        if ec is None:
            raise click.UsageError(f"No configuration found for extra config-section {section!r}.")
        ec.__enter__()
        extra_clients.append(ec)

    try:
        with conn:
            if cleanup_only:
                _do_cleanup_only(conn, caldav_calendar)
                return
            _run_lifecycle(
                conn,
                run_checks,
                run_features,
                verbose,
                output_format,
                show_diff,
                no_cleanup,
                calendar=caldav_calendar,
                extra_clients=extra_clients,
            )
    finally:
        for ec in extra_clients:
            try:
                ec.__exit__(None, None, None)
            except Exception:
                pass


if __name__ == "__main__":
    check_server_compatibility()
