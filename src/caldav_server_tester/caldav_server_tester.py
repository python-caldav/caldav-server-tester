#!/usr/bin/env python

"""
This is the CLI - the "click" application

TODO: make a new cli.py file with the bare-bones click logic.
"""

import importlib.metadata
import sys
from pathlib import Path

import click
from caldav.davclient import get_davclient

from .checker import ServerQuirkChecker

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
        if (root / "tests" / "test_servers" / "__init__.py").exists():
            if str(root) not in sys.path:
                sys.path.insert(0, str(root))
            try:
                from tests.test_servers import get_registry

                return get_registry()
            except ImportError:
                pass

    return None


def _run_checks_against(conn, run_checks):
    """Run the configured checks against a connection and return the checker object."""
    obj = ServerQuirkChecker(conn)
    if not run_checks:
        obj.check_all()
    for check in run_checks:
        obj.check_one(check)
    return obj


def _emit_report(obj, verbose, output_format, show_diff):
    """Print the report in the requested format."""
    return_what = {"json": "json", "yaml": "yaml", "hints": "hints"}.get(output_format, str)
    click.echo(obj.report(verbose=verbose, show_diff=show_diff, return_what=return_what))


def _check_server(server, run_checks, verbose, output_format, show_diff, no_cleanup):
    """Start a TestServer (if needed), run checks, stop it, and print the report."""
    server.start()
    try:
        client = server.get_sync_client()
        with client:
            obj = _run_checks_against(client, run_checks)
            if not no_cleanup:
                obj.cleanup(force=True)
    finally:
        server.stop()
    _emit_report(obj, verbose, output_format, show_diff)


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
    "--config-section", default=None, help="Section name in caldav config file (default: 'default')", metavar="SECTION"
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
@click.option("--run-checks", help="Specific check(s) to run", multiple=True)
def check_server_compatibility(
    verbose, output_format, show_diff, no_cleanup, name, config_section, run_checks, **kwargs
):
    ## Collect explicit connection keys from --caldav-* options
    conn_keys = {k[7:]: v for k, v in kwargs.items() if k.startswith("caldav_") and v}

    ## If an explicit URL was given, use it directly
    if conn_keys.get("url"):
        conn = get_davclient(**conn_keys)
        if conn is None:
            raise click.UsageError(f"Could not connect to {conn_keys['url']}")
        with conn:
            obj = _run_checks_against(conn, run_checks)
            if not no_cleanup:
                obj.cleanup(force=True)
        _emit_report(obj, verbose, output_format, show_diff)
        return

    ## If `--name` is used, we should try to look up the name in the
    ## caldav test server registry ... if such a thing exists
    if name:
        registry = _find_caldav_test_registry()
        if registry is not None:
            server = registry.get(name)
            if server is not None:
                _check_server(server, run_checks, verbose, output_format, show_diff, no_cleanup)
                return

    ## Fall back to the caldav config-file / testconfig path
    conn = get_davclient(name=name, config_section=config_section or name, **conn_keys)
    if conn is None:
        raise click.UsageError(
            f"No configuration found for {name!r}. "
            "Check your caldav client config file (~/.config/caldav/calendar.conf)."
            if name
            else "No server specified. Use --name, --caldav-url, or configure ~/.config/caldav/calendar.conf."
        )
    with conn:
        obj = _run_checks_against(conn, run_checks)
        if not no_cleanup:
            obj.cleanup(force=True)
    _emit_report(obj, verbose, output_format, show_diff)


if __name__ == "__main__":
    check_server_compatibility()
