"""Git operations layer with guardrails for non-technical users.

All git CLI interactions go through this module.  Routes never call
``subprocess`` directly — this gives us a single place for:

- **Guardrails** — refuse operations on protected branches
- **Error handling** — translate git errors to user-friendly messages
- **Backup safety nets** — tag before destructive operations (revert)
"""

from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from haute._logging import get_logger
from haute.errors import HauteError
from haute.schemas import (
    GitBranchItem,
    GitBranchListResponse,
    GitHistoryEntry,
    GitPullResponse,
    GitRevertResponse,
    GitSaveResponse,
    GitSetIdentityResponse,
    GitSetWorkingBranchResponse,
    GitStatusResponse,
    GitSubmitResponse,
    GitWorkingBranchResponse,
)

logger = get_logger(component="git")

PROTECTED_BRANCHES = frozenset({"main", "master", "develop", "production"})

# Branch names created by haute follow: pricing/<user>/<slug>
_BRANCH_PREFIX = "pricing"
_ARCHIVE_PREFIX = "archive"

# Minimum seconds between `git fetch` calls in get_status.
_FETCH_COOLDOWN_SECONDS: float = 30.0
_last_fetch_time: float = 0.0
_fetch_time_lock = threading.Lock()
# Serialises the actual ``git fetch`` subprocess — two concurrent callers
# that both pass the cooldown window must not launch parallel fetches
# because git races on the local .git/objects index.
_fetch_exec_lock = threading.Lock()

# Characters that have no business in a branch name or SHA — used by
# ``_validate_ref_name`` to block argument injection.
_BAD_REF_CHARS = re.compile(r"[\x00-\x1f\x7f~^:?*\[\]\\]")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class GitError(HauteError):
    """Raw ``git`` operation error — unsafe to surface to HTTP.

    By default any ``GitError`` that bubbles to the HTTP layer may
    embed raw subprocess stderr (absolute paths, remote URLs, SSL
    errors, credentials) and is therefore sanitized before reaching
    the client.  The full detail remains in the structured log.

    Hand-written, user-facing messages (missing repo, duplicate
    branch, "no changes to save") use :class:`GitDomainError` so the
    HTTP handler can pass them through verbatim.  Guardrail blocks
    (protected branches) use :class:`GitGuardrailError`.
    """


class GitDomainError(GitError):
    """Hand-written user-facing git error; HTTP layer keeps it verbatim.

    Use this for any message we author ourselves (e.g. "Not a git
    repository", "Branch already exists", "No changes to save") that
    should surface to the user unchanged.  Never pass raw subprocess
    stderr into this class — it bypasses the sanitization performed
    for plain :class:`GitError`.
    """


class GitGuardrailError(GitDomainError):
    """Blocked by a safety guardrail (e.g. writing to main).

    Inherits from :class:`GitDomainError` — the message is
    hand-written and surfaces verbatim — and is distinguished from
    plain domain errors so the HTTP layer can return 403 rather than
    400.
    """


# ---------------------------------------------------------------------------
# Public result types
# ---------------------------------------------------------------------------
#
# ``_git`` hands back the same Pydantic models that the HTTP layer exposes so
# the routes can pass results through verbatim — no dataclass-to-dict-to-model
# round-trip.


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _run_git(*args: str, check: bool = True, cwd: Path | None = None) -> str:
    """Run a git command and return stdout.  Raises ``GitError`` on failure.

    The raw subprocess stderr is wrapped in a plain :class:`GitError`
    (the sanitize-by-default class) so the HTTP handler collapses the
    detail to ``_INTERNAL_ERROR_DETAIL`` — raw stderr commonly contains
    absolute paths, remote URLs, SSL errors, and credentials.
    Full detail is retained in the ``git_command_failed`` structured log.
    """
    cmd = ["git"] + list(args)
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=cwd or Path.cwd(),
    )
    if check and result.returncode != 0:
        stderr = result.stderr.strip()
        logger.warning("git_command_failed", cmd=cmd, stderr=stderr)
        raise GitError(stderr or f"git {args[0]} failed")
    return result.stdout.strip()


def _run_git_ok(*args: str, cwd: Path | None = None) -> tuple[bool, str]:
    """Run a git command and return (success, stdout).  Never raises."""
    cmd = ["git"] + list(args)
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=cwd or Path.cwd(),
    )
    return result.returncode == 0, result.stdout.strip()


def _is_git_repo(cwd: Path | None = None) -> bool:
    ok, _ = _run_git_ok("rev-parse", "--is-inside-work-tree", cwd=cwd)
    return ok


def _get_current_branch(cwd: Path | None = None) -> str:
    """Return the current branch name, or 'HEAD' if detached."""
    ok, branch = _run_git_ok("symbolic-ref", "--short", "HEAD", cwd=cwd)
    return branch if ok else "HEAD"


