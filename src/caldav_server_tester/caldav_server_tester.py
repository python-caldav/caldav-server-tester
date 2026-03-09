#!/usr/bin/env python

"""
This is the CLI - the "click" application
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
    """Run the configured checks against a connection and print the report."""
    obj = ServerQuirkChecker(conn)
    if not run_checks:
        obj.check_all()
    for check in run_checks:
        obj.check_one(check)
    obj.cleanup(force=False)
    return obj


def _check_server(server, run_checks, verbose, output_json):
    """Start a TestServer (if needed), run checks, stop it, and print the report."""
    server.start()
    try:
        client = server.get_sync_client()
        with client:
            obj = _run_checks_against(client, run_checks)
    finally:
        server.stop()
    click.echo(obj.report(verbose=verbose, return_what="json" if output_json else str))


@click.command()
@click.version_option(version=__version__, prog_name="caldav-server-tester")
@click.option("--name", type=str, help="Server name (from test registry or config)", default=None)
@click.option("--verbose/--quiet", default=None, help="More output")
@click.option("--json/--text", help="JSON output.  Overrides verbose")
@click.option("--yes", "-y", is_flag=True, default=False,
              help="Skip interactive confirmation for non-test external servers")
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
def check_server_compatibility(verbose, json, yes, name, run_checks, **kwargs):
    click.echo("WARNING: this script is not production-ready")

    ## Collect explicit connection keys from --caldav-* options
    conn_keys = {k[7:]: v for k, v in kwargs.items() if k.startswith("caldav_") and v}

    ## If an explicit URL was given, use it directly (with confirmation)
    if conn_keys.get("url"):
        if not yes:
            click.confirm(
                f"Run checks against {conn_keys['url']}? "
                "This will create and delete test calendar data on the server.",
                abort=True,
            )
        conn = get_davclient(**conn_keys)
        if conn is None:
            raise click.UsageError(f"Could not connect to {conn_keys['url']}")
        with conn:
            obj = _run_checks_against(conn, run_checks)
        obj.cleanup(force=False)
        click.echo(obj.report(verbose=verbose, return_what="json" if json else str))
        return

    ## Try to use the caldav test server registry
    registry = _find_caldav_test_registry()

    if registry is not None:
        if name:
            server = registry.get(name)
            if server is None:
                raise click.UsageError(
                    f"No test server named {name!r} found in the registry. "
                    f"Available: {[s.name for s in registry.all_servers()]}"
                )
            servers = [server]
        else:
            servers = registry.enabled_servers()

        if not servers:
            raise click.UsageError(
                "No enabled test servers found. "
                "Install radicale/xandikos or configure test_servers.yaml."
            )

        for server in servers:
            ## Embedded and docker servers are safe (ephemeral data, started by us).
            ## External servers without testing_allowed need explicit confirmation.
            needs_confirmation = server.server_type == "external"
            if needs_confirmation and not yes:
                click.confirm(
                    f"Run checks against external server {server.name!r} ({server.url})? "
                    "This will create and delete test calendar data on the server. "
                    "(Use --yes to suppress this prompt.)",
                    abort=True,
                )
            _check_server(server, run_checks, verbose, json)
        return

    ## Fall back to the caldav config-file / testconfig path
    conn = get_davclient(name=name, testconfig=True, **conn_keys)
    if conn is None:
        raise click.UsageError(
            f"No configuration found for {name!r}. "
            "Check your caldav client config file."
            if name
            else "No server specified. Use --name, --caldav-url, or run from a caldav source checkout."
        )
    with conn:
        obj = _run_checks_against(conn, run_checks)
    obj.cleanup(force=False)
    click.echo(obj.report(verbose=verbose, return_what="json" if json else str))


if __name__ == "__main__":
    check_server_compatibility()
