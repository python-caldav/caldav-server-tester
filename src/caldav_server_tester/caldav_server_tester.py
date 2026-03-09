#!/usr/bin/env python

"""
This is the CLI - the "click" application
"""

import importlib.metadata

import click
from caldav.davclient import get_davclient

from .checker import ServerQuirkChecker

try:
    __version__ = importlib.metadata.version("caldav-server-tester")
except importlib.metadata.PackageNotFoundError:
    __version__ = "unknown"


@click.command()
@click.version_option(version=__version__, prog_name="caldav-server-tester")
@click.option("--name", type=str, help="Choose a server by name", default=None)
@click.option("--verbose/--quiet", default=None, help="More output")
@click.option("--json/--text", help="JSON output.  Overrides verbose")
## TODO: lines below has been copied from the plann library.
## TODO: the list is also incomplete
## TODO: Should probably consider to consolidate a bit
# @_set_conn_options ## defined in the caldav_server_tester_old.py file, but needs refactoring.
@click.option("--caldav-url", help="Full URL to the caldav server", metavar="URL")
@click.option(
    "--caldav-username",
    "--caldav-user",
    help="Full URL to the caldav server",
    metavar="URL",
)
@click.option(
    "--caldav-password",
    "--caldav-pass",
    help="Password for the caldav server",
    metavar="URL",
)
@click.option(
    "--caldav-features",
    help="Server compatibility features preset (e.g., 'bedework', 'zimbra', 'sogo')",
    metavar="FEATURES",
)
# @click.option("--check-features", help="List of features to test")
@click.option("--run-checks", help="List of checks to run", multiple=True)
def check_server_compatibility(verbose, json, name, run_checks, **kwargs):
    click.echo("WARNING: this script is not production-ready")

    ## Remove empty keys
    conn_keys = {}
    for x in kwargs:
        if x.startswith("caldav_") and kwargs[x]:
            conn_keys[x[7:]] = kwargs[x]
    conn = get_davclient(name=name, testconfig=True, **conn_keys)
    if conn is None:
        raise click.UsageError(
            f"No configuration found for {name!r}. "
            "Check your caldav client config file."
            if name
            else "No server specified. Use --name or --caldav-url."
        )
    with conn:
        obj = ServerQuirkChecker(conn)
        if not run_checks:
            obj.check_all()
        for check in run_checks:
            obj.check_one(check)
    obj.cleanup(force=False)
    click.echo(obj.report(verbose=verbose, return_what="json" if json else str))

if __name__ == "__main__":
    check_server_compatibility()
