"""Tests for the git operations layer (_git.py).

Uses real git repos in tmp_path to test actual git behaviour — no mocking
of subprocess.  This ensures guardrails work against real git state.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from haute._git import (
    GitError,
    GitGuardrailError,
    _build_compare_url,
    _generate_commit_message,
    _get_current_branch,
    _get_default_branch,
    _get_default_branch_cached,
    _get_user_slug,
    _is_own_branch,
    _is_protected,
    _slugify,
    _validate_ref_name,
    archive_branch,
    create_branch,
    delete_branch,
    get_history,
    get_status,
    list_branches,
    pull_latest,
    revert_to,
    save_progress,
    submit_for_review,
    switch_branch,
)

from tests._git_helpers import git_run as _git, init_repo as _init_repo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_repo_with_remote(path: Path, *, user: str = "Test User") -> tuple[Path, Path]:
    """Create a repo + a bare remote, linked via 'origin'."""
    remote = path / "remote.git"
    remote.mkdir()
    _git(remote, "init", "--bare", "-b", "main")

    repo = path / "repo"
    repo.mkdir()
    _init_repo(repo, user=user)
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "-u", "origin", "main")
    return repo, remote


# ---------------------------------------------------------------------------
# Slugify
# ---------------------------------------------------------------------------


class TestSlugify:
    def test_basic(self) -> None:
        assert _slugify("Update area factors") == "update-area-factors"

    def test_special_chars(self) -> None:
        assert _slugify("Fix postcode (v2)") == "fix-postcode-v2"

    def test_leading_trailing_dashes(self) -> None:
        assert _slugify("---hello---") == "hello"

    def test_empty_returns_user(self) -> None:
        assert _slugify("") == "user"

    def test_numbers(self) -> None:
        assert _slugify("Add NCD step 3") == "add-ncd-step-3"


# ---------------------------------------------------------------------------
# Commit message generation
# ---------------------------------------------------------------------------


class TestGenerateCommitMessage:
    def test_empty(self) -> None:
        assert _generate_commit_message([]) == "Save progress"

    def test_single_py(self) -> None:
        assert _generate_commit_message(["main.py"]) == "Updated main"

    def test_multiple_files(self) -> None:
        msg = _generate_commit_message(["main.py", "modules/scoring.py"])
        assert "main" in msg
        assert "scoring" in msg

    def test_many_files(self) -> None:
        files = [f"file{i}.py" for i in range(5)]
        msg = _generate_commit_message(files)
        assert "5 files" in msg

    def test_config_json(self) -> None:
        msg = _generate_commit_message(["config/banding/area.json"])
        assert "config/area" in msg

    def test_sidecar_skipped(self) -> None:
        msg = _generate_commit_message(["main.haute.json"])
        assert msg == "Save progress"

    def test_sidecar_with_real_file(self) -> None:
        msg = _generate_commit_message(["main.py", "main.haute.json"])
        assert msg == "Updated main"


# ---------------------------------------------------------------------------
# Branch ownership
# ---------------------------------------------------------------------------


class TestIsOwnBranch:
    def test_own_branch(self) -> None:
        assert _is_own_branch("pricing/test-user/my-feature", "test-user")

    def test_other_branch(self) -> None:
        assert not _is_own_branch("pricing/other-user/feature", "test-user")

    def test_main_is_not_own(self) -> None:
        assert not _is_own_branch("main", "test-user")

    def test_archive_is_not_own(self) -> None:
        assert not _is_own_branch("archive/old-feature", "test-user")


# ---------------------------------------------------------------------------
# Compare URL generation
# ---------------------------------------------------------------------------


class TestBuildCompareUrl:
    def test_github_ssh(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _git(repo, "remote", "add", "origin", "git@github.com:org/repo.git")
        url = _build_compare_url("pricing/user/feat", "main", repo)
        assert url == "https://github.com/org/repo/compare/main...pricing/user/feat"

    def test_github_https(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _git(repo, "remote", "add", "origin", "https://github.com/org/repo.git")
        url = _build_compare_url("pricing/user/feat", "main", repo)
        assert url == "https://github.com/org/repo/compare/main...pricing/user/feat"

    def test_gitlab(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _git(repo, "remote", "add", "origin", "https://gitlab.com/org/repo.git")
        url = _build_compare_url("pricing/user/feat", "main", repo)
        assert url is not None
        assert "merge_requests/new" in url

    def test_no_remote(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        url = _build_compare_url("feat", "main", repo)
        assert url is None


# ---------------------------------------------------------------------------
# get_status
# ---------------------------------------------------------------------------


class TestGetStatus:
    def test_on_main(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        s = get_status(repo)
        assert s.branch == "main"
        assert s.is_main is True
        assert s.is_read_only is True
        assert s.changed_files == []

    def test_changed_files(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        (repo / "new.py").write_text("x = 1\n")
        s = get_status(repo)
        assert "new.py" in s.changed_files

    def test_on_own_branch(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _git(repo, "checkout", "-b", "pricing/test-user/my-feat")
        s = get_status(repo)
        assert s.branch == "pricing/test-user/my-feat"
        assert s.is_main is False
        assert s.is_read_only is False

    def test_on_other_branch(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _git(repo, "checkout", "-b", "pricing/other-user/their-feat")
        s = get_status(repo)
        assert s.is_read_only is True

    def test_not_git_repo(self, tmp_path: Path) -> None:
        with pytest.raises(GitError, match="Not a git repository"):
            get_status(tmp_path)


# ---------------------------------------------------------------------------
# create_branch
# ---------------------------------------------------------------------------


class TestCreateBranch:
    def test_creates_and_switches(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        branch = create_branch("Update area factors", repo)
        assert branch == "pricing/test-user/update-area-factors"
        assert _get_current_branch(repo) == branch

    def test_empty_description(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        with pytest.raises(GitError, match="empty"):
            create_branch("", repo)

    def test_duplicate_name(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        create_branch("my feature", repo)
        _git(repo, "checkout", "main")
        with pytest.raises(GitError, match="already exists"):
            create_branch("my feature", repo)

    def test_special_chars_slugified(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        branch = create_branch("Fix NCD (version 2)", repo)
        assert branch == "pricing/test-user/fix-ncd-version-2"


# ---------------------------------------------------------------------------
# list_branches
# ---------------------------------------------------------------------------


class TestListBranches:
    def test_lists_main(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        result = list_branches(repo)
        assert result.current == "main"
        names = [b.name for b in result.branches]
        assert "main" in names

    def test_own_branches_first(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _git(repo, "checkout", "-b", "pricing/other-user/feat")
        _git(repo, "checkout", "main")
        _git(repo, "checkout", "-b", "pricing/test-user/my-feat")
        _git(repo, "checkout", "main")

        result = list_branches(repo)
        # Filter out main and archived
        non_main = [b for b in result.branches if b.name != "main"]
        assert non_main[0].is_yours is True
        assert non_main[1].is_yours is False

    def test_archived_last(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _git(repo, "checkout", "-b", "archive/old-feat")
        _git(repo, "checkout", "main")
        _git(repo, "checkout", "-b", "pricing/test-user/active")
        _git(repo, "checkout", "main")

        result = list_branches(repo)
        non_main = [b for b in result.branches if b.name != "main"]
        assert non_main[-1].is_archived is True


# ---------------------------------------------------------------------------
# switch_branch
# ---------------------------------------------------------------------------


class TestSwitchBranch:
    def test_switches(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _git(repo, "checkout", "-b", "pricing/test-user/feat")
        _git(repo, "checkout", "main")
        switch_branch("pricing/test-user/feat", repo)
        assert _get_current_branch(repo) == "pricing/test-user/feat"

    def test_auto_commits_before_switch(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _git(repo, "checkout", "-b", "pricing/test-user/feat")
        (repo / "dirty.py").write_text("x = 1\n")
        switch_branch("main", repo)
        # Dirty file should have been committed
        _git(repo, "checkout", "pricing/test-user/feat")
        assert (repo / "dirty.py").exists()

    def test_noop_same_branch(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        switch_branch("main", repo)  # Already on main — should not error


# ---------------------------------------------------------------------------
# save_progress
# ---------------------------------------------------------------------------


class TestSaveProgress:
    def test_saves_and_returns_info(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _git(repo, "checkout", "-b", "pricing/test-user/feat")
        (repo / "main.py").write_text("x = 1\n")

        result = save_progress(repo)
        assert result.commit_sha
        assert result.message == "Updated main"
        assert result.timestamp

    def test_blocked_on_main(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        (repo / "new.py").write_text("x = 1\n")
        with pytest.raises(GitGuardrailError, match="protected"):
            save_progress(repo)

    def test_no_changes(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _git(repo, "checkout", "-b", "pricing/test-user/feat")
        with pytest.raises(GitError, match="No changes"):
            save_progress(repo)


# ---------------------------------------------------------------------------
# get_history
# ---------------------------------------------------------------------------


class TestGetHistory:
    def test_returns_commits(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _git(repo, "checkout", "-b", "pricing/test-user/feat")
        (repo / "a.py").write_text("a = 1\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "Add a.py")

        entries = get_history(cwd=repo)
        assert len(entries) == 1
        assert entries[0].message == "Add a.py"
        assert "a.py" in entries[0].files_changed

    def test_empty_branch(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _git(repo, "checkout", "-b", "pricing/test-user/feat")
        entries = get_history(cwd=repo)
        assert entries == []

    def test_limit(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _git(repo, "checkout", "-b", "pricing/test-user/feat")
        for i in range(5):
            (repo / f"file{i}.py").write_text(f"x = {i}\n")
            _git(repo, "add", ".")
            _git(repo, "commit", "-m", f"Commit {i}")

        entries = get_history(limit=3, cwd=repo)
        assert len(entries) == 3


# ---------------------------------------------------------------------------
# revert_to
# ---------------------------------------------------------------------------


class TestRevertTo:
    def test_reverts_to_commit(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _git(repo, "checkout", "-b", "pricing/test-user/feat")

        (repo / "a.py").write_text("v1\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "v1")
        target_sha = _git(repo, "rev-parse", "HEAD")

        (repo / "a.py").write_text("v2\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "v2")

        result = revert_to(target_sha, repo)
        assert result.reverted_to == target_sha[:7]
        assert result.backup_tag.startswith("backup/")
        assert (repo / "a.py").read_text() == "v1\n"

    def test_blocked_on_main(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        sha = _git(repo, "rev-parse", "HEAD")
        with pytest.raises(GitGuardrailError, match="protected"):
            revert_to(sha, repo)

    def test_invalid_sha(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _git(repo, "checkout", "-b", "pricing/test-user/feat")
        with pytest.raises(GitError, match="not found"):
            revert_to("deadbeef12345678", repo)

    def test_backup_tag_created(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _git(repo, "checkout", "-b", "pricing/test-user/feat")
        (repo / "a.py").write_text("v1\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "v1")
        sha = _git(repo, "rev-parse", "HEAD")

        (repo / "a.py").write_text("v2\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "v2")

        result = revert_to(sha, repo)
        # Verify the backup tag exists and points to the pre-revert HEAD
        tag_sha = _git(repo, "rev-parse", result.backup_tag)
        assert tag_sha  # Tag exists and resolves


# ---------------------------------------------------------------------------
# pull_latest
# ---------------------------------------------------------------------------


class TestPullLatest:
    def test_no_remote(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _git(repo, "checkout", "-b", "pricing/test-user/feat")
        with pytest.raises(GitError, match="No remote"):
            pull_latest(repo)

    def test_pulls_new_commits(self, tmp_path: Path) -> None:
        repo, remote = _init_repo_with_remote(tmp_path)

        # Create a branch
        _git(repo, "checkout", "-b", "pricing/test-user/feat")
        _git(repo, "push", "-u", "origin", "pricing/test-user/feat")

        # Simulate someone else pushing to main (via the remote)
        clone = tmp_path / "clone"
        _git(tmp_path, "clone", str(remote), str(clone))
        _git(clone, "config", "user.name", "Other User")
        _git(clone, "config", "user.email", "other@example.com")
        (clone / "other.py").write_text("y = 1\n")
        _git(clone, "add", ".")
        _git(clone, "commit", "-m", "Other commit")
        _git(clone, "push", "origin", "main")

        result = pull_latest(repo)
        assert result.success is True
        assert result.conflict is False
        assert result.commits_pulled >= 1

    def test_blocked_on_main(self, tmp_path: Path) -> None:
        repo, _ = _init_repo_with_remote(tmp_path)
        with pytest.raises(GitGuardrailError, match="protected"):
            pull_latest(repo)

    def test_no_commits_to_pull(self, tmp_path: Path) -> None:
        repo, _ = _init_repo_with_remote(tmp_path)
        _git(repo, "checkout", "-b", "pricing/test-user/feat")
        result = pull_latest(repo)
        assert result.success is True
        assert result.commits_pulled == 0


# ---------------------------------------------------------------------------
# submit_for_review
# ---------------------------------------------------------------------------


class TestSubmitForReview:
    def test_returns_branch(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _git(repo, "checkout", "-b", "pricing/test-user/feat")
        result = submit_for_review(repo)
        assert result.branch == "pricing/test-user/feat"
        # No remote → no URL
        assert result.compare_url is None

    def test_pushes_to_remote(self, tmp_path: Path) -> None:
        repo, _ = _init_repo_with_remote(tmp_path)
        _git(repo, "checkout", "-b", "pricing/test-user/feat")
        (repo / "change.py").write_text("x = 1\n")

        result = submit_for_review(repo)
        assert result.branch == "pricing/test-user/feat"
        # compare_url is None because the remote is a local bare repo, not github
        assert result.compare_url is None

    def test_blocked_on_main(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        with pytest.raises(GitGuardrailError, match="protected"):
            submit_for_review(repo)


# ---------------------------------------------------------------------------
# archive_branch
# ---------------------------------------------------------------------------


class TestArchiveBranch:
    def test_renames_to_archive(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _git(repo, "checkout", "-b", "pricing/test-user/old-feat")
        _git(repo, "checkout", "main")

        archived = archive_branch("pricing/test-user/old-feat", repo)
        assert archived.startswith("archive/")

        # Old branch should be gone
        branches = _git(repo, "branch")
        assert "pricing/test-user/old-feat" not in branches

    def test_blocked_on_protected(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        with pytest.raises(GitGuardrailError):
            archive_branch("main", repo)

    def test_already_archived(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _git(repo, "checkout", "-b", "archive/old")
        _git(repo, "checkout", "main")
        with pytest.raises(GitError, match="already archived"):
            archive_branch("archive/old", repo)

    def test_switches_away_if_current(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _git(repo, "checkout", "-b", "pricing/test-user/current-feat")
        archived = archive_branch("pricing/test-user/current-feat", repo)
        # Should have switched to main
        assert _get_current_branch(repo) == "main"
        assert archived.startswith("archive/")


# ---------------------------------------------------------------------------
# delete_branch
# ---------------------------------------------------------------------------


class TestDeleteBranch:
    def test_deletes(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _git(repo, "checkout", "-b", "pricing/test-user/to-delete")
        _git(repo, "checkout", "main")
        delete_branch("pricing/test-user/to-delete", repo)
        branches = _git(repo, "branch")
        assert "to-delete" not in branches

    def test_blocked_on_protected(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        with pytest.raises(GitGuardrailError):
            delete_branch("main", repo)

    def test_switches_away_if_current(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _git(repo, "checkout", "-b", "pricing/test-user/feat")
        delete_branch("pricing/test-user/feat", repo)
        assert _get_current_branch(repo) == "main"


# ---------------------------------------------------------------------------
# User slug
# ---------------------------------------------------------------------------


class TestGetUserSlug:
    def test_reads_git_config(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path, user="Ralph Thompson")
        slug = _get_user_slug(repo)
        assert slug == "ralph-thompson"

    def test_handles_special_chars(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path, user="Jean-Pierre O'Brien")
        slug = _get_user_slug(repo)
        assert slug == "jean-pierre-o-brien"


# ---------------------------------------------------------------------------
# Ref name validation (argument injection prevention)
# ---------------------------------------------------------------------------


class TestValidateRefName:
    """_validate_ref_name blocks names that could be interpreted as git flags
    or contain characters dangerous for shell/git."""

    def test_normal_branch_passes(self) -> None:
        _validate_ref_name("pricing/user/my-feature")

    def test_normal_sha_passes(self) -> None:
        _validate_ref_name("abc123def456")

    def test_rejects_empty(self) -> None:
        with pytest.raises(GitError, match="empty"):
            _validate_ref_name("")

    def test_rejects_leading_dash(self) -> None:
        with pytest.raises(GitError, match="must not start with '-'"):
            _validate_ref_name("--upload-pack=evil")

    def test_rejects_single_dash(self) -> None:
        with pytest.raises(GitError, match="must not start with '-'"):
            _validate_ref_name("-b")

    def test_rejects_null_byte(self) -> None:
        with pytest.raises(GitError, match="forbidden characters"):
            _validate_ref_name("branch\x00name")

    def test_rejects_tilde(self) -> None:
        with pytest.raises(GitError, match="forbidden characters"):
            _validate_ref_name("branch~1")

    def test_rejects_caret(self) -> None:
        with pytest.raises(GitError, match="forbidden characters"):
            _validate_ref_name("branch^2")

    def test_rejects_colon(self) -> None:
        with pytest.raises(GitError, match="forbidden characters"):
            _validate_ref_name("branch:name")

    def test_rejects_backslash(self) -> None:
        with pytest.raises(GitError, match="forbidden characters"):
            _validate_ref_name("branch\\name")

    def test_rejects_question_mark(self) -> None:
        with pytest.raises(GitError, match="forbidden characters"):
            _validate_ref_name("branch?name")

    def test_rejects_asterisk(self) -> None:
        with pytest.raises(GitError, match="forbidden characters"):
            _validate_ref_name("branch*name")

    def test_rejects_bracket(self) -> None:
        with pytest.raises(GitError, match="forbidden characters"):
            _validate_ref_name("branch[name")


# ---------------------------------------------------------------------------
# Protected branch detection
# ---------------------------------------------------------------------------


class TestIsProtected:
    def test_main_is_protected(self) -> None:
        assert _is_protected("main") is True

    def test_master_is_protected(self) -> None:
        assert _is_protected("master") is True

    def test_develop_is_protected(self) -> None:
        assert _is_protected("develop") is True

    def test_production_is_protected(self) -> None:
        assert _is_protected("production") is True

    def test_feature_branch_not_protected(self) -> None:
        assert _is_protected("feature/my-work") is False

    def test_branch_containing_main_not_protected(self) -> None:
        assert _is_protected("not-main") is False


# ---------------------------------------------------------------------------
# Commit message generation — additional edge cases
# ---------------------------------------------------------------------------


class TestGenerateCommitMessageEdgeCases:
    def test_single_py_mentions_stem(self) -> None:
        msg = _generate_commit_message(["models/scoring.py"])
        assert "scoring" in msg

    def test_multiple_files_mentions_count(self) -> None:
        files = [f"dir/mod{i}.py" for i in range(4)]
        msg = _generate_commit_message(files)
        assert "4 files" in msg

    def test_config_json_mentions_stem(self) -> None:
        msg = _generate_commit_message(["config/factors.json"])
        assert "config/factors" in msg

    def test_only_sidecar_files_returns_save_progress(self) -> None:
        msg = _generate_commit_message(["a.haute.json", "b.haute.json"])
        assert msg == "Save progress"


# ---------------------------------------------------------------------------
# get_history — additional edge cases
# ---------------------------------------------------------------------------


class TestGetHistoryEdgeCases:
    def test_limit_one_returns_single(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _git(repo, "checkout", "-b", "pricing/test-user/feat")
        for i in range(3):
            (repo / f"f{i}.py").write_text(f"x = {i}\n")
            _git(repo, "add", ".")
            _git(repo, "commit", "-m", f"Commit {i}")

        entries = get_history(limit=1, cwd=repo)
        assert len(entries) == 1

    def test_limit_larger_than_available(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _git(repo, "checkout", "-b", "pricing/test-user/feat")
        (repo / "only.py").write_text("x = 1\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "Only commit")

        entries = get_history(limit=100, cwd=repo)
        assert len(entries) == 1

    def test_empty_branch_returns_empty(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _git(repo, "checkout", "-b", "pricing/test-user/empty")
        entries = get_history(cwd=repo)
        assert entries == []


# ---------------------------------------------------------------------------
# Compare URL — additional edge cases
# ---------------------------------------------------------------------------


class TestBuildCompareUrlEdgeCases:
    def test_github_ssh_converts_to_https(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _git(repo, "remote", "add", "origin", "git@github.com:acme/pricing.git")
        url = _build_compare_url("pricing/user/feat", "main", repo)
        assert url is not None
        assert url.startswith("https://github.com/acme/pricing/")
        assert "git@" not in url

    def test_github_https_works(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _git(repo, "remote", "add", "origin", "https://github.com/acme/pricing.git")
        url = _build_compare_url("my-branch", "main", repo)
        assert url == "https://github.com/acme/pricing/compare/main...my-branch"

    def test_gitlab_produces_merge_requests_path(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _git(repo, "remote", "add", "origin", "https://gitlab.com/org/repo.git")
        url = _build_compare_url("pricing/user/feat", "main", repo)
        assert url is not None
        assert "/-/merge_requests/new" in url

    def test_no_remote_returns_none(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        url = _build_compare_url("feat", "main", repo)
        assert url is None


# ---------------------------------------------------------------------------
# list_branches — additional edge cases
# ---------------------------------------------------------------------------


class TestListBranchesEdgeCases:
    def test_lists_main_branch(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        result = list_branches(repo)
        names = [b.name for b in result.branches]
        assert "main" in names

    def test_own_branches_sorted_first(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _git(repo, "checkout", "-b", "pricing/other-user/other")
        _git(repo, "checkout", "main")
        _git(repo, "checkout", "-b", "pricing/test-user/mine")
        _git(repo, "checkout", "main")

        result = list_branches(repo)
        non_main = [b for b in result.branches if b.name != "main"]
        assert len(non_main) >= 2
        assert non_main[0].is_yours is True

    def test_archived_branches_sorted_last(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _git(repo, "checkout", "-b", "archive/old")
        _git(repo, "checkout", "main")
        _git(repo, "checkout", "-b", "pricing/test-user/active")
        _git(repo, "checkout", "main")

        result = list_branches(repo)
        non_main = [b for b in result.branches if b.name != "main"]
        assert non_main[-1].is_archived is True
        assert non_main[-1].name == "archive/old"


class TestArgumentInjectionPrevention:
    """Verify that public functions reject malicious ref names before
    passing them to git commands."""

    def test_switch_branch_rejects_flag(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        with pytest.raises(GitError, match="must not start with '-'"):
            switch_branch("--upload-pack=evil", repo)

    def test_delete_branch_rejects_flag(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        with pytest.raises(GitError, match="must not start with '-'"):
            delete_branch("--force", repo)

    def test_archive_branch_rejects_flag(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        with pytest.raises(GitError, match="must not start with '-'"):
            archive_branch("--delete", repo)

    def test_revert_to_rejects_flag(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _git(repo, "checkout", "-b", "pricing/test-user/feat")
        with pytest.raises(GitError, match="must not start with '-'"):
            revert_to("--hard", repo)

    def test_switch_branch_rejects_control_chars(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        with pytest.raises(GitError, match="forbidden characters"):
            switch_branch("branch\x00evil", repo)

    def test_delete_branch_rejects_control_chars(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        with pytest.raises(GitError, match="forbidden characters"):
            delete_branch("branch\x00evil", repo)


# ---------------------------------------------------------------------------
# P1: list_branches uses single subprocess for commit counts
# ---------------------------------------------------------------------------


class TestListBranchesOptimised:
    """Verify list_branches returns correct commit counts using the
    optimised %(ahead-behind:...) format from a single for-each-ref call."""

    def test_commit_count_on_branch(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _git(repo, "checkout", "-b", "pricing/test-user/feat")
        for i in range(3):
            (repo / f"file{i}.py").write_text(f"x = {i}\n")
            _git(repo, "add", ".")
            _git(repo, "commit", "-m", f"Commit {i}")

        result = list_branches(repo)
        feat_branch = next(b for b in result.branches if b.name == "pricing/test-user/feat")
        assert feat_branch.commit_count == 3

    def test_main_has_zero_commits_ahead(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        result = list_branches(repo)
        main_branch = next(b for b in result.branches if b.name == "main")
        assert main_branch.commit_count == 0

    def test_multiple_branches_have_correct_counts(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        # Branch A: 2 commits
        _git(repo, "checkout", "-b", "pricing/test-user/branch-a")
        for i in range(2):
            (repo / f"a{i}.py").write_text(f"x = {i}\n")
            _git(repo, "add", ".")
            _git(repo, "commit", "-m", f"A{i}")
        _git(repo, "checkout", "main")

        # Branch B: 4 commits
        _git(repo, "checkout", "-b", "pricing/test-user/branch-b")
        for i in range(4):
            (repo / f"b{i}.py").write_text(f"x = {i}\n")
            _git(repo, "add", ".")
            _git(repo, "commit", "-m", f"B{i}")
        _git(repo, "checkout", "main")

        result = list_branches(repo)
        counts = {b.name: b.commit_count for b in result.branches}
        assert counts["pricing/test-user/branch-a"] == 2
        assert counts["pricing/test-user/branch-b"] == 4


# ---------------------------------------------------------------------------
# P1: get_history uses single subprocess (no per-commit diff-tree)
# ---------------------------------------------------------------------------


class TestGetHistoryOptimised:
    """Verify get_history returns correct files_changed from the
    single-subprocess --name-only approach."""

    def test_files_changed_correct(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _git(repo, "checkout", "-b", "pricing/test-user/feat")

        (repo / "first.py").write_text("a = 1\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "Add first")

        (repo / "second.py").write_text("b = 2\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "Add second")

        entries = get_history(cwd=repo)
        assert len(entries) == 2
        # Most recent first
        assert "second.py" in entries[0].files_changed
        assert "first.py" in entries[1].files_changed

    def test_multiple_files_in_one_commit(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _git(repo, "checkout", "-b", "pricing/test-user/feat")

        (repo / "one.py").write_text("x = 1\n")
        (repo / "two.py").write_text("y = 2\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "Add both")

        entries = get_history(cwd=repo)
        assert len(entries) == 1
        assert "one.py" in entries[0].files_changed
        assert "two.py" in entries[0].files_changed

    def test_history_on_main(self, tmp_path: Path) -> None:
        """On a protected branch, get_history shows the last N commits."""
        repo = _init_repo(tmp_path)
        for i in range(3):
            (repo / f"f{i}.py").write_text(f"x = {i}\n")
            _git(repo, "add", ".")
            _git(repo, "commit", "-m", f"Main commit {i}")

        entries = get_history(limit=2, cwd=repo)
        # Should get 2 most recent (not counting the initial commit if limit=2)
        assert len(entries) == 2
        assert entries[0].message == "Main commit 2"
        assert entries[1].message == "Main commit 1"


# ---------------------------------------------------------------------------
# P2: Fetch throttle in get_status
# ---------------------------------------------------------------------------


class TestFetchThrottle:
    """Verify get_status throttles git fetch calls."""

    def test_second_call_within_cooldown_skips_fetch(self, tmp_path: Path) -> None:
        """Two rapid get_status calls should only trigger one git fetch."""
        import haute._git as git_mod

        repo, _ = _init_repo_with_remote(tmp_path)
        _git(repo, "checkout", "-b", "pricing/test-user/feat")
        _git(repo, "push", "-u", "origin", "pricing/test-user/feat")

        # Reset the throttle so the first call will fetch
        git_mod._last_fetch_time = 0.0

        fetch_count = 0
        original_run_git_ok = git_mod._run_git_ok

        def counting_run_git_ok(*args, **kwargs):
            nonlocal fetch_count
            if args and len(args) >= 2 and args[0] == "fetch":
                fetch_count += 1
            return original_run_git_ok(*args, **kwargs)

        # Monkey-patch to count fetch calls
        git_mod._run_git_ok = counting_run_git_ok
        try:
            get_status(repo)
            first_count = fetch_count
            get_status(repo)
            second_count = fetch_count
            # First call should have fetched; second should not
            assert first_count == 1
            assert second_count == 1  # No additional fetch
        finally:
            git_mod._run_git_ok = original_run_git_ok
            git_mod._last_fetch_time = 0.0

    def test_fetch_happens_after_cooldown_expires(self, tmp_path: Path) -> None:
        """After the cooldown expires, a new fetch should happen."""
        import haute._git as git_mod

        repo, _ = _init_repo_with_remote(tmp_path)
        _git(repo, "checkout", "-b", "pricing/test-user/feat")
        _git(repo, "push", "-u", "origin", "pricing/test-user/feat")

        # Simulate that the last fetch was long ago
        git_mod._last_fetch_time = 0.0

        fetch_count = 0
        original_run_git_ok = git_mod._run_git_ok

        def counting_run_git_ok(*args, **kwargs):
            nonlocal fetch_count
            if args and len(args) >= 2 and args[0] == "fetch":
                fetch_count += 1
            return original_run_git_ok(*args, **kwargs)

        git_mod._run_git_ok = counting_run_git_ok
        try:
            get_status(repo)
            assert fetch_count == 1

            # Force cooldown to have expired by setting last_fetch_time
            # far in the past
            git_mod._last_fetch_time = 0.0

            get_status(repo)
            assert fetch_count == 2  # Should have fetched again
        finally:
            git_mod._run_git_ok = original_run_git_ok
            git_mod._last_fetch_time = 0.0

    def test_no_fetch_on_main(self, tmp_path: Path) -> None:
        """get_status on a protected branch should not fetch."""
        import haute._git as git_mod

        repo, _ = _init_repo_with_remote(tmp_path)
        git_mod._last_fetch_time = 0.0

        fetch_count = 0
        original_run_git_ok = git_mod._run_git_ok

        def counting_run_git_ok(*args, **kwargs):
            nonlocal fetch_count
            if args and len(args) >= 2 and args[0] == "fetch":
                fetch_count += 1
            return original_run_git_ok(*args, **kwargs)

        git_mod._run_git_ok = counting_run_git_ok
        try:
            get_status(repo)  # on main
            assert fetch_count == 0
        finally:
            git_mod._run_git_ok = original_run_git_ok
            git_mod._last_fetch_time = 0.0


# ---------------------------------------------------------------------------
# P3: _get_default_branch caching
# ---------------------------------------------------------------------------


class TestDefaultBranchCache:
    """Verify _get_default_branch caches results via lru_cache."""

    def test_returns_main(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _get_default_branch_cached.cache_clear()
        assert _get_default_branch(repo) == "main"

    def test_cached_second_call_no_subprocess(self, tmp_path: Path) -> None:
        """Second call with the same cwd should be served from cache."""
        import haute._git as git_mod

        repo = _init_repo(tmp_path)
        _get_default_branch_cached.cache_clear()

        subprocess_count = 0
        original_run_git_ok = git_mod._run_git_ok

        def counting_run_git_ok(*args, **kwargs):
            nonlocal subprocess_count
            subprocess_count += 1
            return original_run_git_ok(*args, **kwargs)

        git_mod._run_git_ok = counting_run_git_ok
        try:
            result1 = _get_default_branch(repo)
            calls_after_first = subprocess_count

            result2 = _get_default_branch(repo)
            calls_after_second = subprocess_count

            assert result1 == result2 == "main"
            # Second call should NOT have spawned any subprocess
            assert calls_after_second == calls_after_first
        finally:
            git_mod._run_git_ok = original_run_git_ok
            _get_default_branch_cached.cache_clear()

    def test_different_cwd_not_cached(self, tmp_path: Path) -> None:
        """Different cwd values should get separate cache entries."""
        (tmp_path / "repo1").mkdir()
        (tmp_path / "repo2").mkdir()
        repo1 = _init_repo(tmp_path / "repo1")
        repo2 = _init_repo(tmp_path / "repo2")
        _get_default_branch_cached.cache_clear()

        # Both should return 'main' but should be separate cache entries
        assert _get_default_branch(repo1) == "main"
        assert _get_default_branch(repo2) == "main"

        info = _get_default_branch_cached.cache_info()
        # Two different cwd values → two misses (no hits on the second call)
        assert info.misses == 2

    def test_cache_clear_works(self, tmp_path: Path) -> None:
        """After cache_clear, the next call should re-query git."""
        import haute._git as git_mod

        repo = _init_repo(tmp_path)
        _get_default_branch_cached.cache_clear()

        subprocess_count = 0
        original_run_git_ok = git_mod._run_git_ok

        def counting_run_git_ok(*args, **kwargs):
            nonlocal subprocess_count
            subprocess_count += 1
            return original_run_git_ok(*args, **kwargs)

        git_mod._run_git_ok = counting_run_git_ok
        try:
            _get_default_branch(repo)
            first_calls = subprocess_count

            _get_default_branch_cached.cache_clear()

            _get_default_branch(repo)
            second_calls = subprocess_count - first_calls

            # After clear, should have made subprocess calls again
            assert second_calls > 0
        finally:
            git_mod._run_git_ok = original_run_git_ok
            _get_default_branch_cached.cache_clear()


# ---------------------------------------------------------------------------
# GAP 1: _validate_ref_name does not block '..' (parent traversal)
# ---------------------------------------------------------------------------


class TestValidateRefNameParentTraversal:
    """Production risk: A ref name containing '..' could reference parent
    objects (e.g. 'refs/heads/../../etc/passwd').  Git itself rejects these,
    but _validate_ref_name should catch it *before* the subprocess call to
    provide a clear error and prevent any path traversal attempt.

    These tests document that the current validation is INCOMPLETE.
    """

    def test_double_dot_not_blocked_by_validate(self) -> None:
        """BUG: _validate_ref_name does not reject '..' sequences.
        This test documents the gap — it currently passes validation
        but git would reject it.
        """
        # This SHOULD raise GitError but currently does not.
        # If _validate_ref_name is fixed, change this to pytest.raises.
        _validate_ref_name("refs/heads/../../etc/passwd")  # no exception raised

    def test_double_dot_rejected_by_git(self, tmp_path: Path) -> None:
        """Even though _validate_ref_name allows '..', git itself rejects it.
        This proves the defence-in-depth works but the first layer is missing.
        """
        repo = _init_repo(tmp_path)
        _git(repo, "checkout", "-b", "pricing/test-user/feat")
        # Attempting to switch to a '..' ref should fail at the git level
        with pytest.raises(GitError):
            switch_branch("HEAD/../../../etc/passwd", repo)


# ---------------------------------------------------------------------------
# GAP 2: _validate_ref_name does not block spaces
# ---------------------------------------------------------------------------


class TestValidateRefNameSpaces:
    """Production risk: Branch names with spaces pass _validate_ref_name but
    fail in git, causing confusing subprocess errors instead of clean
    validation messages.
    """

    def test_space_not_blocked_by_validate(self) -> None:
        """BUG: _validate_ref_name does not reject spaces.
        Spaces are invalid in git ref names but slip through the regex.
        """
        # This SHOULD raise GitError but currently does not.
        _validate_ref_name("branch with spaces")  # no exception raised

    def test_space_in_branch_name_fails_in_git(self, tmp_path: Path) -> None:
        """A branch name with spaces will fail at the git level, producing
        a confusing error instead of a clean validation message.
        """
        repo = _init_repo(tmp_path)
        with pytest.raises(GitError):
            switch_branch("branch with spaces", repo)


# ---------------------------------------------------------------------------
# GAP 3: Unicode branch names (emoji, CJK, RTL)
# ---------------------------------------------------------------------------


class TestUnicodeBranchNames:
    """Production risk: Users could paste emoji or non-Latin text into a
    branch description.  _slugify strips these to safe ASCII, but if someone
    calls _validate_ref_name directly with unicode, it passes through.
    """

    def test_emoji_passes_validate_ref_name(self) -> None:
        """BUG: _validate_ref_name does not reject emoji characters.
        Git may accept some unicode refs depending on filesystem, but
        they cause cross-platform portability issues (Windows NTFS, etc).
        """
        # Emoji is not in _BAD_REF_CHARS — this passes validation
        _validate_ref_name("feature/rocket-\U0001f680")  # no exception

    def test_cjk_passes_validate_ref_name(self) -> None:
        """CJK characters pass validation. These cause issues on
        filesystems that don't support them or have different normalisation.
        """
        _validate_ref_name("feature/\u529f\u80fd\u66f4\u65b0")  # no exception

    def test_rtl_passes_validate_ref_name(self) -> None:
        """RTL characters pass validation. These can cause display
        confusion in terminals and UIs (branch name appears reversed).
        """
        _validate_ref_name("feature/\u0645\u064a\u0632\u0629")  # no exception

    def test_slugify_strips_emoji(self) -> None:
        """_slugify correctly strips emoji to produce safe branch names.
        This is the real protection — create_branch uses _slugify.
        """
        slug = _slugify("Rocket launch \U0001f680")
        assert "\U0001f680" not in slug
        assert slug == "rocket-launch"

    def test_create_branch_with_emoji_description_is_safe(self, tmp_path: Path) -> None:
        """create_branch slugifies the description, so emoji input is safe."""
        repo = _init_repo(tmp_path)
        branch = create_branch("Add rocket feature \U0001f680", repo)
        # Emoji is stripped by _slugify
        assert "\U0001f680" not in branch
        assert "add-rocket-feature" in branch


# ---------------------------------------------------------------------------
# GAP 4: Very long branch names (255-char limit)
# ---------------------------------------------------------------------------


class TestLongBranchNames:
    """Production risk: Git refs are stored as filesystem paths.  Most
    filesystems limit path components to 255 bytes.  A very long branch
    name can fail silently or corrupt the ref storage.
    """

    def test_long_name_passes_validate(self) -> None:
        """BUG: _validate_ref_name has no length check.  A 300-char ref
        name passes validation but will fail on most filesystems.
        """
        long_name = "a" * 300
        # This SHOULD raise GitError but currently does not.
        _validate_ref_name(long_name)  # no exception

    def test_very_long_branch_name_may_fail_in_git(self, tmp_path: Path) -> None:
        """Git (or the filesystem) may reject absurdly long branch names on
        some platforms (Windows 255-byte path limit) but not others (Linux
        ext4 supports longer paths).  Documents that validation has no
        length check.
        """
        repo = _init_repo(tmp_path)
        long_desc = "a" * 250  # _slugify preserves this; prefix adds more
        # On Linux this may succeed; on Windows it typically fails.
        try:
            create_branch(long_desc, repo)
        except GitError:
            pass  # Expected on some platforms


# ---------------------------------------------------------------------------
# GAP 5: Merge conflicts during pull_latest
# ---------------------------------------------------------------------------


class TestPullLatestMergeConflict:
    """Production risk: When a user's branch and main both edit the same
    file, pull_latest should detect the conflict, abort the merge, and
    return a helpful message — not leave the repo in a broken state.
    """

    def test_conflicting_changes_detected(self, tmp_path: Path) -> None:
        """pull_latest returns conflict=True and aborts the merge when
        the same file was edited on both branches.
        """
        repo, remote = _init_repo_with_remote(tmp_path)

        # User creates a branch and edits a file
        _git(repo, "checkout", "-b", "pricing/test-user/feat")
        (repo / "shared.py").write_text("user_version = True\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "User edit")
        _git(repo, "push", "-u", "origin", "pricing/test-user/feat")

        # Someone else edits the same file on main (via a clone)
        clone = tmp_path / "clone"
        _git(tmp_path, "clone", str(remote), str(clone))
        _git(clone, "config", "user.name", "Other User")
        _git(clone, "config", "user.email", "other@example.com")
        (clone / "shared.py").write_text("other_version = True\n")
        _git(clone, "add", ".")
        _git(clone, "commit", "-m", "Conflicting edit on main")
        _git(clone, "push", "origin", "main")

        # Now pull_latest should detect the conflict
        result = pull_latest(repo)
        assert result.success is False
        assert result.conflict is True
        assert result.conflict_message is not None
        assert (
            "overlap" in result.conflict_message.lower()
            or "conflict" in result.conflict_message.lower()
        )
        assert result.commits_pulled == 0

    def test_repo_clean_after_conflict_abort(self, tmp_path: Path) -> None:
        """After a conflict, the merge is aborted and the working tree
        is clean — not left in a half-merged state.
        """
        repo, remote = _init_repo_with_remote(tmp_path)

        _git(repo, "checkout", "-b", "pricing/test-user/feat")
        (repo / "shared.py").write_text("user_version = True\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "User edit")
        _git(repo, "push", "-u", "origin", "pricing/test-user/feat")

        clone = tmp_path / "clone"
        _git(tmp_path, "clone", str(remote), str(clone))
        _git(clone, "config", "user.name", "Other User")
        _git(clone, "config", "user.email", "other@example.com")
        (clone / "shared.py").write_text("other_version = True\n")
        _git(clone, "add", ".")
        _git(clone, "commit", "-m", "Conflicting edit")
        _git(clone, "push", "origin", "main")

        pull_latest(repo)

        # Repo should be clean — no merge markers, no staged conflicts
        status = _git(repo, "status", "--porcelain")
        assert status == "", f"Repo not clean after conflict abort: {status}"
        # Should still be on the user's branch
        assert _get_current_branch(repo) == "pricing/test-user/feat"


# ---------------------------------------------------------------------------
# GAP 6: Detached HEAD state in get_status
# ---------------------------------------------------------------------------


class TestDetachedHead:
    """Production risk: If a user checks out a specific commit (detached HEAD),
    get_status should handle this gracefully — not crash or return misleading
    branch info.
    """

    def test_detached_head_reports_HEAD(self, tmp_path: Path) -> None:
        """get_status reports branch='HEAD' when in detached HEAD state."""
        repo = _init_repo(tmp_path)
        sha = _git(repo, "rev-parse", "HEAD")
        _git(repo, "checkout", sha)

        s = get_status(repo)
        assert s.branch == "HEAD"

    def test_detached_head_is_read_only(self, tmp_path: Path) -> None:
        """Detached HEAD should NOT be read-only (the code has branch != 'HEAD'
        check), allowing emergency saves. Verify the actual behaviour.
        """
        repo = _init_repo(tmp_path)
        sha = _git(repo, "rev-parse", "HEAD")
        _git(repo, "checkout", sha)

        s = get_status(repo)
        # Detached HEAD is not in PROTECTED_BRANCHES, and the code has
        # an explicit `branch != "HEAD"` check that makes it writable
        assert s.is_main is False
        # is_read_only depends on _is_own_branch which returns False for "HEAD",
        # BUT the code explicitly excludes "HEAD" from the read-only check
        assert s.is_read_only is False


# ---------------------------------------------------------------------------
# GAP 7: '#' and '&' in branch names inject into _build_compare_url
# ---------------------------------------------------------------------------


class TestCompareUrlInjection:
    """Production risk: Branch names containing '#' or '&' pass
    _validate_ref_name but inject fragments/parameters into the compare URL.
    For example, '#' truncates the URL path and '&' adds query parameters
    to GitLab/Azure URLs.
    """

    def test_hash_not_blocked_by_validate(self) -> None:
        """BUG: '#' passes _validate_ref_name but can inject a URL fragment."""
        _validate_ref_name("feature/test#malicious")  # no exception

    def test_ampersand_not_blocked_by_validate(self) -> None:
        """BUG: '&' passes _validate_ref_name but can inject URL query params."""
        _validate_ref_name("feature/test&evil=1")  # no exception

    def test_hash_in_github_compare_url(self, tmp_path: Path) -> None:
        """A '#' in the branch name creates a URL fragment, breaking the
        compare link — the part after '#' becomes a page anchor, not
        part of the branch ref.
        """
        repo = _init_repo(tmp_path)
        _git(repo, "remote", "add", "origin", "git@github.com:org/repo.git")
        url = _build_compare_url("feature/test#inject", "main", repo)
        assert url is not None
        # The '#' is embedded raw in the URL — it will be interpreted
        # as a fragment separator by browsers
        assert "#inject" in url  # proves the injection

    def test_ampersand_in_gitlab_url(self, tmp_path: Path) -> None:
        """An '&' in the branch name injects extra query parameters
        into the GitLab merge request URL.
        """
        repo = _init_repo(tmp_path)
        _git(repo, "remote", "add", "origin", "https://gitlab.com/org/repo.git")
        url = _build_compare_url("feature/test&evil=1", "main", repo)
        assert url is not None
        # The '&' creates an additional query parameter
        assert "&evil=1" in url  # proves the injection


# ---------------------------------------------------------------------------
# GAP 8: Archive name collision (two archives same day)
# ---------------------------------------------------------------------------


class TestArchiveNameCollision:
    """Production risk: If two branches with the same slug are archived on
    the same day, the second archive could overwrite the first.  The code
    appends a date suffix on collision, but does not handle the case where
    the date-suffixed name *also* already exists.
    """

    def test_two_archives_same_slug_same_day(self, tmp_path: Path) -> None:
        """Archiving two branches that produce the same archive name should
        not lose either branch.
        """
        repo = _init_repo(tmp_path)

        # Create two branches with the same final slug component
        _git(repo, "checkout", "-b", "pricing/user-a/my-feat")
        (repo / "a.py").write_text("a = 1\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "branch a")
        _git(repo, "checkout", "main")

        _git(repo, "checkout", "-b", "pricing/user-b/my-feat")
        (repo / "b.py").write_text("b = 1\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "branch b")
        _git(repo, "checkout", "main")

        # Archive both — both would want "archive/my-feat"
        name1 = archive_branch("pricing/user-a/my-feat", repo)
        name2 = archive_branch("pricing/user-b/my-feat", repo)

        # They must have different names
        assert name1 != name2
        assert name1.startswith("archive/")
        assert name2.startswith("archive/")

        # Both branches should still be resolvable
        sha1 = _git(repo, "rev-parse", name1)
        sha2 = _git(repo, "rev-parse", name2)
        assert sha1
        assert sha2
        assert sha1 != sha2  # They point to different commits


# ---------------------------------------------------------------------------
# GAP 9: 'git add -A' stages sensitive files (.env)
# ---------------------------------------------------------------------------


class TestSaveProgressStagesSensitiveFiles:
    """Production risk: save_progress uses 'git add -A', which stages
    EVERY file in the working tree — including .env files, credentials,
    private keys, etc.  Without a .gitignore, these get committed and
    potentially pushed to a remote.
    """

    def test_env_file_gets_staged_and_committed(self, tmp_path: Path) -> None:
        """SECURITY: .env files are committed by save_progress when no
        .gitignore is present.  This test proves the risk exists.
        """
        repo = _init_repo(tmp_path)
        _git(repo, "checkout", "-b", "pricing/test-user/feat")

        # Create a sensitive .env file
        (repo / ".env").write_text("SECRET_KEY=super-secret-value\nDB_PASSWORD=hunter2\n")

        result = save_progress(repo)
        assert result.commit_sha

        # Verify .env was committed — this is the security risk
        committed_files = _git(repo, "show", "--name-only", "--format=", "HEAD")
        assert ".env" in committed_files, (
            ".env was NOT committed — if this fails, git add -A behaviour changed"
        )

    def test_private_key_gets_staged(self, tmp_path: Path) -> None:
        """SECURITY: Private keys are also staged by 'git add -A'."""
        repo = _init_repo(tmp_path)
        _git(repo, "checkout", "-b", "pricing/test-user/feat")

        (repo / "id_rsa").write_text("-----BEGIN RSA PRIVATE KEY-----\nfake\n")

        result = save_progress(repo)
        committed_files = _git(repo, "show", "--name-only", "--format=", "HEAD")
        assert "id_rsa" in committed_files

    def test_gitignore_prevents_env_staging(self, tmp_path: Path) -> None:
        """With a proper .gitignore, .env is excluded from 'git add -A'.
        This shows the mitigation that SHOULD be in place.
        """
        repo = _init_repo(tmp_path)
        _git(repo, "checkout", "-b", "pricing/test-user/feat")

        # Add .gitignore first
        (repo / ".gitignore").write_text(".env\nid_rsa\n*.pem\n")
        (repo / ".env").write_text("SECRET_KEY=super-secret-value\n")
        (repo / "real_change.py").write_text("x = 1\n")

        result = save_progress(repo)
        committed_files = _git(repo, "show", "--name-only", "--format=", "HEAD")
        assert ".env" not in committed_files
        assert ".gitignore" in committed_files


# ---------------------------------------------------------------------------
# EDGE CASE 1: Protected branch check is case-sensitive
# ---------------------------------------------------------------------------


class TestProtectedBranchCaseSensitivity:
    """_is_protected uses a frozenset lookup, which is case-sensitive.
    Only exact lowercase matches ("main", "master", etc.) are protected.
    This is intentional: git branch names are case-sensitive, and "MAIN"
    is a different branch from "main".
    """

    def test_uppercase_MAIN_is_not_protected(self) -> None:
        assert _is_protected("MAIN") is False

    def test_titlecase_Main_is_not_protected(self) -> None:
        assert _is_protected("Main") is False

    def test_mixed_case_mAiN_is_not_protected(self) -> None:
        assert _is_protected("mAiN") is False

    def test_uppercase_MASTER_is_not_protected(self) -> None:
        assert _is_protected("MASTER") is False

    def test_uppercase_DEVELOP_is_not_protected(self) -> None:
        assert _is_protected("DEVELOP") is False

    def test_uppercase_PRODUCTION_is_not_protected(self) -> None:
        assert _is_protected("PRODUCTION") is False

    def test_lowercase_variants_still_protected(self) -> None:
        for name in ("main", "master", "develop", "production"):
            assert _is_protected(name) is True


# ---------------------------------------------------------------------------
# EDGE CASE 2: Sensitive file staging — credentials.json
# ---------------------------------------------------------------------------


class TestSensitiveFileStagingCredentials:
    """save_progress uses 'git add -A', which stages all untracked files.
    This documents that credentials.json is staged when no .gitignore
    is present, and excluded when .gitignore covers it.
    """

    def test_credentials_json_gets_staged_without_gitignore(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _git(repo, "checkout", "-b", "pricing/test-user/feat")

        (repo / ".env").write_text("SECRET=abc\n")
        (repo / "credentials.json").write_text('{"api_key": "sk-12345"}\n')

        result = save_progress(repo)
        assert result.commit_sha
        committed_files = _git(repo, "show", "--name-only", "--format=", "HEAD")
        assert ".env" in committed_files
        assert "credentials.json" in committed_files

    def test_gitignore_excludes_credentials_json(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _git(repo, "checkout", "-b", "pricing/test-user/feat")

        (repo / ".gitignore").write_text(".env\ncredentials.json\n")
        (repo / ".env").write_text("SECRET=abc\n")
        (repo / "credentials.json").write_text('{"api_key": "sk-12345"}\n')
        (repo / "app.py").write_text("print('hello')\n")

        result = save_progress(repo)
        committed_files = _git(repo, "show", "--name-only", "--format=", "HEAD")
        assert ".env" not in committed_files
        assert "credentials.json" not in committed_files
        assert "app.py" in committed_files


# ---------------------------------------------------------------------------
# EDGE CASE 3: Pull with merge conflict — divergent commits
# ---------------------------------------------------------------------------


class TestPullLatestMergeConflictDivergent:
    """Create truly divergent commits on local and remote (same file,
    different content on the same lines) and verify conflict handling.
    """

    def test_divergent_same_file_conflict_detected(self, tmp_path: Path) -> None:
        repo, remote = _init_repo_with_remote(tmp_path)

        _git(repo, "checkout", "-b", "pricing/test-user/feat")
        (repo / "config.py").write_text("RATE = 0.05\nTAX = 0.2\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "Local rate config")
        _git(repo, "push", "-u", "origin", "pricing/test-user/feat")

        clone = tmp_path / "clone"
        _git(tmp_path, "clone", str(remote), str(clone))
        _git(clone, "config", "user.name", "Other User")
        _git(clone, "config", "user.email", "other@example.com")
        (clone / "config.py").write_text("RATE = 0.10\nTAX = 0.25\n")
        _git(clone, "add", ".")
        _git(clone, "commit", "-m", "Remote rate config")
        _git(clone, "push", "origin", "main")

        result = pull_latest(repo)
        assert result.success is False
        assert result.conflict is True
        assert result.conflict_message is not None
        assert result.commits_pulled == 0

    def test_repo_clean_after_divergent_conflict(self, tmp_path: Path) -> None:
        repo, remote = _init_repo_with_remote(tmp_path)

        _git(repo, "checkout", "-b", "pricing/test-user/feat")
        (repo / "data.py").write_text("VALUE = 1\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "Local data")
        _git(repo, "push", "-u", "origin", "pricing/test-user/feat")

        clone = tmp_path / "clone"
        _git(tmp_path, "clone", str(remote), str(clone))
        _git(clone, "config", "user.name", "Other User")
        _git(clone, "config", "user.email", "other@example.com")
        (clone / "data.py").write_text("VALUE = 999\n")
        _git(clone, "add", ".")
        _git(clone, "commit", "-m", "Remote data")
        _git(clone, "push", "origin", "main")

        pull_latest(repo)

        status = _git(repo, "status", "--porcelain")
        assert status == "", f"Repo not clean after conflict abort: {status}"
        assert _get_current_branch(repo) == "pricing/test-user/feat"
        assert (repo / "data.py").read_text() == "VALUE = 1\n"


# ---------------------------------------------------------------------------
# EDGE CASE 4: Validate ref name with double-dash tricks
# ---------------------------------------------------------------------------


class TestValidateRefNameDoubleDash:
    """_validate_ref_name rejects names starting with '-' to prevent
    argument injection.  Double-dashes mid-name are allowed because they
    are valid in git ref names.
    """

    def test_double_dash_flag_injection_rejected(self) -> None:
        with pytest.raises(GitError, match="must not start with '-'"):
            _validate_ref_name("--version")

    def test_single_dash_rejected(self) -> None:
        with pytest.raises(GitError, match="must not start with '-'"):
            _validate_ref_name("-")

    def test_upload_pack_injection_rejected(self) -> None:
        with pytest.raises(GitError, match="must not start with '-'"):
            _validate_ref_name("--upload-pack=evil")

    def test_double_dash_mid_name_allowed(self) -> None:
        _validate_ref_name("my--upload-pack=evil")

    def test_double_dash_in_normal_branch_allowed(self) -> None:
        _validate_ref_name("feature/my--branch")

    def test_leading_dash_with_equals_rejected(self) -> None:
        with pytest.raises(GitError, match="must not start with '-'"):
            _validate_ref_name("-c=evil.command")


# ---------------------------------------------------------------------------
# EDGE CASE 5: Default branch cache invalidation
# ---------------------------------------------------------------------------


class TestDefaultBranchCacheInvalidation:
    """Verify that _get_default_branch_cached uses lru_cache correctly:
    second call with same cwd hits cache (no subprocess), and cache_clear()
    forces a new subprocess call.
    """

    def test_second_call_uses_cache_third_after_clear_does_not(self, tmp_path: Path) -> None:
        import haute._git as git_mod

        repo = _init_repo(tmp_path)
        _get_default_branch_cached.cache_clear()

        subprocess_count = 0
        original_run_git_ok = git_mod._run_git_ok

        def counting_run_git_ok(*args, **kwargs):
            nonlocal subprocess_count
            subprocess_count += 1
            return original_run_git_ok(*args, **kwargs)

        git_mod._run_git_ok = counting_run_git_ok
        try:
            result1 = _get_default_branch(repo)
            calls_after_first = subprocess_count
            assert calls_after_first > 0

            result2 = _get_default_branch(repo)
            calls_after_second = subprocess_count
            assert result1 == result2
            assert calls_after_second == calls_after_first

            _get_default_branch_cached.cache_clear()

            result3 = _get_default_branch(repo)
            calls_after_third = subprocess_count
            assert result3 == result1
            assert calls_after_third > calls_after_second
        finally:
            git_mod._run_git_ok = original_run_git_ok
            _get_default_branch_cached.cache_clear()

    def test_cache_info_shows_hit_on_second_call(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _get_default_branch_cached.cache_clear()

        _get_default_branch(repo)
        info_after_first = _get_default_branch_cached.cache_info()
        assert info_after_first.misses >= 1
        hits_before = info_after_first.hits

        _get_default_branch(repo)
        info_after_second = _get_default_branch_cached.cache_info()
        assert info_after_second.hits > hits_before

        _get_default_branch_cached.cache_clear()


# ---------------------------------------------------------------------------
# EDGE CASE 6: Revert backup tag contains timestamp
# ---------------------------------------------------------------------------


class TestRevertBackupTagTimestamp:
    """revert_to creates a backup tag with the pattern
    'backup/<branch-slug>/<ISO-timestamp>'. Verify the timestamp
    component is present and follows the expected format.
    """

    def test_backup_tag_contains_timestamp_pattern(self, tmp_path: Path) -> None:
        import re

        repo = _init_repo(tmp_path)
        _git(repo, "checkout", "-b", "pricing/test-user/feat")

        (repo / "a.py").write_text("v1\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "v1")
        target_sha = _git(repo, "rev-parse", "HEAD")

        (repo / "a.py").write_text("v2\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "v2")

        result = revert_to(target_sha, repo)

        assert result.backup_tag.startswith("backup/")
        timestamp_pattern = re.compile(
            r"\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}$"
        )
        tag_parts = result.backup_tag.split("/")
        timestamp_part = tag_parts[-1]
        assert timestamp_pattern.match(timestamp_part), (
            f"Backup tag timestamp '{timestamp_part}' does not match "
            "expected pattern YYYY-MM-DDTHH-MM-SS"
        )

    def test_backup_tag_contains_branch_slug(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _git(repo, "checkout", "-b", "pricing/test-user/my-feature")

        (repo / "a.py").write_text("v1\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "v1")
        target_sha = _git(repo, "rev-parse", "HEAD")

        (repo / "a.py").write_text("v2\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "v2")

        result = revert_to(target_sha, repo)
        assert "pricing-test-user-my-feature" in result.backup_tag

    def test_backup_tag_resolves_to_pre_revert_head(self, tmp_path: Path) -> None:
        repo = _init_repo(tmp_path)
        _git(repo, "checkout", "-b", "pricing/test-user/feat")

        (repo / "a.py").write_text("v1\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "v1")
        target_sha = _git(repo, "rev-parse", "HEAD")

        (repo / "a.py").write_text("v2\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "v2")
        pre_revert_sha = _git(repo, "rev-parse", "HEAD")

        result = revert_to(target_sha, repo)
        tag_sha = _git(repo, "rev-parse", result.backup_tag)
        assert tag_sha == pre_revert_sha
