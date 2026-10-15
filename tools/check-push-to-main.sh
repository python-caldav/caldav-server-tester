#!/bin/sh
# pre-push: main takes only work pushed to some other branch first.  Every
# commit a push brings to main, merge commits aside, must already be on a
# remote branch other than main/master (a PR branch, or a fork's PR ref
# fetched into refs/remotes/).  Merge commits themselves are not checked.
# Releases bypass it with --no-verify.
#
# Limitation: pre-commit runs pre-push hooks for the first pushed ref with
# something new only, so `git push origin topic main` does not check main.
# Push main on its own.
case "$PRE_COMMIT_REMOTE_BRANCH" in
    refs/heads/main | refs/heads/master) ;;
    *) exit 0 ;;
esac
if [ -z "$PRE_COMMIT_TO_REF" ]; then
    echo "No commit range for the push to ${PRE_COMMIT_REMOTE_BRANCH#refs/heads/} (a new history?); use --no-verify if intended."
    exit 1
fi
case "$PRE_COMMIT_FROM_REF" in
    "" | 0000000000000000000000000000000000000000) set -- ;;
    *) set -- "$PRE_COMMIT_FROM_REF" ;;
esac
direct=$(git rev-list --no-merges --format=oneline "$PRE_COMMIT_TO_REF" --not "$@" \
    --exclude='*/main' --exclude='*/master' --exclude='*/HEAD' --remotes) || exit 1
if [ -n "$direct" ]; then
    echo "Commits pushed to ${PRE_COMMIT_REMOTE_BRANCH#refs/heads/} that are on no remote branch:"
    echo "$direct"
    echo "Push them to a PR branch and merge that, or use --no-verify for a release."
    exit 1
fi