@lru_cache(maxsize=32)
def _get_default_branch_cached(cwd_str: str) -> str:
    """Cached inner implementation.  Keyed on stringified *cwd* because
    ``Path`` is unhashable and ``lru_cache`` keys must be hashable.
    """
    cwd = Path(cwd_str) if cwd_str else None
    ok, ref = _run_git_ok(
        "symbolic-ref",
        "refs/remotes/origin/HEAD",
        "--short",
        cwd=cwd,
    )
    if ok and "/" in ref:
        return ref.split("/", 1)[1]
    # Fallback: check if 'main' or 'master' exist locally
    ok_main, _ = _run_git_ok("rev-parse", "--verify", "main", cwd=cwd)
    if ok_main:
        return "main"
    ok_master, _ = _run_git_ok("rev-parse", "--verify", "master", cwd=cwd)
    if ok_master:
        return "master"
    return "main"


def _get_default_branch(cwd: Path | None = None) -> str:
    """Detect the default branch (main or master).

    Result is cached per *cwd* — the default branch almost never changes
    during a session so this avoids up to 3 subprocess calls on every
    operation.
    """
    return _get_default_branch_cached(str(cwd) if cwd else "")


def _get_user_slug(cwd: Path | None = None) -> str:
    """Get a slugified version of the git user name."""
    ok, name = _run_git_ok("config", "user.name", cwd=cwd)
    if ok and name:
        return _slugify(name)
    # Fallback to OS username
    return _slugify(os.getlogin())


def _slugify(text: str) -> str:
    """Convert text to a git-safe branch name component."""
    slug = text.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug or "user"


def _validate_ref_name(name: str) -> None:
    """Reject ref names that could be interpreted as git flags or contain
    suspicious characters.  This prevents argument injection when user-supplied
    branch names or SHAs are passed to git CLI commands.
    """
    if not name:
        raise GitDomainError("Ref name cannot be empty.")
    if name.startswith("-"):
        raise GitDomainError(f"Invalid ref name: {name!r} (must not start with '-').")
    if _BAD_REF_CHARS.search(name):
        raise GitDomainError(f"Invalid ref name: {name!r} (contains forbidden characters).")


def _is_protected(branch: str) -> bool:
    return branch in PROTECTED_BRANCHES


def _assert_not_protected(branch: str) -> None:
    if _is_protected(branch):
        raise GitGuardrailError(
            f"Cannot modify protected branch '{branch}'. Create a new branch to make changes."
        )


def _assert_git_repo(cwd: Path | None = None) -> None:
    if not _is_git_repo(cwd):
        raise GitDomainError("Not a git repository. Run 'git init' first.")


def _is_own_branch(branch: str, user_slug: str) -> bool:
    """Check if a branch belongs to the given user."""
    return branch.startswith(f"{_BRANCH_PREFIX}/{user_slug}/")


def _has_remote(cwd: Path | None = None) -> bool:
    ok, remotes = _run_git_ok("remote", cwd=cwd)
    return ok and bool(remotes.strip())


def _get_remote_url(cwd: Path | None = None) -> str | None:
    """Get the origin remote URL."""
    ok, url = _run_git_ok("remote", "get-url", "origin", cwd=cwd)
    return url if ok else None


def _build_compare_url(branch: str, default_branch: str, cwd: Path | None = None) -> str | None:
    """Build a PR/MR comparison URL from the remote origin URL."""
    raw_url = _get_remote_url(cwd)
    if not raw_url:
        return None

    # Normalise SSH → HTTPS
    # git@github.com:org/repo.git → https://github.com/org/repo
    # https://github.com/org/repo.git → https://github.com/org/repo
    url = raw_url
    if url.startswith("git@"):
        url = url.replace(":", "/", 1).replace("git@", "https://", 1)
    url = re.sub(r"\.git$", "", url)

    encoded_branch = branch.replace("/", "%2F") if "gitlab" in url else branch

    if "github" in url:
        return f"{url}/compare/{default_branch}...{branch}"
    elif "gitlab" in url:
        return f"{url}/-/merge_requests/new?merge_request[source_branch]={encoded_branch}"
    elif "dev.azure.com" in url or "visualstudio.com" in url:
        return f"{url}/pullrequestcreate?sourceRef={branch}&targetRef={default_branch}"
    elif "bitbucket" in url:
        return f"{url}/pull-requests/new?source={branch}&dest={default_branch}"

    # Unknown host — return a generic URL
    return None


def _generate_commit_message(changed_files: list[str]) -> str:
    """Generate a human-readable commit message from changed file paths."""
    if not changed_files:
        return "Save progress"

    # Extract meaningful names
    names: list[str] = []
    for f in changed_files:
        p = Path(f)
        if p.suffix == ".py" and p.stem != "__init__":
            names.append(p.stem)
        elif p.suffix == ".json" and "config" in str(p):
            names.append(f"config/{p.stem}")
        elif p.name.endswith(".haute.json"):
            continue  # Skip sidecar files from the message
        else:
            names.append(p.name)

    if not names:
        return "Save progress"
    if len(names) == 1:
        return f"Updated {names[0]}"
    if len(names) <= 3:
        return f"Updated {', '.join(names)}"
    return f"Updated {len(names)} files"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def ensure_repo(cwd: Path | None = None) -> None:
    """Assert we're in a git repo."""
    _assert_git_repo(cwd)


