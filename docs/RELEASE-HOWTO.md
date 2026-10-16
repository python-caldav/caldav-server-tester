# How to make a release

The tester is released in lockstep with the [caldav library](https://github.com/python-caldav/caldav): new probes usually need new feature definitions in caldav's `compatibility_hints.py`, so caldav is released first and the tester's `caldav>=` floor in `pyproject.toml` follows it.

## Checklist

* Every pull request meant for the release is merged, and CI is green on `main`.
* Run the regression check below against a caldav checkout, and investigate what it finds.  Do it before the caldav release, so the hint updates it calls for ship with that release; anything found later waits for the next caldav release.
* The caldav release the tester needs is on PyPI, and `pyproject.toml` requires it.
* The `## Unreleased` section of `CHANGELOG.md` covers every user-visible change since the last tag, and nothing introduced and fixed in between.  It opens with "This release works with caldav X.Y.Z.", and a raised floor also gets a "Requires caldav X.Y.Z." bullet under Changed.
* Rename `## Unreleased` to `## X.Y.Z - YYYY-MM-DD` and commit it.  This commit is made directly on `main`, which the `no-push-to-main` pre-push hook refuses, so push it with `SKIP=no-push-to-main git push origin main`.  That skips only this hook; the tests and the link check still run.  `git push --no-verify` would skip them too.
* Tag `vX.Y.Z` and push the tag.  The publish workflow builds the package and uploads it to PyPI.

## Regression check against real servers

A unit test cannot tell whether a probe still grades a real server the way it did.  Run the previous release and the new code against the same servers, and compare the results.

1. Use a caldav checkout whose test-server registry (`tests/caldav_test_servers.yaml`) lists the servers to test, external accounts included.  `--name` reads that file relative to the current directory, so run everything from that checkout.  Start its docker test servers first: a run stops only the containers it started itself, so otherwise every run starts and stops them on its own.
2. Check which caldav and which tester the console script imports.  An editable install of a caldav checkout shadows the released library, and that is what the run then tests against.  Compare the paths, not the versions, which can be stale in an editable install:
   ```sh
   $(head -1 $(which caldav-server-tester) | cut -c3-) -c 'import caldav, caldav_server_tester as t; print(caldav.__file__, t.__file__)'
   ```
3. Install the previous release in a venv of its own that sees the system packages, so both runs use the same caldav:
   ```sh
   python -m venv --system-site-packages /tmp/tester-old
   /tmp/tester-old/bin/pip install --no-deps caldav-server-tester==PREVIOUS
   ```
4. Run both, one output file per server, e.g. `caldav-server-tester --name SERVER --diff > new/SERVER.txt` and the same with `/tmp/tester-old/bin/caldav-server-tester` into `old/`.  Different servers can run in parallel, but run the old and the new tester against the same server one after the other: both use the same test calendar, and clean up each other's data.  A server with a search-cache delay can take an hour.
5. For each server, compare the `Diff (expected vs observed)` sections of the two runs:
   * A feature only the new release probes, or a deviation the new release no longer reports, is expected.
   * A feature both releases probe but grade differently needs explaining.  Re-run that server alone before blaming the code: external servers have bad minutes, and on servers with delayed (asynchronous) writes the results vary from run to run.
   * A run that crashes before any check ran, at the first PROPFIND, is usually the server; re-run it.
6. Where the new probes find something the caldav hints for that server do not say, update `compatibility_hints.py` in caldav.  Base it on the observed behaviour text, and on more than one run for anything timing-dependent.
