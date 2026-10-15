"""The pre-push hook guarding main: merging pushed work passes, a direct
commit is refused."""

import os
import subprocess
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parent.parent / "tools" / "check-push-to-main.sh"
ZERO = "0" * 40

## Run from a git hook (the pre-push pytest), GIT_DIR and friends point at
## the real repository: without them every command below would act on it.
ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
ENV["GIT_ALLOW_PROTOCOL"] = "file"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=ENV
    ).stdout.strip()


@pytest.fixture
def clone(tmp_path: Path) -> Path:
    remote = tmp_path / "remote.git"
    work = tmp_path / "work"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True, env=ENV)
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True, env=ENV)
    git(work, "config", "user.name", "Test")
    git(work, "config", "user.email", "test@example.com")
    git(work, "config", "commit.gpgsign", "false")
    git(work, "remote", "add", "origin", str(remote))
    commit(work, "base")
    git(work, "push", "-q", "origin", "main")
    return work


def commit(repo: Path, name: str) -> str:
    (repo / name).write_text(name)
    git(repo, "add", name)
    git(repo, "commit", "-q", "--no-verify", "-m", name)
    return git(repo, "rev-parse", "HEAD")


def run_hook(repo: Path, remote_branch: str = "refs/heads/main") -> subprocess.CompletedProcess:
    env = {
        **ENV,
        "PRE_COMMIT_REMOTE_NAME": "origin",
        "PRE_COMMIT_REMOTE_BRANCH": remote_branch,
        "PRE_COMMIT_FROM_REF": git(repo, "rev-parse", "origin/main"),
        "PRE_COMMIT_TO_REF": git(repo, "rev-parse", "HEAD"),
    }
    return subprocess.run(["sh", str(HOOK)], cwd=repo, env=env, capture_output=True, text=True)


def test_direct_commit_on_main_is_refused(clone: Path) -> None:
    commit(clone, "direct")
    result = run_hook(clone)
    assert result.returncode == 1
    assert "direct" in result.stdout


def test_merge_of_a_pushed_branch_passes(clone: Path) -> None:
    git(clone, "checkout", "-q", "-b", "topic")
    commit(clone, "one")
    commit(clone, "two")
    git(clone, "push", "-q", "origin", "topic")
    git(clone, "checkout", "-q", "main")
    git(clone, "merge", "-q", "--no-ff", "--no-verify", "-m", "Merge PR 1", "topic")
    assert run_hook(clone).returncode == 0


def test_fast_forward_to_a_pushed_branch_passes(clone: Path) -> None:
    git(clone, "checkout", "-q", "-b", "topic")
    commit(clone, "one")
    git(clone, "push", "-q", "origin", "topic")
    git(clone, "checkout", "-q", "main")
    git(clone, "merge", "-q", "--ff-only", "topic")
    assert run_hook(clone).returncode == 0


def test_merge_with_an_unpushed_commit_is_refused(clone: Path) -> None:
    git(clone, "checkout", "-q", "-b", "topic")
    commit(clone, "one")
    git(clone, "push", "-q", "origin", "topic")
    commit(clone, "unpushed")
    git(clone, "checkout", "-q", "main")
    git(clone, "merge", "-q", "--no-ff", "--no-verify", "-m", "Merge PR 1", "topic")
    result = run_hook(clone)
    assert result.returncode == 1
    assert "unpushed" in result.stdout


def test_direct_commit_on_another_remotes_main_is_refused(clone: Path) -> None:
    backup = clone.parent / "backup.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(backup)], check=True)
    git(clone, "remote", "add", "backup", str(backup))
    commit(clone, "direct")
    git(clone, "push", "-q", "backup", "main")
    assert run_hook(clone).returncode == 1


def test_missing_refs_fail_closed(clone: Path) -> None:
    """pre-commit exports no FROM/TO_REF for a push that reaches the root commit."""
    env = {**ENV, "PRE_COMMIT_REMOTE_BRANCH": "refs/heads/main"}
    env.pop("PRE_COMMIT_FROM_REF", None)
    env.pop("PRE_COMMIT_TO_REF", None)
    result = subprocess.run(["sh", str(HOOK)], cwd=clone, env=env, capture_output=True, text=True)
    assert result.returncode == 1


def test_other_branches_are_not_checked(clone: Path) -> None:
    git(clone, "checkout", "-q", "-b", "topic")
    commit(clone, "one")
    assert run_hook(clone, "refs/heads/topic").returncode == 0