def get_status(cwd: Path | None = None) -> GitStatusResponse:
    """Get the current git status for the panel."""
    _assert_git_repo(cwd)

    branch = _get_current_branch(cwd)
    default = _get_default_branch(cwd)
    user_slug = _get_user_slug(cwd)
    is_main = _is_protected(branch)

    # Read-only if on protected branch OR on someone else's branch
    is_read_only = is_main or (not _is_own_branch(branch, user_slug) and branch != "HEAD")

    # Changed files (both staged and unstaged)
    ok, diff_output = _run_git_ok("status", "--porcelain", cwd=cwd)
    changed_files: list[str] = []
    if ok and diff_output:
        for line in diff_output.splitlines():
            # Porcelain format: "XY filename" — skip the 2-char status + space
            if len(line) > 3:
                changed_files.append(line[3:].strip().strip('"'))

    # How far ahead is the default branch?
    main_ahead_by = 0
    main_last_updated: str | None = None
    if _has_remote(cwd) and not is_main:
        # Fetch silently — but throttle to avoid hammering the remote
        # when the frontend polls frequently.
        global _last_fetch_time  # noqa: PLW0603
        now = time.monotonic()
        should_fetch = False
        with _fetch_time_lock:
            if now - _last_fetch_time >= _FETCH_COOLDOWN_SECONDS:
                _last_fetch_time = now
                should_fetch = True
        if should_fetch:
            # Serialise the actual subprocess — git fetch races on the
            # local object store if two processes run concurrently.
            with _fetch_exec_lock:
                _run_git_ok("fetch", "origin", default, "--quiet", cwd=cwd)

        ok_count, count_str = _run_git_ok(
            "rev-list",
            "--count",
            f"HEAD..origin/{default}",
            cwd=cwd,
        )
        if ok_count and count_str.isdigit():
            main_ahead_by = int(count_str)

        if main_ahead_by > 0:
            ok_time, timestamp = _run_git_ok(
                "log",
                "-1",
                "--format=%aI",
                f"origin/{default}",
                cwd=cwd,
            )
            if ok_time:
                main_last_updated = timestamp

    return GitStatusResponse(
        branch=branch,
        is_main=is_main,
        is_read_only=is_read_only,
        changed_files=changed_files,
        main_ahead=main_ahead_by > 0,
        main_ahead_by=main_ahead_by,
        main_last_updated=main_last_updated,
    )


def create_branch(description: str, cwd: Path | None = None) -> str:
    """Create a new branch from the latest default branch."""
    _assert_git_repo(cwd)

    if not description.strip():
        raise GitDomainError("Branch description cannot be empty.")

    slug = _slugify(description)
    if not slug:
        raise GitDomainError("Branch description cannot be empty.")

    user_slug = _get_user_slug(cwd)
    branch_name = f"{_BRANCH_PREFIX}/{user_slug}/{slug}"
    _validate_ref_name(branch_name)

    # Check it doesn't already exist
    ok, _ = _run_git_ok("rev-parse", "--verify", branch_name, cwd=cwd)
    if ok:
        raise GitDomainError(
            f"Branch '{branch_name}' already exists. "
            "Choose a different description or switch to the existing branch."
        )

    # Create from current HEAD and switch to it
    _run_git("checkout", "-b", branch_name, cwd=cwd)
    logger.info("branch_created", branch=branch_name)
    return branch_name


def list_branches(cwd: Path | None = None) -> GitBranchListResponse:
    """List all branches, with the user's branches first.

    Uses ``%(ahead-behind:<default>)`` (git 2.35+) to get commit counts
    in a single subprocess call instead of one per branch.
    """
    _assert_git_repo(cwd)

    current = _get_current_branch(cwd)
    default = _get_default_branch(cwd)
    user_slug = _get_user_slug(cwd)

    # Try the fast path first: %(ahead-behind:ref) gives "ahead behind"
    # counts in one subprocess call (git ≥ 2.35).
    ok, raw = _run_git_ok(
        "for-each-ref",
        "--sort=-committerdate",
        f"--format=%(refname:short)\t%(committerdate:iso-strict)\t%(ahead-behind:{default})",
        "refs/heads/",
        cwd=cwd,
    )

    if not ok or not raw:
        # Fallback for very old git: no ahead-behind support.
        ok, raw = _run_git_ok(
            "for-each-ref",
            "--sort=-committerdate",
            "--format=%(refname:short)\t%(committerdate:iso-strict)",
            "refs/heads/",
            cwd=cwd,
        )

    branches: list[GitBranchItem] = []
    if ok and raw:
        for line in raw.splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                continue

            name = parts[0]
            commit_time = parts[1]

            # Parse ahead-behind if available (format: "ahead behind")
            commit_count = 0
            if len(parts) >= 3:
                ab = parts[2].split()
                if len(ab) == 2 and ab[0].isdigit():
                    commit_count = int(ab[0])
            else:
                # Slow fallback: one subprocess per branch
                ok_count, count_str = _run_git_ok(
                    "rev-list",
                    "--count",
                    f"{default}..{name}",
                    cwd=cwd,
                )
                if ok_count and count_str.isdigit():
                    commit_count = int(count_str)

            branches.append(
                GitBranchItem(
                    name=name,
                    is_yours=_is_own_branch(name, user_slug),
                    is_current=name == current,
                    is_archived=name.startswith(f"{_ARCHIVE_PREFIX}/"),
                    last_commit_time=commit_time,
                    commit_count=commit_count,
                )
            )

    # Sort: yours first, then others, archived last
    def sort_key(b: GitBranchItem) -> tuple[int, str]:
        if b.is_archived:
            return (2, b.name)
        if b.is_yours:
            return (0, b.name)
        return (1, b.name)

    branches.sort(key=sort_key)

    return GitBranchListResponse(current=current, branches=branches)


