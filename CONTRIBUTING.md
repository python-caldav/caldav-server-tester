# Contributing to caldav-server-tester

Contributions are mostly welcome (but do inform about it if you've used AI or other tools).  If the length of this text scares you, then I'd rather want you to skip reading and just produce a pull-request in GitHub.  If you find it too difficult to write test code, etc, then you may skip it and hope the maintainer will fix it.

## What to include

Every submission should ideally include:

- **Test code** covering the new behaviour or bug fix
- **Documentation** updates where relevant (README, USAGE.md)
- **A changelog entry** in `CHANGELOG.md` under `[Unreleased]`

## Commit messages

Please follow [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) and write messages in the imperative mood:

- `fix: correct time-range search handling for recurring events`
- `feat: add free-busy query check`
- `docs: update USAGE.md with --diff flag example`

Rather than:

- `This commit fixes the time-range search`
- `Added free-busy query check`

Note: older commits in this repository predate this convention and do not follow it.

## Reporting bugs

Open an issue on GitHub.  Include the server name/version and the output of `caldav-server-tester --version`.
