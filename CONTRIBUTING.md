# Contributing to caldav-server-tester

Contributions are mostly welcome.  If the length of the text scares you, then skip reading and spend your time contributing instead.  GitHub is (as for now) the official platform for tracking contributions and issues, but feel free to reach out e.g. by email if you for various reasons don't want to use GitHub.

Creating a check is quite time-consuming, creating one with test code is even harder.  When it comes to the checks, quantity matters - it may be a good thing to have five checks that catch all the nuances around some server behaviour.  In this project AI-contributions are generally accepted, and most of the recent code is AI-generated.  However, it's important with proper QA.  Checks reporting the wrong things or non-deterministic checks may cause me significant costs down the line.

The AI-policy from pycal.org applies to this project - https://pycal.org/ai-policy/ - in particular, transparency about AI-usage is important, and the commit messages should include some information about prompts and model used.  If you use the AI to fix a bug, the most important thing is that you confirm that the fix solves your problem.

## Changes to the feature label hierarchy

New features to be tested also need changes in the caldav project.  Please coordinate this with Tobias - or at least, create pull requests from the same branch name in both projects.  Those changes need serious thinking and a good decision from a human being.  It may be a good idea to ask the AI about suggestions, an even better idea to ask the AI to do a QA on whatever suggestion you can come up with, but don't trust the AI to get this right.  Discuss with Tobias if in doubt.


## What to include

Every submission should ideally include some test code, documentation and a changelog entry.  In this project, inline-documentation and (for new features tested) descriptions in the [caldav compatibility_hints.py file](https://github.com/python-caldav/caldav/blob/master/caldav/compatibility_hints.py) suffice for the documentation.  I've traditionally not bothered adding much test code to the project - the compatibility test under the caldav project will run through all the checks - however, it's a cheap thing to do when it can be generated through the AI.

## Commit messages

Please follow [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) and write messages in the imperative mood:

- `feat: add free-busy query check`
- `docs: update USAGE.md with --diff flag example`
- `fix: correct time-range search for recurring events`

Rather than:

- `This commit fixes the time-range search`
- `Added free-busy query check`

The 50/72-rule should be observed, try to keep the commit headers under 50 characters, and never longer than 72 characters.

If using e.g. Claude Opus, it usually stuffs really lots of irrelevant information into the commit message, and it may be hard to find the essence in the commit message.  It's probably better to rewrite it by hand.  Good commit messages are important, but "good" doesn't imply "long".  Try to focus on "why" rather than "what" - the "what"-part is usually readable from the diff.

Note: older commits in this repository predate those conventions and do not follow them.

## Reporting bugs

Open an issue on GitHub.  Include the server name/version and the output of `caldav-server-tester --version`.