def switch_branch(branch: str, cwd: Path | None = None) -> None:
    """Switch to a branch, auto-committing any pending changes first."""
    _assert_git_repo(cwd)
    _validate_ref_name(branch)

    current = _get_current_branch(cwd)
    if branch == current:
        return

    # Auto-commit any pending changes before switching
    ok, status = _run_git_ok("status", "--porcelain", cwd=cwd)
    if ok and status.strip():
        _auto_commit(cwd)

    _run_git("checkout", branch, cwd=cwd)
    logger.info("branch_switched", from_branch=current, to_branch=branch)


def save_progress(cwd: Path | None = None) -> GitSaveResponse:
    """Stage all changes and commit.  Returns commit info.

    Never pushes — nothing leaves the machine except through the deliberate
    push surface. When the clone has a working branch configured, this
    legacy panel action is refused: pipeline saves already capture to the
    ledger automatically, and a second commit path would double-write it.
    """
    _assert_git_repo(cwd)

    from haute._git_state import read_working_branch

    if read_working_branch(cwd or Path.cwd()) is not None:
        raise GitDomainError(
            "This project captures every pipeline save in version history "
            "automatically — use Save in the toolbar instead."
        )

    branch = _get_current_branch(cwd)
    _assert_not_protected(branch)

    # Stage all changes
    _run_git("add", "-A", cwd=cwd)

    # Check if there's actually anything to commit
    ok, status = _run_git_ok("diff", "--cached", "--name-only", cwd=cwd)
    if not ok or not status.strip():
        raise GitDomainError("No changes to save.")

    changed = status.strip().splitlines()
    message = _generate_commit_message(changed)

    _run_git("commit", "-m", message, cwd=cwd)

    # Get commit info
    sha = _run_git("rev-parse", "HEAD", cwd=cwd)
    timestamp = _run_git("log", "-1", "--format=%aI", cwd=cwd)

    logger.info("changes_saved", sha=sha[:8], message=message)
    return GitSaveResponse(commit_sha=sha, message=message, timestamp=timestamp)


def _auto_commit(cwd: Path | None = None) -> None:
    """Internal: stage and commit all changes (used before branch switch)."""
    branch = _get_current_branch(cwd)
    if _is_protected(branch):
        return  # Don't auto-commit on protected branches

    _run_git("add", "-A", cwd=cwd)
    ok, status = _run_git_ok("diff", "--cached", "--name-only", cwd=cwd)
    if not ok or not status.strip():
        return  # Nothing to commit

    changed = status.strip().splitlines()
    message = _generate_commit_message(changed)
    _run_git("commit", "-m", message, cwd=cwd)
    # Commit locally only — never auto-push (S16, security: minimise egress).
    # Nothing leaves the machine except through the deliberate push surface.


def get_history(limit: int = 20, cwd: Path | None = None) -> list[GitHistoryEntry]:
    """Get commit history for the current branch.

    Uses ``git log --name-only`` to retrieve commit metadata *and*
    changed file paths in a single subprocess call instead of spawning
    a separate ``diff-tree`` per commit.
    """
    _assert_git_repo(cwd)

    default = _get_default_branch(cwd)
    branch = _get_current_branch(cwd)

    # Show commits on this branch since it diverged from default
    # If on default, show the last N commits
    if _is_protected(branch):
        range_spec = f"-{limit}"
    else:
        range_spec = f"{default}..{branch}"

    # --name-only appends changed file paths after each commit record.
    # We use a unique separator so we can split on it reliably.
    _sep = "---commit-sep---"
    ok, raw = _run_git_ok(
        "log",
        range_spec,
        f"--max-count={limit}",
        f"--format={_sep}%n%H\t%h\t%s\t%aI",
        "--name-only",
        cwd=cwd,
    )

    entries: list[GitHistoryEntry] = []
    if ok and raw:
        # Split on the separator to get per-commit blocks.
        blocks = raw.split(_sep)
        for block in blocks:
            block = block.strip()
            if not block:
                continue
            lines = block.splitlines()
            header = lines[0]
            parts = header.split("\t", 3)
            if len(parts) < 4:
                continue
            sha, short_sha, message, timestamp = parts

            # Remaining non-empty lines are changed file paths
            files_changed = [f for f in lines[1:] if f.strip()]

            entries.append(
                GitHistoryEntry(
                    sha=sha,
                    short_sha=short_sha,
                    message=message,
                    timestamp=timestamp,
                    files_changed=files_changed,
                )
            )

    return entries


def revert_to(sha: str, cwd: Path | None = None) -> GitRevertResponse:
    """Reset the current branch to a specific commit (with backup tag)."""
    _assert_git_repo(cwd)
    _validate_ref_name(sha)

    branch = _get_current_branch(cwd)
    _assert_not_protected(branch)

    # Validate the target SHA exists — use '--' to separate the SHA
    # from git options, preventing argument injection.
    ok, _ = _run_git_ok("cat-file", "-t", "--", sha, cwd=cwd)
    if not ok:
        raise GitDomainError(f"Commit '{sha}' not found.")

    # Create a backup tag before resetting
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%S")
    branch_slug = branch.replace("/", "-")
    backup_tag = f"backup/{branch_slug}/{now}"
    _run_git("tag", backup_tag, "HEAD", cwd=cwd)

    # Reset to the target commit.  The SHA is already validated by
    # _validate_ref_name (rejects leading dashes), so no '--' needed.
    # (git reset --hard treats '--' as a path separator, not an option
    # terminator, so adding it would break the command.)
    _run_git("reset", "--hard", sha, cwd=cwd)

    # Force-push to sync the remote (safe: this is a personal branch)
    if _has_remote(cwd):
        _run_git_ok("push", "origin", branch, "--force-with-lease", cwd=cwd)

    short_sha = sha[:7]
    logger.info("reverted", to=short_sha, backup=backup_tag)
    return GitRevertResponse(backup_tag=backup_tag, reverted_to=short_sha)


def pull_latest(cwd: Path | None = None) -> GitPullResponse:
    """Pull latest default branch into the current branch."""
    _assert_git_repo(cwd)

    branch = _get_current_branch(cwd)
    _assert_not_protected(branch)
    default = _get_default_branch(cwd)

    if not _has_remote(cwd):
        raise GitDomainError("No remote configured. Cannot pull latest changes.")

    # Auto-commit pending changes first
    ok, status = _run_git_ok("status", "--porcelain", cwd=cwd)
    if ok and status.strip():
        _auto_commit(cwd)

    # Fetch latest. Serialise with status polling fetches so git never has
    # two subprocesses racing on the local object store.
    with _fetch_exec_lock:
        _run_git("fetch", "origin", default, cwd=cwd)

    # Count how many commits we're pulling
    ok_count, count_str = _run_git_ok(
        "rev-list",
        "--count",
        f"HEAD..origin/{default}",
        cwd=cwd,
    )
    commits_to_pull = int(count_str) if ok_count and count_str.isdigit() else 0

    if commits_to_pull == 0:
        return GitPullResponse(
            success=True,
            conflict=False,
            conflict_message=None,
            commits_pulled=0,
        )

    # Attempt merge
    ok_merge, merge_output = _run_git_ok(
        "merge",
        f"origin/{default}",
        "--no-edit",
        cwd=cwd,
    )

    if not ok_merge:
        # Conflict detected — abort the merge
        _run_git_ok("merge", "--abort", cwd=cwd)
        logger.warning("merge_conflict", branch=branch)
        return GitPullResponse(
            success=False,
            conflict=True,
            conflict_message=(
                "Your changes overlap with recent updates to "
                f"'{default}'. Ask an engineer for help resolving "
                "this conflict."
            ),
            commits_pulled=0,
        )

    # Push the merge to remote
    if _has_remote(cwd):
        _run_git_ok("push", "origin", branch, cwd=cwd)

    logger.info("pull_complete", commits=commits_to_pull)
    return GitPullResponse(
        success=True,
        conflict=False,
        conflict_message=None,
        commits_pulled=commits_to_pull,
    )


def submit_for_review(cwd: Path | None = None) -> GitSubmitResponse:
    """Push branch and return a comparison URL for PR creation."""
    _assert_git_repo(cwd)

    branch = _get_current_branch(cwd)
    _assert_not_protected(branch)

    # Auto-commit any pending changes
    ok, status = _run_git_ok("status", "--porcelain", cwd=cwd)
    if ok and status.strip():
        _auto_commit(cwd)

    # Push
    if _has_remote(cwd):
        _run_git("push", "origin", branch, "--set-upstream", cwd=cwd)

    default = _get_default_branch(cwd)
    compare_url = _build_compare_url(branch, default, cwd)

    logger.info("submitted_for_review", branch=branch, url=compare_url)
    return GitSubmitResponse(compare_url=compare_url, branch=branch)


def archive_branch(branch: str, cwd: Path | None = None) -> str:
    """Rename a branch to archive/<name>."""
    _assert_git_repo(cwd)
    _validate_ref_name(branch)
    _assert_not_protected(branch)

    if branch.startswith(f"{_ARCHIVE_PREFIX}/"):
        raise GitDomainError(f"Branch '{branch}' is already archived.")

    current = _get_current_branch(cwd)
    default = _get_default_branch(cwd)

    # Strip prefix to get a clean archive name
    # "pricing/ralph/update-factors" → "archive/update-factors"
    parts = branch.split("/")
    # Take the last part as the descriptive name
    archive_name = f"{_ARCHIVE_PREFIX}/{parts[-1]}" if parts else f"{_ARCHIVE_PREFIX}/{branch}"

    # Ensure unique archive name
    ok, _ = _run_git_ok("rev-parse", "--verify", archive_name, cwd=cwd)
    if ok:
        # Add timestamp to make unique
        now = datetime.now(UTC).strftime("%Y%m%d")
        archive_name = f"{archive_name}-{now}"

    # Can't rename the current branch while on it — switch away first
    if branch == current:
        _run_git("checkout", default, cwd=cwd)

    _run_git("branch", "-m", branch, archive_name, cwd=cwd)

    # Push renamed branch and delete old remote ref
    if _has_remote(cwd):
        _run_git_ok("push", "origin", archive_name, cwd=cwd)
        _run_git_ok("push", "origin", "--delete", branch, cwd=cwd)

    logger.info("branch_archived", from_branch=branch, to=archive_name)
    return archive_name


def delete_branch(branch: str, cwd: Path | None = None) -> None:
    """Permanently delete a branch (local + remote)."""
    _assert_git_repo(cwd)
    _validate_ref_name(branch)
    _assert_not_protected(branch)

    current = _get_current_branch(cwd)
    default = _get_default_branch(cwd)

    if branch == current:
        _run_git("checkout", default, cwd=cwd)

    _run_git("branch", "-D", branch, cwd=cwd)

    if _has_remote(cwd):
        _run_git_ok("push", "origin", "--delete", branch, cwd=cwd)

    logger.info("branch_deleted", branch=branch)


# ---------------------------------------------------------------------------
# v1 engine — working/ledger branch-pair model
#
# Every working branch <W> is paired with a ledger branch <W>-save. Saves
# commit on the ledger (one commit per save); "save & commit" merges the
# ledger into the working branch with an always-real merge commit, so the
# working branch's first-parent chain reads as deliberate milestones while
# full save granularity stays reachable through each merge's second parent.
# HEAD lives on the ledger during normal operation.
#
# Healthy-state invariant (NOT naive ancestry — false from the first
# milestone onward): the working branch advances only via merges whose
# second parent is on its ledger; the working tip's TREE equals the tree of
# merge-base(working, ledger), which is the last-merged ledger commit (or
# the spawn point before any milestone exists).
# ---------------------------------------------------------------------------

LEDGER_SUFFIX = "-save"

BranchCategory = str  # "protected" | "ledger" | "working"


def ledger_name(working: str) -> str:
    """Return the ledger branch name paired with *working*."""
    return f"{working}{LEDGER_SUFFIX}"


def working_name(ledger: str) -> str | None:
    """Return the working branch a ledger serves, or None if not a ledger name."""
    if ledger.endswith(LEDGER_SUFFIX) and len(ledger) > len(LEDGER_SUFFIX):
        return ledger[: -len(LEDGER_SUFFIX)]
    return None


def branch_category(branch: str) -> BranchCategory:
    """Classify a branch name into the model's trichotomy.

    Naming-convention markers only: protected set first, then the ledger
    suffix, everything else is a working-branch candidate.
    """
    if _is_protected(branch):
        return "protected"
    if working_name(branch) is not None:
        return "ledger"
    return "working"


def is_eligible_working_branch(branch: str) -> bool:
    """Whether *branch* may be chosen as a working branch."""
    return branch_category(branch) == "working"


def _assert_eligible_working(branch: str) -> None:
    category = branch_category(branch)
    if category == "protected":
        raise GitGuardrailError(
            f"'{branch}' is a protected branch and cannot be used as a working branch."
        )
    if category == "ledger":
        raise GitGuardrailError(
            f"'{branch}' is a save ledger (managed by haute) and cannot be used as a "
            "working branch."
        )


def _rev_parse(ref: str, cwd: Path | None = None) -> str | None:
    """SHA for *ref*, or None when the ref does not resolve."""
    ok, sha = _run_git_ok("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", cwd=cwd)
    return sha.strip() if ok and sha.strip() else None


def _tree_of(ref: str, cwd: Path | None = None) -> str:
    """Tree object SHA for a commit-ish."""
    return _run_git("rev-parse", f"{ref}^{{tree}}", cwd=cwd).strip()


def _merge_base(a: str, b: str, cwd: Path | None = None) -> str | None:
    ok, base = _run_git_ok("merge-base", a, b, cwd=cwd)
    return base.strip() if ok and base.strip() else None


def _is_ancestor(ancestor: str, descendant: str, cwd: Path | None = None) -> bool:
    ok, _ = _run_git_ok("merge-base", "--is-ancestor", ancestor, descendant, cwd=cwd)
    return ok


def resolve_ledger(working: str, cwd: Path | None = None) -> str:
    """Find-or-create the ledger for *working* and check it out (HEAD-on-ledger).

    Lazy spawn: the ledger is created at the working branch's current tip on
    first use. Returns the ledger branch name.
    """
    _assert_git_repo(cwd)
    _validate_ref_name(working)
    _assert_eligible_working(working)

    if _rev_parse(working, cwd=cwd) is None:
        raise GitDomainError(f"Working branch '{working}' does not exist.")

    ledger = ledger_name(working)
    if _rev_parse(ledger, cwd=cwd) is None:
        _run_git("branch", ledger, working, cwd=cwd)
        logger.info("ledger_spawned", working=working, ledger=ledger)

    if _get_current_branch(cwd) != ledger:
        _run_git("checkout", ledger, cwd=cwd)

    return ledger


def commit_save(
    paths: list[str], working: str, cwd: Path | None = None, message: str | None = None
) -> str | None:
    """Record one save as one commit on the ledger of *working*.

    Pathspec-scoped: only *paths* enter the commit, regardless of any content
    the user may have staged in the meantime. Returns the new commit SHA, or
    None when none of *paths* changed (idempotent saves produce no empty
    commits).
    """
    if not paths:
        return None

    ledger = resolve_ledger(working, cwd=cwd)

    ok, status = _run_git_ok("status", "--porcelain", "--", *paths, cwd=cwd)
    if not ok or not status.strip():
        return None

    changed = [line[3:] for line in status.strip().splitlines()]
    msg = message if message is not None else _generate_commit_message(changed)

    # New files must be known to git before a pathspec'd commit can include
    # them; the explicit-path add also stages deletions of tracked paths.
    _run_git("add", "--", *paths, cwd=cwd)
    # `git commit -- <paths>` commits the working-tree state of exactly those
    # paths, bypassing unrelated index content the user may have pre-staged.
    _run_git("commit", "-m", msg, "--", *paths, cwd=cwd)

    sha = _run_git("rev-parse", "HEAD", cwd=cwd).strip()
    logger.info("save_committed", ledger=ledger, sha=sha, files=len(changed))
    return sha


def check_invariants(working: str, cwd: Path | None = None) -> list[str]:
    """Cheap plumbing checks of the branch-pair healthy-state invariant.

    Returns a list of human-readable violations (empty == healthy). Used at
    open and before every milestone merge.
    """
    _assert_git_repo(cwd)
    violations: list[str] = []

    working_tip = _rev_parse(working, cwd=cwd)
    if working_tip is None:
        return [f"working branch '{working}' does not exist"]

    ledger = ledger_name(working)
    ledger_tip = _rev_parse(ledger, cwd=cwd)
    if ledger_tip is None:
        return []  # pre-spawn: nothing to check

    base = _merge_base(working_tip, ledger_tip, cwd=cwd)
    if base is None:
        violations.append(f"'{working}' and '{ledger}' share no history")
        return violations

    if not _is_ancestor(base, ledger_tip, cwd=cwd):
        violations.append(f"merge-base of '{working}' and '{ledger}' is not on the ledger")

    if _tree_of(working_tip, cwd=cwd) != _tree_of(base, cwd=cwd):
        violations.append(
            f"'{working}' tip tree differs from its last-merged ledger commit — "
            "the working branch was advanced outside haute"
        )

    return violations


def merge_to_working(
    working: str,
    message: str,
    tag_label: str | None = None,
    cwd: Path | None = None,
) -> str:
    """Milestone merge: ledger → working, always a real merge commit.

    Produced with plumbing (``commit-tree`` + ``update-ref``) — no checkout,
    no index, and by construction never a fast-forward. The user-supplied
    *message* rides the merge commit itself. Returns the milestone SHA.
    """
    _assert_git_repo(cwd)
    _validate_ref_name(working)
    _assert_eligible_working(working)

    ledger = ledger_name(working)
    working_tip = _rev_parse(working, cwd=cwd)
    ledger_tip = _rev_parse(ledger, cwd=cwd)
    if working_tip is None:
        raise GitDomainError(f"Working branch '{working}' does not exist.")
    if ledger_tip is None:
        raise GitDomainError(f"'{working}' has no save ledger yet — nothing to commit.")

    if not message.strip():
        raise GitDomainError("A commit message is required.")

    violations = check_invariants(working, cwd=cwd)
    if violations:
        raise GitDomainError(
            "Cannot commit to the working branch: " + "; ".join(violations) + ". "
            "Use the branch manager to start a fresh branch from your current state."
        )

    base = _merge_base(working_tip, ledger_tip, cwd=cwd)
    if base == ledger_tip:
        raise GitDomainError("No new saves to commit — the working branch is up to date.")

    tree = _tree_of(ledger_tip, cwd=cwd)
    sha = _run_git(
        "commit-tree", tree, "-p", working_tip, "-p", ledger_tip, "-m", message, cwd=cwd
    ).strip()
    _run_git("update-ref", f"refs/heads/{working}", sha, working_tip, cwd=cwd)

    if tag_label is not None:
        _validate_ref_name(tag_label)
        tag_ref = f"version/{tag_label}"
        ok, _ = _run_git_ok("rev-parse", "--verify", "--quiet", f"refs/tags/{tag_ref}", cwd=cwd)
        if ok:
            raise GitDomainError(f"Version label '{tag_label}' already exists.")
        _run_git("tag", "-a", tag_ref, "-m", tag_label, sha, cwd=cwd)

    logger.info(
        "milestone_merged", working=working, sha=sha, tag=tag_label or "", ledger=ledger
    )
    return sha


# ---------------------------------------------------------------------------
# Commit identity (question 3) — detect and set git user.name / user.email.
# ---------------------------------------------------------------------------


def get_identity(cwd: Path | None = None) -> tuple[str | None, str | None]:
    """Return (user_name, user_email) from git config, each None when unset."""
    ok_name, name = _run_git_ok("config", "user.name", cwd=cwd)
    ok_email, email = _run_git_ok("config", "user.email", cwd=cwd)
    return (
        name.strip() if ok_name and name.strip() else None,
        email.strip() if ok_email and email.strip() else None,
    )


def set_identity(
    user_name: str,
    user_email: str,
    set_global: bool = False,
    cwd: Path | None = None,
) -> GitSetIdentityResponse:
    """Set git commit identity, repo-local by default (or global on request)."""
    _assert_git_repo(cwd)
    name = user_name.strip()
    email = user_email.strip()
    if not name or not email:
        raise GitDomainError("Both a name and an email are required.")

    scope_flag = "--global" if set_global else "--local"
    # These are plain config values, not refs, so _validate_ref_name does not
    # apply — but reject ALL control characters defensively (newlines and other
    # C0/DEL chars would corrupt the git config file or inject extra lines).
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in name + email):
        raise GitDomainError("Name and email must not contain control characters.")

    _run_git("config", scope_flag, "user.name", name, cwd=cwd)
    _run_git("config", scope_flag, "user.email", email, cwd=cwd)
    logger.info("git_identity_set", scope="global" if set_global else "local")
    return GitSetIdentityResponse(
        user_name=name, user_email=email, scope="global" if set_global else "local"
    )


# ---------------------------------------------------------------------------
# Working-branch selection (P2) — compose state file + engine into the
# readiness signal the startup flow and indicator consume, and the setter.
# ---------------------------------------------------------------------------


def _eligible_working_branches(cwd: Path | None = None) -> list[str]:
    """Names choosable as a working branch: not protected, ledger, archived, or
    the repo's default branch (which is deploy-only, like the hardcoded
    protected set — PROTECTED_BRANCHES being configurable is a later item)."""
    listing = list_branches(cwd=cwd)
    default = _get_default_branch(cwd)
    return [
        b.name
        for b in listing.branches
        if not b.is_archived
        and b.name != default
        and is_eligible_working_branch(b.name)
    ]


def _ledger_or_branch_sha(branch: str, cwd: Path | None = None) -> str | None:
    """Short SHA of the branch's ledger tip, or the branch tip pre-spawn."""
    ledger = ledger_name(branch)
    tip = _rev_parse(ledger, cwd=cwd) or _rev_parse(branch, cwd=cwd)
    return tip[:8] if tip else None


def working_branch_status(
    project_root: Path, cwd: Path | None = None
) -> GitWorkingBranchResponse:
    """Compute the working-branch readiness signal for a clone.

    state is one of:
      - "unset"     — no working branch recorded
      - "invalid"   — recorded branch missing / ineligible / invariants violated
      - "divergent" — recorded branch fine, but HEAD is on neither it nor its
                      ledger (user moved the repo outside haute)
      - "ready"     — recorded branch is the current lineage and healthy
    """
    from haute._git_state import read_working_branch

    _assert_git_repo(cwd)
    current = _get_current_branch(cwd)
    name, email = get_identity(cwd)
    identity_set = name is not None and email is not None
    eligible = _eligible_working_branches(cwd)

    working = read_working_branch(project_root)
    base = GitWorkingBranchResponse(
        working_branch=working,
        current_branch=current,
        eligible_branches=eligible,
        identity_set=identity_set,
        user_name=name,
        user_email=email,
    )

    if working is None:
        base.state = "unset"
        return base

    base.last_save_sha = _ledger_or_branch_sha(working, cwd=cwd)

    if not is_eligible_working_branch(working):
        base.state = "invalid"
        base.errors = [f"'{working}' is no longer a valid working branch."]
        return base
    if _rev_parse(working, cwd=cwd) is None:
        base.state = "invalid"
        base.errors = [f"Working branch '{working}' no longer exists."]
        return base

    violations = check_invariants(working, cwd=cwd)
    if violations:
        base.state = "invalid"
        base.errors = violations
        return base

    if current not in (working, ledger_name(working)):
        base.state = "divergent"
        return base

    base.state = "ready"
    return base


def set_working_branch(
    branch: str,
    project_root: Path,
    create: bool = False,
    cwd: Path | None = None,
) -> GitSetWorkingBranchResponse:
    """Adopt *branch* as this clone's working branch.

    Validates eligibility, optionally creates the branch off current HEAD,
    spawns + checks out its ledger (HEAD-on-ledger, S10), and records the
    association. The startup modal's confirm and the save-gate both land here.
    """
    from haute._git_state import write_working_branch

    _assert_git_repo(cwd)
    _validate_ref_name(branch)
    _assert_eligible_working(branch)

    exists = _rev_parse(branch, cwd=cwd) is not None
    if create:
        if exists:
            raise GitDomainError(f"Branch '{branch}' already exists.")
        _run_git("checkout", "-b", branch, cwd=cwd)
    elif not exists:
        raise GitDomainError(f"Branch '{branch}' does not exist.")

    # Spawn (if needed) and move HEAD onto the ledger — normal operating posture.
    resolve_ledger(branch, cwd=cwd)
    write_working_branch(project_root, branch)
    logger.info("working_branch_set", branch=branch, created=create)

    return GitSetWorkingBranchResponse(
        working_branch=branch,
        state="ready",
        last_save_sha=_ledger_or_branch_sha(branch, cwd=cwd),
    )
