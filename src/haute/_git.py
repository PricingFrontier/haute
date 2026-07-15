"""Git operations layer with guardrails for non-technical users.

All git CLI interactions go through this module.  Routes never call
``subprocess`` directly — this gives us a single place for:

- **Guardrails** — refuse operations on protected branches
- **Error handling** — translate git errors to user-friendly messages
- **Backup safety nets** — tag before destructive operations (revert)
"""

from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import tarfile
import threading
import time
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from haute._gitignore_guard import ensure_gitignore_guards
from haute._logging import get_logger
from haute.errors import HauteError
from haute.schemas import (
    GitArchiveResponse,
    GitBranchAwayResponse,
    GitBranchItem,
    GitBranchListResponse,
    GitCommitContext,
    GitCommitRef,
    GitCommitResponse,
    GitCreateWorkingBranchResponse,
    GitDeleteBranchResponse,
    GitFastForwardResponse,
    GitFileChange,
    GitGraphBranch,
    GitGraphEntry,
    GitGraphResponse,
    GitLedgerSave,
    GitLedgerSavesResponse,
    GitManagedBranch,
    GitMilestoneEntry,
    GitMilestoneFork,
    GitMilestonesResponse,
    GitMoveResponse,
    GitPrefs,
    GitPushRejection,
    GitPushResponse,
    GitRemote,
    GitRemoteLeg,
    GitRemotesResponse,
    GitRestoreResponse,
    GitSetIdentityResponse,
    GitSetWorkingBranchResponse,
    GitStatusResponse,
    GitUndeleteResponse,
    GitWorkingBranchesResponse,
    GitWorkingBranchResponse,
)

logger = get_logger(component="git")

DEFAULT_PROTECTED_BRANCHES = frozenset({"main", "master", "develop", "production"})
PROTECTED_BRANCHES = DEFAULT_PROTECTED_BRANCHES

# Branch names created by haute follow: pricing/<user>/<slug>
_BRANCH_PREFIX = "pricing"
_ARCHIVE_PREFIX = "archive"

# Minimum seconds between `git fetch` calls per (cwd, remote, kind).
_FETCH_COOLDOWN_SECONDS: float = 30.0
# Hard ceiling on a single background fetch so a slow / unreachable / auth-walled
# remote can never wedge the request thread (F1).
_FETCH_TIMEOUT_SECONDS: float = 10.0
# Per-(cwd, remote, kind) last-fetch timestamps. Keyed — not one global float —
# so concurrent worktrees served by a single process don't share one cooldown
# window, where one clone's fetch would starve another's (F7). ``kind`` keeps
# fetch families independent (the deploy-branch peek vs. the working-pair fetch).
_fetch_cooldowns: dict[tuple[str, str, str], float] = {}
_fetch_time_lock = threading.Lock()
# Serialises the actual ``git fetch`` subprocess — two concurrent callers that
# both pass the cooldown window must not launch parallel fetches because git
# races on the local .git/objects index. Stays process-global on purpose: git
# worktrees share one object store, so the exec lock must span them all.
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


class GitPushRejectedError(GitDomainError):
    """A non-fast-forward push rejection carrying the per-leg divergence (P7 M7).

    Subclasses :class:`GitDomainError` (the message is hand-written and surfaces
    verbatim), but the HTTP layer special-cases it to a **409** with the
    structured :class:`~haute.schemas.GitPushRejection` body so the UI can draw
    the honest fork — which leg moved, by how much — instead of a dead-end
    string. The ``message`` still reads sensibly on its own for any client that
    only looks at the text.
    """

    def __init__(self, rejection: GitPushRejection) -> None:
        super().__init__(rejection.message)
        self.rejection = rejection


class GitMilestoneForkError(GitDomainError):
    """A milestone refused because it would fork the remote working branch (U4).

    Subclasses :class:`GitDomainError`, but the HTTP layer maps it to **409** with
    the structured :class:`~haute.schemas.GitMilestoneFork` body so the UI can
    warn and offer "commit anyway (creates a fork)". Only raised when the override
    is off and the working branch is measurably behind/diverged from its canonical
    remote on locally-known refs — so a local-only or offline user is never
    blocked (the gate degrades open)."""

    def __init__(self, fork: GitMilestoneFork) -> None:
        super().__init__(fork.message)
        self.fork = fork


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


def _run_git(
    *args: str,
    check: bool = True,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> str:
    """Run a git command and return stdout.  Raises ``GitError`` on failure.

    The raw subprocess stderr is wrapped in a plain :class:`GitError`
    (the sanitize-by-default class) so the HTTP handler collapses the
    detail to ``_INTERNAL_ERROR_DETAIL`` — raw stderr commonly contains
    absolute paths, remote URLs, SSL errors, and credentials.
    Full detail is retained in the ``git_command_failed`` structured log.

    *env* overlays the inherited environment (used to preserve author/committer
    identity + dates when replaying commits via ``commit-tree``).
    """
    cmd = ["git"] + list(args)
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=cwd or Path.cwd(),
        env={**os.environ, **env} if env else None,
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
        encoding="utf-8",
        cwd=cwd or Path.cwd(),
    )
    return result.returncode == 0, result.stdout.strip()


def _run_git_rc(*args: str, cwd: Path | None = None) -> tuple[int, str]:
    """Run a git command and return (returncode, stdout).  Never raises.

    Exists alongside :func:`_run_git_ok` for callers that must distinguish a
    SEMANTIC non-zero exit from a real failure — ``merge-base --is-ancestor``
    exits 1 for "not an ancestor" (a valid, cacheable answer) but >1 for an
    unreadable object (which must never be cached).
    """
    cmd = ["git"] + list(args)
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=cwd or Path.cwd(),
    )
    return result.returncode, result.stdout.strip()


def _should_fetch(remote: str, cwd: Path | None = None, kind: str = "deploy") -> bool:
    """Whether a throttled background fetch may run now for this
    ``(cwd, remote, kind)``, claiming the cooldown slot if so.

    Keyed per-worktree (F7) so concurrent clones served by one process each get
    their own window rather than starving each other through a shared global.
    """
    key = (str(cwd) if cwd is not None else "", remote, kind)
    now = time.monotonic()
    with _fetch_time_lock:
        if now - _fetch_cooldowns.get(key, 0.0) >= _FETCH_COOLDOWN_SECONDS:
            _fetch_cooldowns[key] = now
            return True
    return False


def _fetch_refs(remote: str, *refs: str, cwd: Path | None = None) -> bool:
    """Run a prompt-proof, time-bounded ``git fetch``; return success.

    A background fetch must never block the UI on a credential prompt or a hung
    connection (F1): terminal and SSH prompts are disabled and the subprocess is
    killed after ``_FETCH_TIMEOUT_SECONDS``. Any failure — including timeout —
    degrades silently to the locally-known remote-tracking refs; a fetch only
    ever updates ``refs/remotes/*``, so failing to fetch breaks no local
    invariant. Callers serialise via ``_fetch_exec_lock`` (shared object store).

    ``credential.helper`` is deliberately left intact: ``GIT_TERMINAL_PROMPT=0``
    + SSH ``BatchMode`` already stop interactive prompts, and the timeout bounds
    any hang, so a legitimately-configured non-interactive helper (token cache)
    keeps working rather than being forced off.
    """
    env = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_SSH_COMMAND": "ssh -oBatchMode=yes -oConnectTimeout=10",
    }
    cmd = ["git", "fetch", remote, *refs, "--quiet"]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=cwd or Path.cwd(),
            env=env,
            timeout=_FETCH_TIMEOUT_SECONDS,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("git_fetch_degraded", remote=remote, error=str(exc))
        return False
    if result.returncode != 0:
        logger.debug("git_fetch_failed", remote=remote, stderr=result.stderr.strip())
        return False
    return True


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
    # X5: resolve the deploy branch against the CANONICAL remote, not a hardcoded
    # ``origin`` — a clone whose sole remote is named e.g. "upstream" still reads
    # a correct default branch instead of falling through to the local guesses.
    remote = _canonical_remote(cwd)
    if remote is not None:
        ok, ref = _run_git_ok(
            "symbolic-ref",
            f"refs/remotes/{remote}/HEAD",
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
    # No remote HEAD and neither main nor master exists locally: fall back to a
    # branch that is GUARANTEED to exist — the current one — rather than
    # inventing "main" (which would make switch-away checkouts fail, and would
    # leak the real default branch into the working-branch manager list).
    return _get_current_branch(cwd)


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


def _protected_branches() -> frozenset[str]:
    """Return the protected-branch set, overridable via the
    ``HAUTE_PROTECTED_BRANCHES`` env var (comma-separated). An empty entry
    fails loudly so a misconfigured var can't silently drop a guard.
    """
    configured = os.environ.get("HAUTE_PROTECTED_BRANCHES")
    if configured is None:
        return DEFAULT_PROTECTED_BRANCHES

    branches: list[str] = []
    for raw in configured.split(","):
        branch = raw.strip()
        if not branch:
            raise GitGuardrailError("HAUTE_PROTECTED_BRANCHES contains an empty branch entry.")
        _validate_ref_name(branch)
        branches.append(branch)
    return frozenset(branches)


def _is_protected(branch: str) -> bool:
    return branch in _protected_branches()


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


def _canonical_remote(cwd: Path | None = None) -> str | None:
    """The single remote haute reads divergence against (X5).

    The read-side baseline must name ONE remote so it can't disagree with the
    push target: ``origin`` when configured, else the sole remote when exactly
    one exists, else ``None`` — genuinely ambiguous (several non-origin remotes)
    or offline, in which case callers report "can't tell" rather than guessing a
    wrong baseline against a non-existent ``origin/<default>``. The push surface
    still accepts any remote by name; this only governs the status /
    default-branch reads.
    """
    remotes = _remote_names(cwd)
    if "origin" in remotes:
        return "origin"
    if len(remotes) == 1:
        return remotes[0]
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

    # How far ahead is the default branch? Measured against the CANONICAL remote
    # (X5: ``origin`` if present, else the sole remote, else none) so the baseline
    # can't silently read a non-existent ``origin/<default>`` on a clone whose
    # remote is named otherwise — that would report ``main_ahead=False`` (looks
    # in-sync) when it genuinely can't tell.
    main_ahead_by = 0
    main_last_updated: str | None = None
    remote = _canonical_remote(cwd)
    if remote is not None and not is_main:
        # Fetch silently — throttled per (cwd, remote) so frequent polls and
        # concurrent worktrees neither hammer the remote nor starve each other
        # (F7), and hardened so a slow / auth-walled remote can't hang the poll
        # (F1). A failed fetch degrades to the last-known remote-tracking ref.
        if _should_fetch(remote, cwd=cwd, kind="deploy"):
            # Serialise the actual subprocess — git fetch races on the
            # local object store if two processes run concurrently.
            with _fetch_exec_lock:
                _fetch_refs(remote, default, cwd=cwd)

        ok_count, count_str = _run_git_ok(
            "rev-list",
            "--count",
            f"HEAD..{remote}/{default}",
            cwd=cwd,
        )
        if ok_count and count_str.isdigit():
            main_ahead_by = int(count_str)

        if main_ahead_by > 0:
            ok_time, timestamp = _run_git_ok(
                "log",
                "-1",
                "--format=%aI",
                f"{remote}/{default}",
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


def _list_branches_with_tips(
    cwd: Path | None = None,
) -> tuple[GitBranchListResponse, dict[str, str]]:
    """List all local branches (user's first), plus a ``{short ref name: tip
    SHA}`` map covering EVERY local head (ledgers and archived pairs included),
    both read from a single ``for-each-ref`` call via ``%(objectname)``.

    The graph and branch-manager paths key their content-addressed caches by
    these tips instead of issuing per-branch ``rev-parse`` subprocesses — refs
    are resolved to SHAs exactly once per request, here.

    The format carries only names, tips and commit times: no per-branch
    ahead-behind walk, because no caller consumes commit counts. The
    current/user-slug reads stay — ``is_yours`` drives the yours-first sort,
    which IS consumed (the branch-manager render order and the startup modal's
    ``eligible[0]`` preselection follow it)."""
    _assert_git_repo(cwd)

    current = _get_current_branch(cwd)
    user_slug = _get_user_slug(cwd)

    ref_format = "%(refname:short)\t%(objectname)\t%(committerdate:iso-strict)"
    ok, raw = _run_git_ok(
        "for-each-ref",
        "--sort=-committerdate",
        f"--format={ref_format}",
        "refs/heads/",
        cwd=cwd,
    )

    branches: list[GitBranchItem] = []
    tips: dict[str, str] = {}
    if ok and raw:
        for line in raw.splitlines():
            parts = line.split("\t")
            if len(parts) < 3:
                continue

            name = parts[0]
            tip_sha = parts[1]
            commit_time = parts[2]
            tips[name] = tip_sha

            branches.append(
                GitBranchItem(
                    name=name,
                    is_yours=_is_own_branch(name, user_slug),
                    is_current=name == current,
                    is_archived=name.startswith(f"{_ARCHIVE_PREFIX}/"),
                    last_commit_time=commit_time,
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

    return GitBranchListResponse(current=current, branches=branches), tips


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


@lru_cache(maxsize=1024)
def _tree_of_cached(sha: str, cwd_key: str) -> str:
    """Cached inner — a commit's tree SHA is content-addressed. ``_run_git``
    raises on failure and ``lru_cache`` never memoises a raising call, so an
    unreadable object is never cached."""
    return _run_git("rev-parse", f"{sha}^{{tree}}", cwd=Path(cwd_key) if cwd_key else None).strip()


def _tree_of(ref: str, cwd: Path | None = None) -> str:
    """Tree object SHA for a commit-ish. Cached per (sha, cwd) when *ref* is a
    full SHA — a commit's tree never changes — so the invariant check and the
    milestone/save folds, which resolve refs to SHAs before calling here, pay
    the ``rev-parse`` only once per distinct commit."""
    if _is_full_sha(ref):
        return _tree_of_cached(ref, str(cwd) if cwd else "")
    return _run_git("rev-parse", f"{ref}^{{tree}}", cwd=cwd).strip()


# ---------------------------------------------------------------------------
# Content-addressed caches — a full commit SHA names an immutable object, so
# any pure derivation from full SHAs (ancestry, merge-base, parents, the
# first-parent spine, a windowed log) is itself immutable and can be cached
# forever with no invalidation story. Ref names are NOT invariant, so only
# full-40-hex-SHA arguments take the cached path (anything else falls through
# to a live subprocess), and TAG-derived data (version labels) is never cached
# here — tags move independently of the commits they point at. Failures
# (unreadable ref/object) are never cached: each inner cached function raises
# ``GitError`` on failure and ``functools.lru_cache`` does not memoise a call
# that raises; the public wrappers catch and keep the old failure semantics.
# Keys include ``str(cwd)`` (the ``_get_default_branch_cached`` precedent) so
# repos served by one process never share entries.
# ---------------------------------------------------------------------------

_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _is_full_sha(value: str) -> bool:
    """Whether *value* is a full 40-hex commit SHA (the cacheable key shape)."""
    return bool(_FULL_SHA_RE.match(value))


@lru_cache(maxsize=1024)
def _merge_base_cached(a: str, b: str, cwd_key: str) -> str:
    """Cached inner — raises on any failure (incl. no common ancestor) so
    lru_cache never stores it; only a real base SHA is memoised."""
    ok, base = _run_git_ok("merge-base", a, b, cwd=Path(cwd_key) if cwd_key else None)
    if not ok or not base.strip():
        raise GitError(f"merge-base failed for {a} {b}")
    return base.strip()


def _merge_base(a: str, b: str, cwd: Path | None = None) -> str | None:
    if _is_full_sha(a) and _is_full_sha(b):
        try:
            return _merge_base_cached(a, b, str(cwd) if cwd else "")
        except GitError:
            return None
    ok, base = _run_git_ok("merge-base", a, b, cwd=cwd)
    return base.strip() if ok and base.strip() else None


@lru_cache(maxsize=4096)
def _is_ancestor_cached(ancestor: str, descendant: str, cwd_key: str) -> bool:
    """Cached inner — exit 0/1 are the two valid (immutable) answers; any other
    exit is an unreadable object and raises, so it is never memoised."""
    code, _ = _run_git_rc(
        "merge-base",
        "--is-ancestor",
        ancestor,
        descendant,
        cwd=Path(cwd_key) if cwd_key else None,
    )
    if code == 0:
        return True
    if code == 1:
        return False
    raise GitError(f"merge-base --is-ancestor failed for {ancestor} {descendant}")


def _is_ancestor(ancestor: str, descendant: str, cwd: Path | None = None) -> bool:
    if _is_full_sha(ancestor) and _is_full_sha(descendant):
        try:
            return _is_ancestor_cached(ancestor, descendant, str(cwd) if cwd else "")
        except GitError:
            return False
    ok, _ = _run_git_ok("merge-base", "--is-ancestor", ancestor, descendant, cwd=cwd)
    return ok


@lru_cache(maxsize=64)
def _first_parent_spine_cached(tip_sha: str, cwd_key: str) -> tuple[str, ...]:
    """Cached inner — the full first-parent chain below an immutable tip SHA."""
    ok, raw = _run_git_ok(
        "rev-list",
        "--first-parent",
        tip_sha,
        "--",
        cwd=Path(cwd_key) if cwd_key else None,
    )
    if not ok or not raw.strip():
        raise GitError(f"rev-list --first-parent failed for {tip_sha}")
    return tuple(raw.split())


def _first_parent_spine(tip: str, cwd: Path | None = None) -> list[str] | None:
    """Full first-parent chain of *tip*, newest first; ``None`` when unreadable.

    Cached per (tip SHA, cwd) when *tip* is a full SHA — the spine below a
    commit is content-addressed, so a branch whose tip hasn't moved costs no
    subprocess on re-read, and any tip move (fast-forward or rewrite) is a new
    key and therefore immediately fresh."""
    if _is_full_sha(tip):
        try:
            return list(_first_parent_spine_cached(tip, str(cwd) if cwd else ""))
        except GitError:
            return None
    ok, raw = _run_git_ok("rev-list", "--first-parent", tip, "--", cwd=cwd)
    return raw.split() if ok and raw.strip() else None


def _clear_content_caches() -> None:
    """Drop every content-addressed cache (test isolation + repo-reset hygiene,
    alongside ``_get_default_branch_cached.cache_clear()``)."""
    _merge_base_cached.cache_clear()
    _is_ancestor_cached.cache_clear()
    _first_parent_spine_cached.cache_clear()
    _commit_parents_cached.cache_clear()
    _graph_log_cached.cache_clear()
    _tree_of_cached.cache_clear()


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
    # Reject C0 control characters (except tab/newline/CR, which are valid in a
    # multi-line message). A stray record-separator etc. would otherwise corrupt
    # the ledger-history parser, which delimits commits with \x1e.
    if any((ord(c) < 0x20 and c not in "\t\n\r") or ord(c) == 0x7F for c in message):
        raise GitDomainError("Commit message must not contain control characters.")

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

    logger.info("milestone_merged", working=working, sha=sha, tag=tag_label or "", ledger=ledger)
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
    protected set — PROTECTED_BRANCHES being configurable is a later item).

    Only names, archived flags and the yours-first order are consumed — the
    startup modal preselects the FIRST eligible branch, so the order must match
    the branch listing's."""
    listing, _ = _list_branches_with_tips(cwd=cwd)
    default = _get_default_branch(cwd)
    return [
        b.name
        for b in listing.branches
        if not b.is_archived and b.name != default and is_eligible_working_branch(b.name)
    ]


def _ledger_or_branch_sha(branch: str, cwd: Path | None = None) -> str | None:
    """Short SHA of the branch's ledger tip, or the branch tip pre-spawn."""
    ledger = ledger_name(branch)
    tip = _rev_parse(ledger, cwd=cwd) or _rev_parse(branch, cwd=cwd)
    return tip[:8] if tip else None


def working_branch_status(project_root: Path, cwd: Path | None = None) -> GitWorkingBranchResponse:
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


# What haute owns in a project tree — the permit half of the unborn-repo
# seed's defence-in-depth (haute._gitignore_guard carries the deny half).
# Shapes follow the `haute init` scaffold plus the nested-pipeline layout.
# Default git pathspec globs: '*' also matches '/', so "*.py" covers
# rating/utility/helpers.py and "*/config/*" covers any pipeline's config
# dir (a wildcard combined with a bare trailing slash matches nothing —
# hence the explicit /* suffix there).
_SEED_PATHSPECS: tuple[str, ...] = (
    "haute.toml",
    "pyproject.toml",
    "uv.lock",
    ".gitignore",
    ".env.example",
    "*.py",
    "*.haute.json",
    "config/",
    "*/config/*",
    "prompts/",
    "tests/",
    ".githooks/",
    ".github/",
    ".gitlab-ci.yml",
    "azure-pipelines.yml",
)


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

    When *create=True* and the repo has no commits yet (unborn HEAD), a root
    commit is seeded on the current branch first so the new branch forks off a
    real ref.  The create path is all-or-nothing: a failure after the branch is
    created restores HEAD and deletes the half-created branch.
    """
    from haute._git_state import write_working_branch

    _assert_git_repo(cwd)
    _validate_ref_name(branch)
    _assert_eligible_working(branch)

    exists = _rev_parse(branch, cwd=cwd) is not None
    if create:
        if exists:
            raise GitDomainError(f"Branch '{branch}' already exists.")

        # Unborn-repo seeding: when HEAD has no commits yet, plant a root commit
        # on the canonical default branch so the working branch can fork off a
        # real ref.  The root commit is a permanent, legitimate state — a retry
        # after a partial failure simply takes the normal (born) code path.
        #
        # If HEAD is on an unborn working-category branch (e.g. "initial-branch"
        # left by a prior failed attempt), rename it to "main" first so the root
        # commit lands on the default, never on the branch we are about to create.
        # Protected branches (main/master/etc.) are kept as-is.
        if _rev_parse("HEAD", cwd=cwd) is None:
            current_unborn = _get_current_branch(cwd)
            if branch_category(current_unborn) != "protected":
                # A born 'main' can only coexist with an unborn non-protected HEAD
                # via `git checkout --orphan` outside haute; the rename would then
                # collide, so surface a clear domain error rather than a raw git one.
                if _rev_parse("main", cwd=cwd) is not None:
                    raise GitDomainError(
                        "Cannot seed the initial commit: HEAD is on an unborn branch "
                        "but 'main' already exists."
                    )
                _run_git("branch", "-m", "main", cwd=cwd)
                _get_default_branch_cached.cache_clear()
                _clear_content_caches()

            # Seed staging is defence-in-depth: a file
            # enters the root commit only by matching a haute-owned pathspec
            # (_SEED_PATHSPECS, the permit gate) AND surviving the .gitignore
            # guard entries (the deny gate). `haute init` writes the guards,
            # but a foreign `git init` repo carries no such guarantee — assert
            # them here, or the seed publishes .env into history and commits
            # .haute/ (the clone-lockout class).
            toplevel = Path(_run_git("rev-parse", "--show-toplevel", cwd=cwd).strip())
            ensure_gitignore_guards(toplevel)
            # Pre-staged index content bypasses .gitignore and would ride into
            # the commit unstaged-checks notwithstanding; start from an empty
            # index so the two gates alone decide what enters the root commit.
            # --cached leaves the working tree untouched; -f covers
            # stage-vs-disk mismatches.
            _run_git("rm", "-r", "-f", "-q", "--cached", "--ignore-unmatch", "--", ".", cwd=cwd)
            # ls-files applies both gates: the pathspecs permit, and
            # --exclude-standard enforces the ignore rules.
            listed = _run_git(
                "ls-files",
                "--others",
                "--exclude-standard",
                "-z",
                "--",
                *_SEED_PATHSPECS,
                cwd=cwd,
            )
            seed_files = [f for f in listed.split("\0") if f]
            if seed_files:
                # :(literal) so a filename containing glob characters cannot
                # re-expand into a broader pathspec at add time.
                _run_git("add", "--", *[f":(literal){f}" for f in seed_files], cwd=cwd)
            # If nothing was staged (empty working tree) use --allow-empty so we
            # still establish a root commit and the model remains consistent.
            ok_diff, _ = _run_git_ok("diff", "--cached", "--quiet", cwd=cwd)
            commit_extra = ["--allow-empty"] if ok_diff else []
            try:
                _run_git("commit", *commit_extra, "-m", "Initial commit", cwd=cwd)
            except GitError as exc:
                stderr = str(exc)
                if any(
                    kw in stderr
                    for kw in (
                        "Author identity unknown",
                        "Please tell me who you are",
                        "user.email",
                        "user.name",
                    )
                ):
                    raise GitDomainError(
                        "Cannot create the initial commit: set your git name and email first."
                    ) from exc
                raise

        # Capture pre-creation position for atomic rollback.
        ok_sym, sym = _run_git_ok("symbolic-ref", "--short", "HEAD", cwd=cwd)
        if ok_sym:
            _rb_ref = sym.strip()
            _rb_detach = False
        else:
            # Detached HEAD (e.g. after move_to_commit): capture SHA for restore.
            _rb_ref = _run_git("rev-parse", "HEAD", cwd=cwd)
            _rb_detach = True

        try:
            _run_git("checkout", "-b", branch, cwd=cwd)
            resolve_ledger(branch, cwd=cwd)
            write_working_branch(project_root, branch)
        except Exception:
            # All-or-nothing: restore HEAD and delete the half-created branch so
            # a retry is not blocked and the repo is left in a coherent state.
            # Also drop the ledger resolve_ledger may have just spawned — leaving
            # it orphaned would let a later fork off an advanced default read as
            # `invalid` via check_invariants (tree mismatch vs the stale ledger).
            if _rb_detach:
                _run_git_ok("checkout", "--detach", _rb_ref, cwd=cwd)
            else:
                _run_git_ok("checkout", _rb_ref, cwd=cwd)
            _run_git_ok("branch", "-D", branch, cwd=cwd)
            _run_git_ok("branch", "-D", ledger_name(branch), cwd=cwd)
            raise
    else:
        if not exists:
            raise GitDomainError(f"Branch '{branch}' does not exist.")
        # Adopt existing branch: spawn ledger if needed and move HEAD onto it.
        resolve_ledger(branch, cwd=cwd)
        write_working_branch(project_root, branch)

    logger.info("working_branch_set", branch=branch, created=create)

    return GitSetWorkingBranchResponse(
        working_branch=branch,
        state="ready",
        last_save_sha=_ledger_or_branch_sha(branch, cwd=cwd),
    )


# ---------------------------------------------------------------------------
# Move through history (P6 — §3.4). A move materialises a historical commit's
# tree as the working directory via a detached checkout. It creates nothing and
# moves no ref: HEAD detaches at the target and the working-branch association is
# cleared, so the next save re-enters the S5/S13 modal to spawn a fresh
# working+ledger pair there. Read-only viewing (archive_commit / git show) is the
# no-checkout counterpart; this is the real tree mutation.
# ---------------------------------------------------------------------------

# Volatile on-disk artefacts (S12/D8): reconstructable caches + outputs that must
# not bleed across a move into a different version's tree. Wiped best-effort
# before the checkout; failures are logged, never fatal (the contract is that
# they regenerate).
_VOLATILE_ARTEFACTS = (
    "__pycache__",
    "output",
    "outputs",
    ".haute_cache",
    ".cache",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".webassets-cache",
)


def _git_dir(cwd: Path | None = None) -> Path:
    """Absolute path to the repo's ``.git`` directory (worktree-safe)."""
    raw = _run_git("rev-parse", "--git-dir", cwd=cwd)
    path = Path(raw)
    return path if path.is_absolute() else (cwd or Path.cwd()) / path


def _assert_no_git_op_in_progress(cwd: Path | None = None) -> None:
    """Row H (§3.9): refuse haute git ops while a merge/rebase/cherry-pick is
    mid-flight — the user must finish or abort it outside haute first."""
    git_dir = _git_dir(cwd)
    in_progress = (
        (git_dir / "MERGE_HEAD").exists()
        or (git_dir / "CHERRY_PICK_HEAD").exists()
        or (git_dir / "REVERT_HEAD").exists()
        or (git_dir / "rebase-merge").is_dir()
        or (git_dir / "rebase-apply").is_dir()
    )
    if in_progress:
        raise GitDomainError(
            "A git operation is in progress; finish or abort it outside haute "
            "before moving to another version."
        )


def _wipe_volatile_artefacts(repo_root: Path) -> None:
    """Best-effort wipe of reconstructable on-disk volatile state (S12)."""
    for name in _VOLATILE_ARTEFACTS:
        target = repo_root / name
        if not target.exists():
            continue
        try:
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target, ignore_errors=True)
            else:
                target.unlink(missing_ok=True)
        except OSError as exc:  # pragma: no cover - defensive; rmtree swallows most
            logger.warning("volatile_wipe_failed", path=name, error=str(exc))


def move_to_commit(sha: str, project_root: Path, cwd: Path | None = None) -> GitMoveResponse:
    """Move the working directory to *sha* — its tree becomes the repo state.

    A detached-HEAD checkout (§3.4): creates nothing and moves no ref, so the
    prior branch keeps pointing at its tip and stays fully reachable (unlike v0's
    revert, which reset a ref and could orphan milestones). The working-branch
    association is cleared, leaving the clone in the 'unset' state so the next
    save spawns a fresh working+ledger pair here (S13).

    Pre-move floors (§3.9): refuse if a git operation is in progress (row H) or
    if the tree has uncommitted tracked changes (row A / S21) — resolution
    happens via save-or-discard *before* the move, never silently here. Volatile
    on-disk artefacts are wiped (S12).
    """
    from haute._git_state import clear_working_branch

    _assert_git_repo(cwd)
    _validate_ref_name(sha)

    # Floor (row H): no haute git op while a merge/rebase/cherry-pick is unfinished.
    _assert_no_git_op_in_progress(cwd)

    # Floor (row A / S21): a dirty tracked tree means unsaved or external edits.
    # Refuse — the caller saves or discards first. Untracked files (e.g.
    # .haute/state.json) don't block a checkout, so they're ignored here.
    ok_status, status = _run_git_ok("status", "--porcelain", "--untracked-files=no", cwd=cwd)
    if ok_status and status.strip():
        raise GitDomainError(
            "You have unsaved changes. Save or discard them before moving to another version."
        )

    target = _rev_parse(sha, cwd=cwd)
    if target is None:
        raise GitDomainError(f"No commit found for {sha!r}.")

    prior_branch = _get_current_branch(cwd)

    # Volatile artefacts (S12): wipe so a stale cache can't survive into the
    # moved-to tree. Best-effort and before the checkout — reconstructable.
    _wipe_volatile_artefacts(cwd or Path.cwd())

    # Detached checkout: materialise the target's tree as the working directory.
    _run_git("checkout", "--detach", target, cwd=cwd)

    # The clone now serves no working branch — the next save spawns one (S13).
    clear_working_branch(project_root)

    logger.info("moved_to_commit", sha=target, prior_branch=prior_branch)
    return GitMoveResponse(
        sha=target,
        short_sha=target[:8],
        prior_branch=prior_branch,
        is_detached=True,
    )


# ---------------------------------------------------------------------------
# Fork (P5d) — create a new working branch off the current one. The default
# forks at the latest milestone (NOT off HEAD, which lives on the ledger and
# would drag the raw saves in as fake milestones); branching at a pending save
# crystallizes it as an anchoring milestone. "Move" relocates the work after the
# fork point onto the new branch and rewinds the current one (S38).
# ---------------------------------------------------------------------------


def _on_first_parent(working: str, commit: str, cwd: Path | None = None) -> bool:
    """Whether *commit* sits on *working*'s first-parent (milestone) chain."""
    ok, raw = _run_git_ok("rev-list", "--first-parent", working, cwd=cwd)
    return ok and commit in raw.split()


def _commits_in_range(start: str, end: str, cwd: Path | None = None) -> list[str]:
    """SHAs in ``start..end``, oldest-first (ready to replay onto a new base)."""
    ok, raw = _run_git_ok("rev-list", "--reverse", f"{start}..{end}", cwd=cwd)
    return raw.split() if ok and raw.strip() else []


def _crystallize_milestone(working_tip: str, save: str, name: str, cwd: Path | None = None) -> str:
    """An anchoring milestone for a new branch forked at a pending *save*: a real
    merge commit (parents = latest milestone + the save) carrying the save's
    tree, so the new branch opens at a clean milestone capturing that state."""
    tree = _tree_of(save, cwd=cwd)
    msg = f"Start {name} from save {save[:8]}"
    return _run_git("commit-tree", tree, "-p", working_tip, "-p", save, "-m", msg, cwd=cwd)


def _replay_onto(base: str, commits: list[str], cwd: Path | None = None) -> str:
    """Replay each commit (its tree + message) onto *base* via plumbing, linear,
    returning the new tip. Trees apply cleanly because the commits already formed
    a linear chain whose root has *base*'s tree. Author + committer identity and
    dates are preserved (relocated saves keep their provenance + timeline, S38).
    Empty *commits* returns base."""
    tip = base
    for c in commits:
        tree = _tree_of(c, cwd=cwd)
        msg = _run_git("log", "-1", "--format=%B", c, cwd=cwd)
        # \x1f (unit separator) can't appear in identity/date fields.
        an, ae, ad, cn, ce, cd = _run_git(
            "log", "-1", "--format=%an%x1f%ae%x1f%aI%x1f%cn%x1f%ce%x1f%cI", c, cwd=cwd
        ).split("\x1f")
        env = {
            "GIT_AUTHOR_NAME": an,
            "GIT_AUTHOR_EMAIL": ae,
            "GIT_AUTHOR_DATE": ad,
            "GIT_COMMITTER_NAME": cn,
            "GIT_COMMITTER_EMAIL": ce,
            "GIT_COMMITTER_DATE": cd,
        }
        tip = _run_git("commit-tree", tree, "-p", tip, "-m", msg, cwd=cwd, env=env)
    return tip


def _rollback_fork(name: str, ledger: str, ledger_tip: str, cwd: Path | None = None) -> None:
    """Best-effort undo of a partially-applied fork so a mid-sequence git failure
    never leaves a half-forked, retry-blocked repo. Never raises: gets HEAD off
    the new ledger, restores the spawning ledger to its prior tip, and drops the
    new pair's refs."""
    new_ledger = ledger_name(name)
    if _get_current_branch(cwd) == new_ledger:
        # The spawning ledger isn't checked out → safe to restore it, then move
        # HEAD back so the new ledger can be deleted.
        _run_git_ok("branch", "-f", ledger, ledger_tip, cwd=cwd)
        _run_git_ok("checkout", ledger, cwd=cwd)
    _run_git_ok("branch", "-D", new_ledger, cwd=cwd)
    _run_git_ok("branch", "-D", name, cwd=cwd)


def create_working_branch(
    name: str,
    project_root: Path,
    at: str | None = None,
    move: bool = False,
    cwd: Path | None = None,
) -> GitCreateWorkingBranchResponse:
    """Create a new working branch as a fork of the current one (P5d/S38).

    Fork point: ``at=None`` → the current branch's latest milestone; ``at=<sha>``
    → that milestone, or a pending save (crystallized into an anchoring
    milestone). ``move=False`` (default) spins off a parallel line and leaves the
    current branch and your in-progress work untouched — you stay put.
    ``move=True`` relocates the work after the fork point (unmilestoned saves +
    uncommitted edits) onto the new branch, rewinds the current branch's ledger
    to the fork point, and switches you over. Move is valid only at the latest
    milestone or a pending save.
    """
    from haute._git_state import read_working_branch, set_fork, write_working_branch

    _assert_git_repo(cwd)
    _validate_ref_name(name)
    _assert_eligible_working(name)

    current = read_working_branch(project_root)
    if current is None:
        # No working branch yet — an adopt-create off real HEAD; no fork model.
        if at is not None or move:
            raise GitDomainError("No working branch to fork from yet.")
        res = set_working_branch(name, project_root, create=True, cwd=cwd)
        return GitCreateWorkingBranchResponse(
            working_branch=name,
            moved=False,
            switched=True,
            last_save_sha=res.last_save_sha,
        )

    if _rev_parse(name, cwd=cwd) is not None:
        raise GitDomainError(f"Branch '{name}' already exists.")
    if _rev_parse(ledger_name(name), cwd=cwd) is not None:
        raise GitDomainError(f"A branch named '{ledger_name(name)}' already exists.")

    working_tip = _rev_parse(current, cwd=cwd)
    if working_tip is None:
        raise GitDomainError(f"Working branch '{current}' does not exist.")
    ledger = ledger_name(current)
    ledger_tip = _rev_parse(ledger, cwd=cwd) or working_tip

    if at is not None:
        _validate_ref_name(at)  # same guard every other user-supplied ref gets
    point = working_tip if at is None else _rev_parse(at, cwd=cwd)
    if point is None:
        raise GitDomainError(f"Commit '{at}' does not exist.")

    is_milestone = _on_first_parent(current, point, cwd=cwd)
    is_pending = (
        not is_milestone
        and _is_ancestor(point, ledger_tip, cwd=cwd)
        and not _is_ancestor(point, working_tip, cwd=cwd)
    )
    if not is_milestone and not is_pending:
        raise GitDomainError(
            "You can only branch from a milestone or a pending save on the current branch."
        )

    base = point if is_milestone else _crystallize_milestone(working_tip, point, name, cwd=cwd)

    if not move:
        # Parallel fork: two fresh refs at the base; current and HEAD untouched.
        _run_git("branch", name, base, cwd=cwd)
        try:
            _run_git("branch", ledger_name(name), base, cwd=cwd)
        except GitError:
            _run_git_ok("branch", "-D", name, cwd=cwd)  # don't leak a lone ref
            raise
        set_fork(project_root, name, point)  # back-link the spawning commit
        logger.info("working_branch_forked", name=name, at=point[:8], moved=False)
        return GitCreateWorkingBranchResponse(
            working_branch=name,
            moved=False,
            switched=False,
            last_save_sha=_ledger_or_branch_sha(name, cwd=cwd),
        )

    # Move: only at the latest milestone or a pending save.
    if is_milestone and point != working_tip:
        raise GitDomainError(
            "Create & Move is only available at the latest milestone or a "
            "pending save — older milestones can only spin off a parallel line."
        )
    # M5 safety: move-mode rewinds the spawning branch's ledger to the fork
    # point (the ``branch -f`` below). If that ledger is already published, the
    # rewind drops commits the remote still has, leaving the source pair
    # un-pushable (non-fast-forward) — and S33 forbids the force-push that would
    # fix it. Refuse and steer to a parallel fork, which rewinds nothing. Only
    # refuse when the rewind genuinely orphans published commits (the remote
    # ledger is not an ancestor of the fork point); a tip-fork stays frictionless.
    for remote in _remote_names(cwd=cwd):
        remote_ledger = _rev_parse(f"refs/remotes/{remote}/{ledger}", cwd=cwd)
        if remote_ledger is not None and not _is_ancestor(remote_ledger, point, cwd=cwd):
            raise GitGuardrailError(
                "This branch's save history is published, and moving from here "
                "would rewind it past the shared copy. Spin off a parallel line "
                "instead — it leaves this branch untouched."
            )
    # The new ledger carries the saves after the fork point. At the latest
    # milestone the pending chain already sits on the base, so reuse it; at a
    # pending save, replay the later saves onto the crystallized milestone.
    if is_milestone:
        new_ledger_tip = ledger_tip
    else:
        new_ledger_tip = _replay_onto(base, _commits_in_range(point, ledger_tip, cwd=cwd), cwd=cwd)

    # The mutations below are not individually atomic; on any failure roll the
    # whole fork back so the user isn't wedged behind the "already exists" guard
    # with work duplicated across two lineages (S38).
    try:
        _run_git("branch", name, base, cwd=cwd)
        _run_git("branch", ledger_name(name), new_ledger_tip, cwd=cwd)
        # Switch onto the new ledger. The new ledger tip shares the old HEAD's
        # tree, so uncommitted edits carry across the checkout untouched.
        _run_git("checkout", ledger_name(name), cwd=cwd)
        # Rewind the spawning branch's ledger to the fork point (its later work
        # has been relocated; the commits stay reachable via the new ledger).
        _run_git("branch", "-f", ledger, point, cwd=cwd)
        write_working_branch(project_root, name)
    except (GitError, OSError):
        _rollback_fork(name, ledger, ledger_tip, cwd=cwd)
        raise
    set_fork(project_root, name, point)  # back-link the spawning commit
    logger.info("working_branch_forked", name=name, at=point[:8], moved=True)
    return GitCreateWorkingBranchResponse(
        working_branch=name,
        moved=True,
        switched=True,
        last_save_sha=_ledger_or_branch_sha(name, cwd=cwd),
    )


# ---------------------------------------------------------------------------
# Save & commit (P3) — milestone-merge the ledger onto the working branch, and
# read the working branch's milestone history (its first-parent chain).
# ---------------------------------------------------------------------------


def commit_milestone(
    message: str,
    project_root: Path,
    version_label: str | None = None,
    cwd: Path | None = None,
    allow_fork: bool = False,
) -> GitCommitResponse:
    """Promote the ledger's accumulated saves to a milestone on the working
    branch (a real `--no-ff`-shaped merge commit via plumbing), with the
    user's *message* and an optional version-label tag (S7/S18).

    Fork-gate (U4/D4): if the working branch is behind/diverged from its canonical
    remote on locally-known refs, a milestone would branch off the shared copy —
    refuse with :class:`GitMilestoneForkError` so the UI can warn, unless
    *allow_fork* is the user's deliberate override. The check is local-only (no
    fetch): the milestone stays instant and a local-only/offline user is never
    blocked (no remote, or an untracked/unknown leg, degrades open)."""
    from haute._git_state import read_working_branch

    _assert_git_repo(cwd)
    working = read_working_branch(project_root)
    if working is None:
        raise GitDomainError("No working branch is set for this project.")

    if not allow_fork:
        leg = divergence_state(working, cwd=cwd)
        if leg is not None and leg.status in ("behind", "diverged"):
            remote = _canonical_remote(cwd) or "the remote"
            behind = leg.behind or 0
            message_text = (
                f"Saving a milestone now will fork '{remote}' — it has {behind} newer "
                f"milestone{'' if behind == 1 else 's'} on this branch that you don't "
                "have yet, so your version would branch off the shared one instead of "
                "building on it. Your work is safe — commit anyway to create a fork, "
                "or catch up first."
            )
            raise GitMilestoneForkError(
                GitMilestoneFork(remote=remote, working=leg, message=message_text)
            )

    sha = merge_to_working(working, message, tag_label=version_label, cwd=cwd)
    logger.info("milestone_committed", working=working, sha=sha, tag=version_label or "")
    return GitCommitResponse(
        sha=sha,
        short_sha=sha[:8],
        working_branch=working,
        version_label=version_label,
    )


def working_milestones(
    project_root: Path,
    limit: int = 20,
    cwd: Path | None = None,
    branch: str | None = None,
) -> GitMilestonesResponse:
    """Milestone history (first-parent chain, newest first, with version-label
    tags). Defaults to the clone's working branch; pass *branch* to peek at
    another branch's history without switching to it.

    Shares the graph rail's cached windowed log (keyed by the resolved tip
    SHA): one ``git log`` per (tip, limit) ever, then a warm page costs the
    tip resolve plus the per-request batched tag read only. Version labels
    are applied after retrieval — tags move independently of tips."""
    _assert_git_repo(cwd)
    from haute._git_state import read_working_branch

    if branch is not None:
        _validate_ref_name(branch)
        working: str | None = branch
    else:
        working = read_working_branch(project_root)
    if working is None:
        return GitMilestonesResponse(working_branch=working, entries=[])
    tip = _rev_parse(working, cwd=cwd)
    if tip is None:
        return GitMilestonesResponse(working_branch=working, entries=[])

    # First-parent walk = the milestone spine (skips the ledger's per-save
    # commits, which hang off each merge's second parent).
    try:
        rows = _graph_log_cached(tip, limit, str(cwd) if cwd else "")
    except GitError:
        return GitMilestonesResponse(working_branch=working, entries=[])

    # ONE batched tag read for the whole page instead of a per-row
    # ``tag --points-at`` (the N+1 that dominated this endpoint).
    labels = _version_label_map(cwd=cwd)
    entries = [
        GitMilestoneEntry(
            sha=sha,
            short_sha=short_sha,
            message=message,
            timestamp=timestamp,
            version_label=labels.get(sha),
            # Truncation-aware root tagging for free from the row's %P: only a
            # genuinely parentless commit is flagged, and a first-parent walk
            # can only ever end (not pass through) one — so this fires for the
            # oldest entry of an untruncated page and never for a windowed cut.
            is_root=not parents,
        )
        for sha, short_sha, parents, timestamp, message in rows
    ]
    return GitMilestonesResponse(working_branch=working, entries=entries)


def _version_label_for(sha: str, cwd: Path | None = None) -> str | None:
    """The version label (a ``version/<label>`` tag) pointing at *sha*, if any."""
    ok, raw = _run_git_ok("tag", "--points-at", sha, "--list", "version/*", cwd=cwd)
    if ok and raw.strip():
        first = raw.strip().splitlines()[0]
        return first[len("version/") :] if first.startswith("version/") else first
    return None


def _commit_meta(sha: str, cwd: Path | None = None) -> tuple[str, str, str, str]:
    """(full sha, short sha, subject, ISO author date) for *sha*.

    Order is sha, short, subject, timestamp; the subject %s is the 3rd of 4
    tab-separated fields and the timestamp %aI (4th) never contains a tab, so a
    tab in the subject can't shift the columns when split with ``maxsplit=3``.
    Raises :class:`GitError` when git can't read the commit.
    """
    ok, raw = _run_git_ok("show", "-s", "--format=%H%x09%h%x09%s%x09%aI", sha, cwd=cwd)
    parts = raw.split("\t", 3)
    if not ok or len(parts) < 4:
        raise GitError(f"git show failed for {sha}")
    full, short_sha, message, timestamp = parts
    return full, short_sha, message, timestamp


def _is_root_commit(sha: str, cwd: Path | None = None) -> bool:
    """Whether *sha* is a root commit (no parents). The ``rev-list --parents``
    line is ``"<sha> <parent1> <parent2>..."`` — a root has no trailing shas."""
    ok, raw = _run_git_ok("rev-list", "--parents", "-n", "1", sha, cwd=cwd)
    return ok and len(raw.split()) <= 1


def _ledger_point(milestone_sha: str, cwd: Path | None = None) -> str:
    """A milestone's ledger fold-point — the last ledger commit it folded in, i.e.
    its SECOND parent. Milestone *merges* (working line) are never ancestors of
    the ledger's save commits, but their fold-point IS, so ancestry against the
    fold-point is what locates the latest milestone for a given save. A non-merge
    milestone (the root) has no second parent, so it is its own fold-point."""
    second = _rev_parse(f"{milestone_sha}^2", cwd=cwd)
    return second if second is not None else milestone_sha


def commit_context(
    project_root: Path, sha: str, cwd: Path | None = None, base: str | None = None
) -> GitCommitContext:
    """A commit's "breadcrumb context" for the version-compare UI: the LATEST
    milestone at the commit and the distance (commit count) from that milestone's
    ledger fold-point to the commit. A milestone is its own anchor (distance 0).
    The latest milestone is found by ledger fold-point ancestry — a save folded
    after milestone M but before M+1 anchors on M, and a pending save after the tip
    milestone anchors on the tip — not on the repo root. When ``base`` is given,
    also reports ``delta_from_base`` = the commit count ``base..sha`` (the
    historic↔current span). Pure read — no checkout, no HEAD change."""
    _assert_git_repo(cwd)
    _validate_ref_name(sha)
    resolved = _rev_parse(sha, cwd=cwd)
    if resolved is None:
        raise GitDomainError(f"Unknown commit: {sha}")

    full, short_sha, message, timestamp = _commit_meta(resolved, cwd=cwd)
    is_root = _is_root_commit(resolved, cwd=cwd)

    milestones = working_milestones(project_root, cwd=cwd).entries
    milestone_shas = {m.sha for m in milestones}
    is_milestone = full in milestone_shas
    version_label = _version_label_for(full, cwd=cwd)

    nearest: GitCommitRef
    distance: int
    if is_milestone:
        entry = next(m for m in milestones if m.sha == full)
        nearest = GitCommitRef(
            sha=entry.sha,
            short_sha=entry.short_sha,
            message=entry.message,
            version_label=entry.version_label,
            is_root=is_root,
        )
        distance = 0
    elif is_root:
        nearest = GitCommitRef(
            sha=full,
            short_sha=short_sha,
            message=message,
            version_label=version_label,
            is_root=True,
        )
        distance = 0
    else:
        # Walk milestones newest-first; the latest one whose ledger fold-point is
        # an ancestor of this save is the milestone the save sits under (a save
        # folded by a later milestone fails the check — its fold-point is a
        # descendant of the save — so we land on the previous milestone, or the
        # tip for a pending save). Distance is counted from that fold-point.
        latest: GitMilestoneEntry | None = None
        anchor: str | None = None
        for m in milestones:
            if m.sha == full:
                continue
            point = _ledger_point(m.sha, cwd=cwd)
            if point != full and _is_ancestor(point, full, cwd=cwd):
                latest = m
                anchor = point
                break
        if latest is not None and anchor is not None:
            nearest = GitCommitRef(
                sha=latest.sha,
                short_sha=latest.short_sha,
                message=latest.message,
                version_label=latest.version_label,
                is_root=_is_root_commit(latest.sha, cwd=cwd),
            )
        else:
            # No milestone fold-point ancestor — anchor on the repo's root commit.
            ok_root, root_raw = _run_git_ok("rev-list", "--max-parents=0", resolved, cwd=cwd)
            if not ok_root or not root_raw.strip():
                raise GitError(f"could not find root commit for {sha}")
            root_sha = root_raw.splitlines()[0]
            r_full, r_short, r_msg, _r_ts = _commit_meta(root_sha, cwd=cwd)
            nearest = GitCommitRef(
                sha=r_full,
                short_sha=r_short,
                message=r_msg,
                version_label=_version_label_for(r_full, cwd=cwd),
                is_root=True,
            )
            anchor = r_full
        ok_count, count_raw = _run_git_ok("rev-list", "--count", f"{anchor}..{full}", cwd=cwd)
        if not ok_count:
            raise GitError(f"git rev-list --count failed for {anchor}..{full}")
        distance = int(count_raw.strip())

    # Optional historic↔current delta: commits between a caller-supplied base and
    # this commit (rev-list --count base..self). Used by the compare UI to show how
    # far the current pipeline has moved past the inspected version. Robust across
    # milestone merges (base..head counts only what head reaches that base doesn't).
    delta_from_base: int | None = None
    if base is not None:
        _validate_ref_name(base)
        base_resolved = _rev_parse(base, cwd=cwd)
        if base_resolved is None:
            raise GitDomainError(f"Unknown commit: {base}")
        ok_delta, delta_raw = _run_git_ok(
            "rev-list", "--count", f"{base_resolved}..{full}", cwd=cwd
        )
        if not ok_delta:
            raise GitError(f"git rev-list --count failed for {base_resolved}..{full}")
        delta_from_base = int(delta_raw.strip())

    return GitCommitContext(
        sha=full,
        short_sha=short_sha,
        message=message,
        timestamp=timestamp,
        is_root=is_root,
        is_milestone=is_milestone,
        version_label=version_label,
        nearest_milestone=nearest,
        distance=distance,
        delta_from_base=delta_from_base,
    )


# ---------------------------------------------------------------------------
# Ledger expansion (P5) — the per-save commits a milestone folded in, and the
# pending saves on the ledger ahead of the working tip. Rename-aware (`-M`), so
# the view shows a renamed config as one rename, not delete+add (closes the P4
# read-path deferral).
# ---------------------------------------------------------------------------

# ASCII record separator — will not appear in commit metadata, so it safely
# delimits per-commit blocks in the `git log` output.
_SAVE_RECORD_SEP = "\x1e"


def _parse_ledger_saves(range_spec: str, cwd: Path | None = None) -> list[GitLedgerSave]:
    """Parse ``git log -M --name-status`` over *range_spec* into save records.

    Order in the format is sha, short, timestamp, **message last** so a tab in
    the subject can't shift the columns (the name-status lines below use git's
    own tab separators).
    """
    # core.quotepath=false: git otherwise octal-escapes + quotes non-ASCII paths
    # (e.g. a unicode config filename), which would surface as a mangled path in
    # the history view. haute-owned paths never contain spaces/tabs/newlines
    # (sanitized identifiers), which git would still quote regardless.
    ok, raw = _run_git_ok(
        "-c",
        "core.quotepath=false",
        "log",
        "-M",
        "--name-status",
        f"--format={_SAVE_RECORD_SEP}%H%x09%h%x09%aI%x09%s",
        range_spec,
        cwd=cwd,
    )
    if not ok or not raw:
        return []

    saves: list[GitLedgerSave] = []
    for block in raw.split(_SAVE_RECORD_SEP):
        block = block.strip("\n")
        if not block:
            continue
        lines = block.split("\n")
        header = lines[0].split("\t", 3)
        if len(header) < 4:
            continue
        sha, short_sha, timestamp, message = header

        files: list[GitFileChange] = []
        for line in lines[1:]:
            if not line.strip():
                continue
            cols = line.split("\t")
            code = cols[0]
            letter = code[0] if code else "?"
            if letter in ("R", "C") and len(cols) >= 3:
                files.append(GitFileChange(status=letter, path=cols[2], old_path=cols[1]))
            elif len(cols) >= 2:
                files.append(GitFileChange(status=letter, path=cols[1]))
        saves.append(
            GitLedgerSave(
                sha=sha,
                short_sha=short_sha,
                message=message,
                timestamp=timestamp,
                files=files,
            )
        )
    return saves


def milestone_saves(milestone_sha: str, cwd: Path | None = None) -> GitLedgerSavesResponse:
    """The ledger saves folded into a milestone — the commits on its second
    parent that its first parent doesn't have (``M^1..M^2``), newest first.

    A non-merge commit on the spine (e.g. the pre-spawn root) folds in nothing.
    """
    _assert_git_repo(cwd)
    _validate_ref_name(milestone_sha)

    # Resolve to a single commit first. _validate_ref_name does not block "..",
    # so a range-shaped value ("a..b") would otherwise reach rev-list as a range;
    # rev-parse --verify <sha>^{commit} rejects anything that is not one commit.
    resolved = _rev_parse(milestone_sha, cwd=cwd)
    if resolved is None:
        raise GitDomainError(f"Commit '{milestone_sha}' not found.")

    ok, parents = _run_git_ok("rev-list", "--parents", "-n", "1", resolved, cwd=cwd)
    if not ok or not parents.strip():
        raise GitDomainError(f"Commit '{milestone_sha}' not found.")
    parent_shas = parents.split()[1:]
    if len(parent_shas) < 2:
        return GitLedgerSavesResponse(saves=[])

    first_parent, second_parent = parent_shas[0], parent_shas[1]
    return GitLedgerSavesResponse(
        saves=_parse_ledger_saves(f"{first_parent}..{second_parent}", cwd=cwd)
    )


def pending_ledger_saves(
    project_root: Path, cwd: Path | None = None, branch: str | None = None
) -> GitLedgerSavesResponse:
    """The saves on a branch's ledger ahead of its tip (``branch..branch-save``):
    what the next save & commit would fold into a milestone. Defaults to the
    clone's working branch; pass *branch* to peek at another. Empty when no
    branch resolves, the ledger is unspawned, or nothing is pending."""
    from haute._git_state import read_working_branch

    _assert_git_repo(cwd)
    if branch is not None:
        _validate_ref_name(branch)
        working: str | None = branch
    else:
        working = read_working_branch(project_root)
    if working is None:
        return GitLedgerSavesResponse(saves=[])
    ledger = ledger_name(working)
    if _rev_parse(working, cwd=cwd) is None or _rev_parse(ledger, cwd=cwd) is None:
        return GitLedgerSavesResponse(saves=[])
    return GitLedgerSavesResponse(saves=_parse_ledger_saves(f"{working}..{ledger}", cwd=cwd))


# ---------------------------------------------------------------------------
# Graph topology — the whole working-branch forest for the panel's graph rail:
# every pair's first-parent spine plus fork attachments computed from git
# ancestry (claim-based over full spines), never from the clone-local
# forks.json (which is lossy: entries from other clones are simply absent).
# ---------------------------------------------------------------------------


def _version_label_map(cwd: Path | None = None) -> dict[str, str]:
    """sha → version label for every ``version/<label>`` tag, in ONE batched
    ``for-each-ref`` call (vs. the per-commit ``tag --points-at`` the milestones
    view issues). Reads the peeled ``%(*objectname)`` — version tags are
    annotated — falling back to ``%(objectname)`` for a lightweight tag. First
    label per commit wins (refname order), matching ``_version_label_for``'s
    first-line semantics."""
    ok, raw = _run_git_ok(
        "for-each-ref",
        "refs/tags/version/",
        "--format=%(*objectname) %(objectname) %(refname:short)",
        cwd=cwd,
    )
    labels: dict[str, str] = {}
    if not ok or not raw.strip():
        return labels
    for line in raw.splitlines():
        parts = line.split(" ", 2)
        if len(parts) < 3:
            continue
        peeled, plain, refname = parts
        sha = peeled or plain
        label = refname[len("version/") :] if refname.startswith("version/") else refname
        if sha and sha not in labels:
            labels[sha] = label
    return labels


@lru_cache(maxsize=1024)
def _commit_parents_cached(sha: str, cwd_key: str) -> tuple[str, ...]:
    """Cached inner — a commit's parent list is content-addressed; an empty
    tuple (root commit) is a valid cached answer, an unreadable object raises
    and is never memoised."""
    ok, raw = _run_git_ok("show", "-s", "--format=%P", sha, cwd=Path(cwd_key) if cwd_key else None)
    if not ok:
        raise GitError(f"git show failed for {sha}")
    return tuple(raw.split())


def _commit_parents(sha: str, cwd: Path | None = None) -> list[str]:
    """All parent SHAs of one commit (``%P``), ``[]`` when unreadable."""
    if _is_full_sha(sha):
        try:
            return list(_commit_parents_cached(sha, str(cwd) if cwd else ""))
        except GitError:
            return []
    ok, raw = _run_git_ok("show", "-s", "--format=%P", sha, cwd=cwd)
    return raw.split() if ok else []


def _fork_source_and_credit(
    spine: list[str],
    fork_point_sha: str,
    parent_spine: list[str],
    parent_ledger_tip: str | None,
    cwd: Path | None = None,
) -> tuple[str | None, str | None]:
    """The spawn-source save and its crediting milestone for one forked branch.

    A fork created AT A SAVE gets an auto "anchoring" merge as its oldest own
    commit X, whose parents are ``[spawning spine tip, the save]`` — that
    second parent is the commit the user actually forked from. But an
    ORDINARY milestone-level fork's oldest own commit is just its first
    milestone, whose second parent is the fork's OWN ledger save. Ancestry
    tells the two apart: a crystallized fork's source save lives in the
    PARENT pair's history (folded into a later parent milestone, or still
    pending on the parent's ledger), while a fork's own fold never does.

    Returns ``(fork_source, fork_credit)``:

    * ``fork_source`` — X's second parent, when X is a merge AND that commit
      is reachable from the parent's working tip or its ledger tip (the
      ledger may not exist — treated as not-ancestor); else None.
    * ``fork_credit`` — computed only when ``fork_source`` is set: the OLDEST
      parent-spine commit ABOVE the fork point that contains the source save,
      i.e. the milestone whose fold swallowed it — the row that should
      visually take credit for the spawn while its fold is collapsed. Found
      by binary search (containment along a first-parent spine is monotone).
      None when the save is still pending (reachable only via the parent's
      ledger, folded into no parent milestone yet).

    Both spines are the full first-parent chains graph_topology already holds
    in memory, newest first; only the is-ancestor checks (and one ``%P`` read
    for X) hit git — both SHA-keyed-cached, so a repeat is free.
    """
    idx = spine.index(fork_point_sha)
    if idx == 0:
        return (None, None)  # no own commits — the branch sits AT the fork point
    anchor = spine[idx - 1]  # X: the fork's oldest own spine commit
    parents = _commit_parents(anchor, cwd=cwd)
    if len(parents) < 2:
        return (None, None)  # plain commit — nothing was folded at the spawn
    source = parents[1]
    in_parent_history = _is_ancestor(source, parent_spine[0], cwd=cwd) or (
        parent_ledger_tip is not None and _is_ancestor(source, parent_ledger_tip, cwd=cwd)
    )
    if not in_parent_history:
        return (None, None)  # the anchoring second parent is the fork's own save
    # The parent spine is newest-first, so everything above the fork point is
    # the prefix; the EARLIEST (oldest) fold containing the save is the credit.
    # Containment along a first-parent spine is monotone — each newer spine
    # commit's ancestor set is a superset of its elder's — so contains(source)
    # is True on a newest-first prefix of the candidates and the boundary is
    # binary-searchable: O(log n) is-ancestor probes instead of the old
    # oldest-first linear scan's O(n). The newest-candidate probe repeats the
    # in_parent_history check above, so it costs nothing (SHA-keyed cache).
    parent_idx = parent_spine.index(fork_point_sha)
    candidates = parent_spine[:parent_idx]
    if not candidates or not _is_ancestor(source, candidates[0], cwd=cwd):
        return (source, None)  # still pending on the parent's ledger
    lo, hi = 0, len(candidates) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _is_ancestor(source, candidates[mid], cwd=cwd):
            lo = mid
        else:
            hi = mid - 1
    return (source, candidates[lo])


# One parsed windowed-log row: (sha, short sha, parents, timestamp, message).
_GraphLogRow = tuple[str, str, tuple[str, ...], str, str]


def _graph_log_rows(tip: str, limit: int, cwd: Path | None = None) -> tuple[_GraphLogRow, ...]:
    """Uncached windowed first-parent log below *tip*, parsed. The subject %s
    sits LAST in the format so a tab in it can't shift the fixed columns.
    Raises :class:`GitError` when the ref/object is unreadable (so the cached
    wrapper never memoises a failure)."""
    ok, raw = _run_git_ok(
        "log",
        "--first-parent",
        f"--max-count={limit}",
        "--format=%H%x09%h%x09%P%x09%aI%x09%s",
        tip,
        "--",
        cwd=cwd,
    )
    if not ok:
        raise GitError(f"git log failed for {tip}")
    rows: list[_GraphLogRow] = []
    for line in raw.splitlines():
        parts = line.split("\t", 4)
        if len(parts) < 5:
            continue
        sha, short_sha, parents_raw, timestamp, message = parts
        rows.append((sha, short_sha, tuple(parents_raw.split()), timestamp, message))
    return tuple(rows)


@lru_cache(maxsize=256)
def _graph_log_cached(tip_sha: str, limit: int, cwd_key: str) -> tuple[_GraphLogRow, ...]:
    """Cached inner — the window below an immutable tip SHA. Deliberately
    stores the PARSED rows, not GitGraphEntry objects: version labels come
    from tags (mutable) and must be applied per-request after retrieval."""
    return _graph_log_rows(tip_sha, limit, cwd=Path(cwd_key) if cwd_key else None)


def _graph_entries(
    tip: str, limit: int, labels: dict[str, str], cwd: Path | None = None
) -> list[GitGraphEntry]:
    """The newest *limit* spine entries below *tip* (a resolved tip SHA on the
    graph path — cached per (tip, limit, cwd)), with parents and version labels
    (from the batched per-request *labels* map, applied AFTER cache retrieval —
    tags move independently of tips). Root tagging is read off %P — empty
    parents IS the root commit, window-truncated or not — so no per-branch
    rev-list probe."""
    try:
        if _is_full_sha(tip):
            rows = _graph_log_cached(tip, limit, str(cwd) if cwd else "")
        else:
            rows = _graph_log_rows(tip, limit, cwd=cwd)
    except GitError:
        return []
    return [
        GitGraphEntry(
            sha=sha,
            short_sha=short_sha,
            message=message,
            timestamp=timestamp,
            version_label=labels.get(sha),
            parents=list(parents),
            is_root=not parents,
        )
        for sha, short_sha, parents, timestamp, message in rows
    ]


def graph_topology(
    project_root: Path, cwd: Path | None = None, limit: int = 50
) -> GitGraphResponse:
    """The working-branch forest for the graph rail.

    Per pair: the newest-first first-parent spine (windowed to *limit*) and its
    fork attachment, computed from FULL spines by deterministic claiming — the
    CURRENT working branch claims first (its own spine must never be claimed by
    a deeper fork), then the rest deepest-spine-first (then name); each branch's
    ``fork_point_sha`` is its newest spine commit already claimed by an
    earlier-processed branch, ``fork_of`` that branch's name, and it claims
    everything above. Forked branches additionally carry ``fork_source_sha``
    / ``fork_credit_sha`` — the save the branch was actually spawned from
    (when it differs from the fork-point milestone) and the parent milestone
    whose fold contains that save (see _fork_source_and_credit). A branch
    sharing no claimed commit roots its own tree
    (both null) — the fork FOREST is real, since the root commit lives on the
    default branch, which is not a working pair. Archived pairs are included
    (the client filters); ``forked_from`` passes the clone-local forks.json
    back-link through for API completeness (the fork chips read
    /api/git/working-branches) and plays no part in the topology.
    Pure read — no checkout, no HEAD movement, no ref or state writes."""
    from haute._git_state import read_forks, read_working_branch

    _assert_git_repo(cwd)
    working = read_working_branch(project_root)
    default = _get_default_branch(cwd)
    forks = read_forks(project_root)

    # Same enumeration as the branch manager (working pairs only, ledgers
    # implicit, the deploy branch excluded) — but archived pairs stay in.
    # Refs resolve to tip SHAs once, in the for-each-ref itself; each spine is
    # then a content-addressed read below its tip (cached, so an unmoved
    # branch costs no rev-list on refresh). The enumeration carries only names,
    # tips and commit times — no per-branch ahead-behind walk.
    listing, tips = _list_branches_with_tips(cwd=cwd)
    spines: dict[str, list[str]] = {}
    archived: dict[str, bool] = {}
    for b in listing.branches:
        if branch_category(b.name) != "working" or b.name == default:
            continue
        tip = tips.get(b.name)
        spine = _first_parent_spine(tip, cwd=cwd) if tip is not None else None
        if spine is None:
            continue  # unreadable ref — nothing to draw for it
        spines[b.name] = spine
        archived[b.name] = b.is_archived

    # Deterministic processing order: the CURRENT working branch first — a
    # crystallized fork sits at spawning spine + 1 until the branch advances,
    # and depth-first claiming would hand it the user's own spine — then
    # deepest spine, then name. The first-processed branch of each component
    # roots its fork tree; two forks off one commit tie-break by name.
    # (working may be None or not a listed pair — the key degrades cleanly.)
    order = sorted(spines, key=lambda name: (name != working, -len(spines[name]), name))

    claimed: dict[str, str] = {}
    attachments: dict[str, tuple[str | None, str | None]] = {}
    for name in order:
        spine = spines[name]
        cut = len(spine)
        attachment: tuple[str | None, str | None] = (None, None)
        for i, sha in enumerate(spine):
            owner = claimed.get(sha)
            if owner is not None:
                cut = i
                attachment = (sha, owner)
                break
        for sha in spine[:cut]:
            claimed[sha] = name
        attachments[name] = attachment

    labels = _version_label_map(cwd=cwd)
    branches: list[GitGraphBranch] = []
    for name in order:
        spine = spines[name]
        fork_point_sha, fork_of = attachments[name]
        fork_source_sha: str | None = None
        fork_credit_sha: str | None = None
        if fork_point_sha is not None and fork_of is not None:
            # Parent ledger tips come from the same for-each-ref enumeration
            # (ledgers are local heads too) — no per-fork rev-parse.
            fork_source_sha, fork_credit_sha = _fork_source_and_credit(
                spine, fork_point_sha, spines[fork_of], tips.get(ledger_name(fork_of)), cwd=cwd
            )
        # forks.json passthrough, with the same reachability guard the branch
        # manager applies (a stale entry is dropped, never surfaced dangling).
        fork = forks.get(name)
        forked_from = fork if fork and _rev_parse(fork, cwd=cwd) is not None else None
        branches.append(
            GitGraphBranch(
                name=name,
                is_archived=archived[name],
                is_current=name == working,
                tip_sha=spine[0],
                fork_point_sha=fork_point_sha,
                fork_of=fork_of,
                fork_source_sha=fork_source_sha,
                fork_credit_sha=fork_credit_sha,
                forked_from=forked_from,
                truncated=len(spine) > limit,
                entries=_graph_entries(spine[0], limit, labels, cwd=cwd),
            )
        )
    return GitGraphResponse(working_branch=working, order=order, branches=branches)


# ---------------------------------------------------------------------------
# Read-only history view (S11) — materialise a commit's tree WITHOUT a checkout
# so its pipeline can be parsed and rendered read-only (view ≠ move): no HEAD
# change, no working-tree mutation, any number of visits.
# ---------------------------------------------------------------------------


def archive_commit(sha: str, dest: Path, cwd: Path | None = None) -> None:
    """Extract the whole tree of *sha* into *dest* via ``git archive`` — a pure
    read of object storage that never touches HEAD, the index, or the working
    tree (S11). *dest* must already exist. Used to parse a commit's pipeline
    (with its config + submodel files) for a read-only view."""
    _assert_git_repo(cwd)
    _validate_ref_name(sha)
    if _rev_parse(sha, cwd=cwd) is None:
        raise GitDomainError(f"No commit found for '{sha}'.")
    proc = subprocess.run(
        ["git", "archive", "--format=tar", sha],
        cwd=cwd or Path.cwd(),
        capture_output=True,
    )
    if proc.returncode != 0:
        stderr = proc.stderr.decode(errors="replace").strip()
        logger.warning("git_archive_failed", sha=sha, stderr=stderr)
        raise GitError(stderr or "git archive failed")
    # The archive is git-produced from our own repo (repo-relative paths); the
    # data filter is belt-and-braces against absolute/traversal members.
    with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as tar:
        tar.extractall(dest, filter="data")


# ---------------------------------------------------------------------------
# Remotes and deliberate push (S16/S33) — no auto-push, no add-remote from the
# UI, no force-push ever. Push the working/ledger PAIR atomically to an EXISTING
# remote. ahead/behind are read from locally-known remote refs only (no fetch,
# so no egress; fetch cadence is a later deliberate surface, P7/D10).
# ---------------------------------------------------------------------------


def _remote_names(cwd: Path | None = None) -> list[str]:
    """Names of the configured remotes (empty when fully offline)."""
    ok, out = _run_git_ok("remote", cwd=cwd)
    return out.splitlines() if ok and out.strip() else []


def _leg_state(branch: str, remote: str, cwd: Path | None = None) -> GitRemoteLeg:
    """Divergence of one local *branch* vs ``<remote>/<branch>`` from the
    locally-known remote-tracking ref only — no fetch (callers freshen via
    :func:`fetch_pair`). Distinguishes (F2) "untracked" (never pushed to this
    remote, or the branch doesn't exist locally yet — e.g. a ledger not spawned)
    from "unknown" (the count couldn't be read) from the measured states, so the
    UI never renders "can't tell" as "in sync"."""
    tracking = f"refs/remotes/{remote}/{branch}"
    if _rev_parse(branch, cwd=cwd) is None or _rev_parse(tracking, cwd=cwd) is None:
        return GitRemoteLeg(status="untracked")
    ok, out = _run_git_ok("rev-list", "--left-right", "--count", f"{tracking}...{branch}", cwd=cwd)
    parts = out.split()
    if not ok or len(parts) != 2:
        return GitRemoteLeg(status="unknown")
    try:
        behind, ahead = int(parts[0]), int(parts[1])  # left=remote-only, right=local-only
    except ValueError:
        return GitRemoteLeg(status="unknown")
    if ahead and behind:
        return GitRemoteLeg(status="diverged", ahead=ahead, behind=behind)
    if ahead:
        return GitRemoteLeg(status="ahead", ahead=ahead, behind=behind)
    if behind:
        return GitRemoteLeg(status="behind", ahead=ahead, behind=behind)
    return GitRemoteLeg(status="synced", ahead=ahead, behind=behind)


def _ahead_behind(
    working: str, remote: str, cwd: Path | None = None
) -> tuple[int | None, int | None]:
    """(ahead, behind) of *working* vs ``<remote>/<working>`` — the working leg's
    counts, kept for back-compat. See :func:`_leg_state` for the structured
    per-leg state (including the ledger leg)."""
    leg = _leg_state(working, remote, cwd=cwd)
    return leg.ahead, leg.behind


def fetch_pair(remote: str, working: str, cwd: Path | None = None) -> bool:
    """Refresh the working pair's remote-tracking refs (oW + oL) so divergence
    detection reads fresh data (F5). Demand-driven and throttled per
    ``(cwd, remote, "pair")`` — independently of the deploy-branch peek (F7) —
    and hardened so a slow / auth-walled remote can't hang the caller (F1).
    Returns whether a fetch actually ran (``False`` when throttled). Any failure
    degrades silently to the last-known tracking refs."""
    if not _should_fetch(remote, cwd=cwd, kind="pair"):
        return False
    with _fetch_exec_lock:
        _fetch_refs(remote, working, ledger_name(working), cwd=cwd)
    return True


def divergence_state(working: str, cwd: Path | None = None) -> GitRemoteLeg | None:
    """The working branch's divergence vs the canonical remote, from LOCAL refs
    only — no fetch (U4). This is the single predicate the save&commit fork-gate
    and the passive badge share, so a milestone can never be blocked by a state
    the badge doesn't also show. Returns ``None`` when no canonical remote
    resolves (nothing to diverge from — the gate then degrades open)."""
    remote = _canonical_remote(cwd)
    if remote is None:
        return None
    return _leg_state(working, remote, cwd=cwd)


def _redact_remote_url(url: str) -> str:
    """Strip any ``user:password@`` userinfo from a URL-style remote before it
    crosses the API boundary. A token-in-URL (``https://x-access-token:ghp_…@``)
    is a common CI/clone pattern, and the module's threat model bars remote URLs
    and credentials from reaching the client. scp-style ``git@host:path`` has no
    password component and is left untouched."""
    if "://" not in url:
        return url  # scp-style or a local path — no userinfo to leak
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(url)
    if "@" not in parts.netloc:
        return url
    host = parts.netloc.rsplit("@", 1)[1]
    return urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))


def list_remotes(project_root: Path, cwd: Path | None = None) -> GitRemotesResponse:
    """Existing remotes for the push dropdown and the passive behind-remote
    surface, each annotated with the working branch's AND its ledger's divergence
    vs that remote (F6). A throttled, hardened pair fetch (F5) freshens the
    tracking refs first; the counts themselves read local refs only."""
    from haute._git_state import read_working_branch

    _assert_git_repo(cwd)
    working = read_working_branch(project_root)
    remotes: list[GitRemote] = []
    for name in _remote_names(cwd):
        ok_url, url = _run_git_ok("remote", "get-url", name, cwd=cwd)
        working_leg: GitRemoteLeg | None = None
        ledger_leg: GitRemoteLeg | None = None
        if working:
            fetch_pair(name, working, cwd=cwd)  # F5: freshen oW + oL (throttled)
            working_leg = _leg_state(working, name, cwd=cwd)
            ledger_leg = _leg_state(ledger_name(working), name, cwd=cwd)
        remotes.append(
            GitRemote(
                name=name,
                url=_redact_remote_url(url) if ok_url and url.strip() else None,
                ahead=working_leg.ahead if working_leg else None,
                behind=working_leg.behind if working_leg else None,
                working=working_leg,
                ledger=ledger_leg,
            )
        )
    return GitRemotesResponse(remotes=remotes, working_branch=working)


def _is_rewrite(remote: str, branch: str, project_root: Path, cwd: Path | None = None) -> bool:
    """Whether *remote*'s *branch* was REWRITTEN since this clone last pushed it
    (X3): the recorded last-pushed SHA is no longer an ancestor of the remote tip,
    so a commit we published was dropped (a rebase/force-push upstream) rather than
    the remote simply advancing. Unknown (never recorded / unreadable tip) → False
    so it degrades to ordinary divergence."""
    from haute._git_state import read_pushed_shas

    recorded = read_pushed_shas(project_root).get(f"{remote}/{branch}")
    if recorded is None:
        return False
    remote_tip = _rev_parse(f"refs/remotes/{remote}/{branch}", cwd=cwd)
    if remote_tip is None or recorded == remote_tip:
        return False
    return not _is_ancestor(recorded, remote_tip, cwd=cwd)


def _push_rejection(
    remote: str, working: str, ledger: str, project_root: Path, cwd: Path | None = None
) -> GitPushRejectedError:
    """Build the data-bearing non-FF push rejection (M7/M6, X3).

    Fetch the pair once — *forced* past the demand throttle, because a rejection
    is authoritative, not a poll — then recompute both legs so the payload shows
    the live fork. ``--atomic`` means a fast-forwardable leg is rejected
    alongside a non-FF one, so the message names the **blocking** leg(s) (the ones
    the remote has moved ahead on), reconciling with the per-leg counts rather
    than blaming whichever ref git happened to print (M6). When the remote dropped
    a commit we published (X3), the message says so distinctly and points at the
    person-reconciles off-ramp. A failed fetch degrades to the last-known tracking
    refs — still honest, never a hang (F1)."""
    with _fetch_exec_lock:
        _fetch_refs(remote, working, ledger, cwd=cwd)
    working_leg = _leg_state(working, remote, cwd=cwd)
    ledger_leg = (
        _leg_state(ledger, remote, cwd=cwd) if _rev_parse(ledger, cwd=cwd) is not None else None
    )
    is_rewrite = _is_rewrite(remote, working, project_root, cwd=cwd) or (
        ledger_leg is not None and _is_rewrite(remote, ledger, project_root, cwd=cwd)
    )
    if is_rewrite:
        message = (
            f"The history on '{remote}' was rewritten — a version you had published "
            "is no longer there. haute never force-pushes, so your local work is "
            "safe; a person needs to reconcile this. Spin off a copy to keep yours."
        )
    else:
        blocked: list[str] = []
        if working_leg.status in ("behind", "diverged"):
            blocked.append("working branch")
        if ledger_leg is not None and ledger_leg.status in ("behind", "diverged"):
            blocked.append("save history")
        which = " and ".join(blocked) if blocked else "shared copy"
        message = (
            f"The {which} on '{remote}' changed since you last synced, so this push "
            "would overwrite remote work. haute never force-pushes — your local work "
            "is safe; reconcile by spinning off a copy or catching up first."
        )
    return GitPushRejectedError(
        GitPushRejection(
            remote=remote,
            working=working_leg,
            ledger=ledger_leg,
            message=message,
            is_rewrite=is_rewrite,
        )
    )


def _ls_remote_version_tags(remote: str, cwd: Path | None = None) -> dict[str, str]:
    """``{tag_name: commit_sha}`` for ``version/*`` tags on *remote* — prompt-proof
    and time-bounded (F1). Empty on any failure: the caller treats "can't tell" as
    no pre-check and lets git's own tag rejection backstop a real collision.

    The sha captured is the underlying COMMIT each tag points to, not the
    annotated-tag object sha: ``git ls-remote --tags`` emits both
    ``refs/tags/version/X <objsha>`` and the peeled ``refs/tags/version/X^{} <commitsha>``,
    and we prefer the peeled commit sha. This matches the local commit sha from
    :func:`_rev_parse` (which appends ``^{commit}``) so a collision is judged on
    the release commit, not the tag object — annotated tags have ``objsha !=
    commitsha`` even when pointing at the same commit, which would otherwise
    false-positive on every idempotent re-push of an already-published label."""
    env = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_SSH_COMMAND": "ssh -oBatchMode=yes -oConnectTimeout=10",
    }
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--tags", remote, "refs/tags/version/*"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=cwd or Path.cwd(),
            env=env,
            timeout=_FETCH_TIMEOUT_SECONDS,
        )
    except (subprocess.TimeoutExpired, OSError):
        return {}
    if result.returncode != 0:
        return {}
    out: dict[str, str] = {}
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 2:
            continue
        sha, ref = parts
        if not ref.startswith("refs/tags/"):
            continue
        # Prefer the peeled commit sha (``refs/tags/version/X^{}``) so the map is
        # keyed to the underlying release commit. A lightweight tag has no peeled
        # line, so its object line already IS the commit; an annotated tag's
        # peeled line overrides the earlier object line.
        peeled = ref.endswith("^{}")
        name = ref[len("refs/tags/") : -3] if peeled else ref[len("refs/tags/") :]
        if peeled or name not in out:
            out[name] = sha
    return out


def _tag_collisions(remote: str, working: str, cwd: Path | None = None) -> list[str]:
    """``version/<label>`` tags reachable from *working* that already exist on
    *remote* at a DIFFERENT release COMMIT — a label name reused for another
    release (X4 / decision A: one canonical label per release). The reachable set
    mirrors what ``--follow-tags`` would push.

    Both sides are compared as the COMMIT each tag resolves to: ``_rev_parse``
    peels the local tag to its commit and :func:`_ls_remote_version_tags`
    captures the remote's peeled commit sha. A label already on the remote at the
    SAME commit (an idempotent re-push of a published release) is therefore NOT a
    collision — only a genuine name-reuse at a different commit is."""
    ok, raw = _run_git_ok("tag", "--merged", working, "--list", "version/*", cwd=cwd)
    local_tags = [t for t in raw.splitlines() if t.strip()] if ok else []
    if not local_tags:
        return []
    remote_tags = _ls_remote_version_tags(remote, cwd=cwd)
    collisions: list[str] = []
    for tag in local_tags:
        local_sha = _rev_parse(f"refs/tags/{tag}", cwd=cwd)
        remote_sha = remote_tags.get(tag)
        if remote_sha is not None and local_sha is not None and remote_sha != local_sha:
            collisions.append(tag)
    return collisions


def push_working_pair(remote: str, project_root: Path, cwd: Path | None = None) -> GitPushResponse:
    """Deliberately push the working branch AND its ledger to *remote*, atomically
    (S16): both refs land or neither does. NEVER force-pushes (S33). Pushes only
    to a remote that already exists (no add-remote from the UI)."""
    from haute._git_state import read_working_branch

    _assert_git_repo(cwd)
    _validate_ref_name(remote)
    if remote not in _remote_names(cwd):
        raise GitDomainError(f"No remote named '{remote}' is configured.")

    working = read_working_branch(project_root)
    if working is None:
        raise GitDomainError("No working branch is set for this clone — nothing to push.")
    if _rev_parse(working, cwd=cwd) is None:
        raise GitDomainError(f"Working branch '{working}' does not exist.")
    ledger = ledger_name(working)

    # X4: version labels are canonical org-wide (one `version/<label>` per
    # release). Pre-check for a label already on the remote at a DIFFERENT object
    # and refuse with a friendly message before the push, rather than letting it
    # surface as a raw atomic-push rejection (best-effort: an unreachable remote
    # skips the check and git's own tag-reject backstops a real collision).
    collisions = _tag_collisions(remote, working, cwd=cwd)
    if collisions:
        labels = ", ".join(sorted(c[len("version/") :] for c in collisions))
        plural = "s" if len(collisions) > 1 else ""
        raise GitDomainError(
            f"Version label{plural} ({labels}) already exist on '{remote}' pointing "
            "at a different version. Each release name is shared across the team — "
            "pick a different label, or coordinate with whoever published it."
        )

    # Push the pair; include the ledger only when it has been spawned. No
    # --force / --force-with-lease — published history is never rewritten (S33).
    # --follow-tags carries the annotated version/<label> tags reachable from the
    # pushed commits (X4: labels travel with the work they mark).
    refspecs = [f"{working}:{working}"]
    if _rev_parse(ledger, cwd=cwd) is not None:
        refspecs.append(f"{ledger}:{ledger}")

    cmd = ["git", "push", "--atomic", "--follow-tags", remote, *refspecs]
    result = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", cwd=cwd or Path.cwd()
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        logger.warning("git_push_failed", remote=remote, refs=refspecs, stderr=stderr)
        if any(s in stderr for s in ("non-fast-forward", "fetch first", "[rejected]")):
            # M7: a rejection is the moment we KNOW we're diverged — turn it into
            # the data-bearing fork the UI needs, not a generic dead-end string.
            raise _push_rejection(remote, working, ledger, project_root, cwd=cwd)
        raise GitError(stderr or "git push failed")

    pushed = [working] + ([ledger] if len(refspecs) == 2 else [])
    # X3 robustness (§6.8): record the tips we just published so rewrite detection
    # survives a pruned reflog (keyed <remote>/<ref>).
    from haute._git_state import record_pushed_shas

    pushed_shas: dict[str, str] = {}
    w_tip = _rev_parse(working, cwd=cwd)
    if w_tip is not None:
        pushed_shas[f"{remote}/{working}"] = w_tip
    if len(refspecs) == 2:
        l_tip = _rev_parse(ledger, cwd=cwd)
        if l_tip is not None:
            pushed_shas[f"{remote}/{ledger}"] = l_tip
    record_pushed_shas(project_root, pushed_shas)

    logger.info("pushed_working_pair", remote=remote, branches=pushed)
    return GitPushResponse(
        remote=remote, working_branch=working, ledger_branch=ledger, pushed_refs=pushed
    )


def fast_forward_pair(
    remote: str, project_root: Path, cwd: Path | None = None
) -> GitFastForwardResponse:
    """Catch up the working pair to *remote*'s tips by FAST-FORWARD only (D1/D2).

    A pure ref advance, never a merge — conflict-free by construction. Refuses
    anything that isn't a clean fast-forward: it re-fetches so the decision is on
    fresh tips, then requires every leg to be behind-or-synced. If any leg is
    ahead/diverged a save landed since detection — the user resolves by spinning
    off a copy, never a silent merge (never-merge-locally). The ledger is the
    checked-out branch (HEAD-on-ledger), so it advances with ``merge --ff-only``
    (which also updates the working tree); the working ref advances with a CAS
    ``update-ref``. Volatile caches are wiped first (S12); the caller pauses the
    watcher for the tree replacement (M4)."""
    from haute._git_state import read_working_branch

    _assert_git_repo(cwd)
    _assert_no_git_op_in_progress(cwd)
    _validate_ref_name(remote)
    if remote not in _remote_names(cwd):
        raise GitDomainError(f"No remote named '{remote}' is configured.")

    working = read_working_branch(project_root)
    if working is None:
        raise GitDomainError("No working branch is set for this clone.")
    if _rev_parse(working, cwd=cwd) is None:
        raise GitDomainError(f"Working branch '{working}' does not exist.")
    ledger = ledger_name(working)

    # Normal operating posture only: HEAD must be on the ledger. While viewing
    # history / detached (a move state) the on-disk tree isn't this branch, so a
    # catch-up would be meaningless — refuse and let the user return first.
    if _get_current_branch(cwd) != ledger:
        raise GitDomainError("Return to your branch before catching up — you're viewing history.")

    # A ff updates the working tree; unsaved tracked edits would be clobbered (and
    # would otherwise surface as a raw git error). Refuse with guidance instead.
    ok_status, status = _run_git_ok("status", "--porcelain", "--untracked-files=no", cwd=cwd)
    if ok_status and status.strip():
        raise GitDomainError("You have unsaved changes. Save or discard them before catching up.")

    # Re-fetch so the catch-up decision is on fresh tips (authoritative, not a
    # poll), then read both legs.
    with _fetch_exec_lock:
        _fetch_refs(remote, working, ledger, cwd=cwd)
    w_leg = _leg_state(working, remote, cwd=cwd)
    l_leg = _leg_state(ledger, remote, cwd=cwd)

    if any(leg.status in ("ahead", "diverged") for leg in (w_leg, l_leg)):
        raise GitDomainError(
            "Can't catch up — you have local changes the remote doesn't have. Spin "
            "off a copy to keep them, then reconcile."
        )
    if w_leg.status != "behind" and l_leg.status != "behind":
        raise GitDomainError(f"Already up to date with '{remote}'.")

    # Volatile caches must not survive into the caught-up tree (S12).
    _wipe_volatile_artefacts(cwd or Path.cwd())

    fast_forwarded: list[str] = []
    # Ledger first (it's HEAD; merge --ff-only advances it and the working tree).
    if l_leg.status == "behind":
        _run_git("merge", "--ff-only", f"refs/remotes/{remote}/{ledger}", cwd=cwd)
        fast_forwarded.append(ledger)
    # Working ref (not checked out): CAS-advance it to its remote tip.
    if w_leg.status == "behind":
        old = _rev_parse(working, cwd=cwd)
        target = _rev_parse(f"refs/remotes/{remote}/{working}", cwd=cwd)
        if old is None or target is None:
            raise GitError("could not resolve refs for the working-branch fast-forward")
        _run_git("update-ref", f"refs/heads/{working}", target, old, cwd=cwd)
        fast_forwarded.append(working)

    logger.info("fast_forwarded_pair", remote=remote, refs=fast_forwarded)
    return GitFastForwardResponse(
        remote=remote, working_branch=working, fast_forwarded=fast_forwarded
    )


def _unique_aside_name(working: str, cwd: Path | None = None) -> str:
    """A dated ``<working>-local-<date>`` name for which BOTH it and its ledger
    are free, so a branch-away can't collide on either ref. Disambiguates with a
    counter when several set-asides land on one day."""

    def taken(name: str) -> bool:
        return (
            _rev_parse(name, cwd=cwd) is not None
            or _rev_parse(ledger_name(name), cwd=cwd) is not None
        )

    date = datetime.now(UTC).strftime("%Y%m%d")
    base = f"{working}-local-{date}"
    if not taken(base):
        return base
    counter = 2
    while taken(f"{base}-{counter}"):
        counter += 1
    return f"{base}-{counter}"


def _rollback_branch_away(
    working: str,
    ledger: str,
    aside: str,
    aside_ledger: str,
    *,
    renamed_w: bool,
    renamed_l: bool,
    created_w: bool,
    created_l: bool,
    cwd: Path | None = None,
) -> None:
    """Best-effort undo of a partially-applied branch-away so a mid-sequence
    failure never strands the pair under the dated name. Never raises: drop any
    freshly-created canonical refs, rename the set-aside pair back, and restore
    HEAD onto the original ledger."""
    if created_l:
        _run_git_ok("branch", "-D", ledger, cwd=cwd)
    if created_w:
        _run_git_ok("branch", "-D", working, cwd=cwd)
    if renamed_l:
        _run_git_ok("branch", "-m", aside_ledger, ledger, cwd=cwd)
    if renamed_w:
        _run_git_ok("branch", "-m", aside, working, cwd=cwd)
    _run_git_ok("checkout", ledger, cwd=cwd)


def branch_away(remote: str, project_root: Path, cwd: Path | None = None) -> GitBranchAwayResponse:
    """M3: resolve a remote fork by setting the local pair aside under a dated name
    and repointing the canonical name to the remote's tips — both lineages
    preserved, the baton intact, zero rewrites (the never-merge-locally escape).

    The canonical name keeps tracking the SHARED line (decision: shared line keeps
    the name); the local divergent work is preserved under ``<W>-local-<date>``
    (S35: surfaced, never silent). NOT the move-mode rewind — no ref is ever wound
    back. ``oL`` absent (X2) → repoint only ``W`` and let the ledger respawn at the
    refreshed tip. Atomic with rollback; the caller pauses the watcher (M4)."""
    from haute._git_state import read_working_branch, set_fork, write_working_branch

    _assert_git_repo(cwd)
    _assert_no_git_op_in_progress(cwd)
    _validate_ref_name(remote)
    if remote not in _remote_names(cwd):
        raise GitDomainError(f"No remote named '{remote}' is configured.")

    working = read_working_branch(project_root)
    if working is None:
        raise GitDomainError("No working branch is set for this clone.")
    old_w = _rev_parse(working, cwd=cwd)
    if old_w is None:
        raise GitDomainError(f"Working branch '{working}' does not exist.")
    ledger = ledger_name(working)
    # Normal posture only: HEAD on the ledger (not detached / mid-move).
    if _get_current_branch(cwd) != ledger:
        raise GitDomainError(
            "Return to your branch before spinning off a copy — you're viewing history."
        )
    ok_status, status = _run_git_ok("status", "--porcelain", "--untracked-files=no", cwd=cwd)
    if ok_status and status.strip():
        raise GitDomainError(
            "You have unsaved changes. Save or discard them before spinning off a copy."
        )

    # Fresh tips so we adopt the current shared line (deliberate action).
    with _fetch_exec_lock:
        _fetch_refs(remote, working, ledger, cwd=cwd)
    remote_w = _rev_parse(f"refs/remotes/{remote}/{working}", cwd=cwd)
    if remote_w is None:
        raise GitDomainError(
            f"'{remote}' has no '{working}' to adopt — push first, or pick another remote."
        )
    remote_l = _rev_parse(f"refs/remotes/{remote}/{ledger}", cwd=cwd)
    old_l = _rev_parse(ledger, cwd=cwd)
    if old_l is None:  # HEAD is on the ledger, so it exists — defensive narrowing
        raise GitDomainError(f"Save ledger '{ledger}' does not exist.")
    if old_w == remote_w and (remote_l is None or old_l == remote_l):
        raise GitDomainError(f"Already in sync with '{remote}' — nothing to set aside.")

    aside = _unique_aside_name(working, cwd=cwd)
    aside_ledger = ledger_name(aside)

    # Volatile caches must not bleed from the local tree into the adopted one (S12).
    _wipe_volatile_artefacts(cwd or Path.cwd())

    renamed_w = renamed_l = created_w = created_l = False
    try:
        # Free the pair for renaming (HEAD is on the ledger): detach at its tip —
        # same commit, so the working tree doesn't change here.
        _run_git("checkout", "--detach", old_l, cwd=cwd)
        _run_git("branch", "-m", working, aside, cwd=cwd)
        renamed_w = True
        _run_git("branch", "-m", ledger, aside_ledger, cwd=cwd)
        renamed_l = True
        _run_git("branch", working, remote_w, cwd=cwd)
        created_w = True
        if remote_l is not None:
            _run_git("branch", ledger, remote_l, cwd=cwd)
            created_l = True
            _run_git("checkout", ledger, cwd=cwd)
        else:
            # X2: no remote ledger — respawn it at the adopted working tip + checkout.
            resolve_ledger(working, cwd=cwd)
        write_working_branch(project_root, working)  # canonical name unchanged
    except (GitError, OSError):
        _rollback_branch_away(
            working,
            ledger,
            aside,
            aside_ledger,
            renamed_w=renamed_w,
            renamed_l=renamed_l,
            created_w=created_w,
            created_l=created_l,
            cwd=cwd,
        )
        raise

    base = _merge_base(old_w, remote_w, cwd=cwd)
    if base is not None:
        set_fork(project_root, aside, base)  # branch-manager back-link for the set-aside line
    logger.info("branched_away", working=working, set_aside=aside, remote=remote)
    return GitBranchAwayResponse(working_branch=working, set_aside_as=aside)


# ---------------------------------------------------------------------------
# Branch manager (P5) — working branches as version lines (their ledgers are
# implicit), with the §8 guards: archive the pair bidirectionally (S32), delete
# the pair refusing on unmerged ledger saves (loss is real on delete only).
# ---------------------------------------------------------------------------


def _has_unmerged_saves(
    working_tip: str | None, ledger_tip: str | None, cwd: Path | None = None
) -> bool:
    """Whether a pair's ledger holds saves not yet milestoned into its working
    branch (i.e. the ledger is ahead of the working branch). Takes resolved tip
    SHAs — callers already hold them (from the for-each-ref enumeration or a
    prior rev-parse), so the merge-base lands in the SHA-keyed cache and an
    unmoved pair costs no subprocess on re-read."""
    if working_tip is None or ledger_tip is None:
        return False
    return _merge_base(working_tip, ledger_tip, cwd=cwd) != ledger_tip


def _normalize_to_working(branch: str) -> str:
    """A ledger name resolves to the working branch it serves; anything else is
    taken as the working name itself (archive/delete operate on the pair)."""
    return working_name(branch) or branch


def working_branches(project_root: Path, cwd: Path | None = None) -> GitWorkingBranchesResponse:
    """The branch manager's view: every working branch (active + archived),
    ledgers hidden, the repo's default deploy branch excluded — each with its
    current/archived flags and whether its ledger has unmerged saves."""
    from haute._git_state import read_forks, read_working_branch

    _assert_git_repo(cwd)
    current = read_working_branch(project_root)
    default = _get_default_branch(cwd)
    forks = read_forks(project_root)
    # The working tree belongs to whatever HEAD points at (the current branch's
    # ledger); tracked, uncommitted changes block the switch-away that archive/
    # delete of the *current* pair needs. Compute once.
    ok_dirty, dirty_status = _run_git_ok("status", "--porcelain", "--untracked-files=no", cwd=cwd)
    tree_dirty = ok_dirty and bool(dirty_status.strip())

    entries: list[GitManagedBranch] = []
    # Working AND ledger tips come from the single for-each-ref enumeration
    # (ledgers are local heads too) — no per-branch rev-parse pair, and the
    # unmerged-saves merge-base is SHA-keyed-cached. The enumeration carries no
    # ahead-behind counts, but it does preserve the yours-first ORDER the
    # manager view consumes.
    listing, tips = _list_branches_with_tips(cwd=cwd)
    for b in listing.branches:
        # Working branches only — ledgers (category "ledger") and protected
        # branches are not version lines; the default branch is deploy-only.
        if branch_category(b.name) != "working" or b.name == default:
            continue
        is_current = b.name == current
        # The commit this branch was spawned from, if still reachable (a stale
        # fork-point — its lineage deleted — is dropped so no dangling back-link).
        fork = forks.get(b.name)
        forked_from = fork if fork and _rev_parse(fork, cwd=cwd) is not None else None
        entries.append(
            GitManagedBranch(
                name=b.name,
                is_current=is_current,
                is_archived=b.is_archived,
                has_unmerged_saves=_has_unmerged_saves(
                    tips.get(b.name), tips.get(ledger_name(b.name)), cwd=cwd
                ),
                has_uncommitted_changes=is_current and tree_dirty,
                forked_from=forked_from,
            )
        )
    return GitWorkingBranchesResponse(current=current, branches=entries)


def _switch_away_if_active(
    working: str,
    ledger: str,
    project_root: Path,
    cwd: Path | None = None,
    discard: bool = False,
) -> None:
    """Before archiving/deleting a pair, move HEAD off it (a checked-out branch
    can't be renamed/deleted) and forget it as the working branch if recorded.

    When *discard* (a confirmed delete — the branch is going away anyway), a
    dirty tree is force-discarded with the checkout. Otherwise tracked
    modifications refuse the move with actionable guidance, since a lossless
    archive must not silently throw away volatile work (S12/S38)."""
    from haute._git_state import clear_working_branch, read_working_branch

    recorded = read_working_branch(project_root)
    if recorded == working or _get_current_branch(cwd) in (working, ledger):
        if not discard:
            # TRACKED modifications would make the checkout abort with a raw,
            # sanitized error. Refuse with actionable guidance instead. Untracked
            # files (e.g. .haute/state.json) don't block a checkout, so ignore.
            ok, status = _run_git_ok("status", "--porcelain", "--untracked-files=no", cwd=cwd)
            if ok and status.strip():
                raise GitDomainError(
                    "You have unsaved changes on this branch. Save or discard "
                    "them before archiving it."
                )
            _run_git("checkout", _get_default_branch(cwd), cwd=cwd)
        else:
            # Confirmed delete: discard the dirty tree along with the branch.
            _run_git("checkout", "-f", _get_default_branch(cwd), cwd=cwd)
        if recorded == working:
            clear_working_branch(project_root)


def _unique_archive_name(working: str, cwd: Path | None = None) -> str:
    """An ``archive/<working>`` name for which BOTH it and its ledger
    (``archive/<working>-save``) are free, so the pair can't collide with an
    existing branch on either ref. Disambiguates with the date, then a counter."""

    def taken(name: str) -> bool:
        return (
            _rev_parse(name, cwd=cwd) is not None
            or _rev_parse(ledger_name(name), cwd=cwd) is not None
        )

    base = f"{_ARCHIVE_PREFIX}/{working}"
    if not taken(base):
        return base
    date = datetime.now(UTC).strftime("%Y%m%d")
    candidate = f"{base}-{date}"
    counter = 2
    while taken(candidate):
        candidate = f"{base}-{date}-{counter}"
        counter += 1
    return candidate


def archive_working_pair(
    branch: str, project_root: Path, cwd: Path | None = None
) -> GitArchiveResponse:
    """Archive a working branch and its ledger together (S32): bidirectional
    (either name archives both), switches away first if it's the active pair,
    NO unmerged-saves refusal (the saves ride into the archived ledger), and
    no remote side effects (S16)."""
    _assert_git_repo(cwd)
    _validate_ref_name(branch)
    working = _normalize_to_working(branch)
    _assert_eligible_working(working)

    if _rev_parse(working, cwd=cwd) is None:
        raise GitDomainError(f"Branch '{working}' does not exist.")
    if working.startswith(f"{_ARCHIVE_PREFIX}/"):
        raise GitDomainError(f"'{working}' is already archived.")

    ledger = ledger_name(working)
    _switch_away_if_active(working, ledger, project_root, cwd=cwd)

    # Both target names are guaranteed free, so neither rename collides; if the
    # ledger rename still fails, roll back the working rename so we never leave a
    # half-archived, mis-paired state.
    archived = _unique_archive_name(working, cwd=cwd)
    _run_git("branch", "-m", working, archived, cwd=cwd)
    if _rev_parse(ledger, cwd=cwd) is not None:
        try:
            _run_git("branch", "-m", ledger, ledger_name(archived), cwd=cwd)
        except GitError:
            _run_git_ok("branch", "-m", archived, working, cwd=cwd)
            raise

    from haute._git_state import rename_fork

    rename_fork(project_root, working, archived)  # keep the back-link valid
    logger.info("working_pair_archived", working=working, archived=archived)
    return GitArchiveResponse(archived_as=archived)


def _trash_ref(branch: str) -> str:
    """The ``refs/haute/trash/`` ref pinning a deleted branch's tip. A plain
    ref outside ``refs/heads/`` — invisible to the branch surfaces, but it
    keeps the commit chain reachable so gc can never collect a deleted pair
    while its tombstone is alive."""
    return f"refs/haute/trash/{branch}"


def delete_working_pair(
    branch: str,
    project_root: Path,
    confirm: bool = False,
    cwd: Path | None = None,
) -> GitDeleteBranchResponse:
    """Delete a working branch and its ledger together (§8): bidirectional,
    refuses when the ledger has unmerged saves unless *confirm* (loss is real),
    switches away first if active, no remote side effects (S16).

    The delete is trash-preserving: before the branch refs go, both tips are
    pinned under ``refs/haute/trash/`` (an instant ref write that also shields
    the objects from gc) and a tombstone — tips, forks.json back-link,
    archived flag, delete time — lands in ``.haute/trash.json``, so
    ``undelete_working_pair`` can rebuild the pair exactly. The deleted
    lineage therefore survives locally even though the branches vanish."""
    _assert_git_repo(cwd)
    _validate_ref_name(branch)
    working = _normalize_to_working(branch)
    _assert_eligible_working(working)

    working_tip = _rev_parse(working, cwd=cwd)
    if working_tip is None:
        raise GitDomainError(f"Branch '{working}' does not exist.")

    ledger = ledger_name(working)
    ledger_tip = _rev_parse(ledger, cwd=cwd)

    if not confirm and _has_unmerged_saves(working_tip, ledger_tip, cwd=cwd):
        raise GitGuardrailError(
            f"'{working}' has saves that were never committed to a milestone — "
            "deleting it loses them. Confirm to delete anyway."
        )

    # A confirmed delete is destructive by intent — discard a dirty tree along
    # with the branch rather than refusing (S38: deleting the lineage already
    # dwarfs the uncommitted edits).
    _switch_away_if_active(working, ledger, project_root, cwd=cwd, discard=True)

    from haute._git_state import read_forks, record_trash, remove_fork

    # Recovery net FIRST, refs second — a failure between the two leaves a
    # harmless extra trash ref, never an unrecoverable deletion.
    _run_git("update-ref", _trash_ref(working), working_tip, cwd=cwd)
    if ledger_tip is not None:
        _run_git("update-ref", _trash_ref(ledger), ledger_tip, cwd=cwd)
    else:
        # A re-deleted name whose ledger no longer exists must not leave the
        # PREVIOUS delete's ledger pin behind to be mis-restored later.
        _run_git_ok("update-ref", "-d", _trash_ref(ledger), cwd=cwd)
    record_trash(
        project_root,
        working,
        {
            "branch_tip": working_tip,
            "ledger_tip": ledger_tip,
            "forked_from": read_forks(project_root).get(working),
            "was_archived": working.startswith(f"{_ARCHIVE_PREFIX}/"),
            "deleted_at": datetime.now(UTC).isoformat(),
        },
    )

    _run_git("branch", "-D", working, cwd=cwd)
    if ledger_tip is not None and _rev_parse(ledger, cwd=cwd) is not None:
        _run_git("branch", "-D", ledger, cwd=cwd)

    remove_fork(project_root, working)
    logger.info("working_pair_deleted", working=working, confirmed=confirm)
    return GitDeleteBranchResponse(status="deleted", branch=working)


def undelete_working_pair(
    branch: str, project_root: Path, cwd: Path | None = None
) -> GitUndeleteResponse:
    """Restore a deleted working pair from its trash pins + tombstone — the
    inverse of delete_working_pair's recovery net.

    Pure ref/state ops (no checkout, no HEAD movement): the working and
    ledger refs are recreated at their recorded tips, the forks.json
    back-link comes back when one was recorded, and the trash refs +
    tombstone are consumed. The archived flag needs no separate restore —
    archived-ness IS the ``archive/`` name prefix, and the pair is recreated
    under the exact name it was deleted as. The restored pair is NOT adopted
    as the working branch; the user switches to it deliberately.

    Domain errors (verbatim to the client): no tombstone for the name, either
    restored name already occupied, or the recorded commit no longer exists
    (tombstones can outlive their objects if the trash refs were hand-deleted
    and gc ran)."""
    from haute._git_state import read_trash, remove_trash, set_fork

    _assert_git_repo(cwd)
    _validate_ref_name(branch)
    working = _normalize_to_working(branch)
    _assert_eligible_working(working)

    entry = read_trash(project_root).get(working)
    if entry is None:
        raise GitDomainError(f"No deleted branch named '{working}' to restore.")

    ledger = ledger_name(working)
    if _rev_parse(working, cwd=cwd) is not None:
        raise GitDomainError(f"Cannot restore: a branch named '{working}' already exists.")
    if _rev_parse(ledger, cwd=cwd) is not None:
        raise GitDomainError(f"Cannot restore: a branch named '{ledger}' already exists.")

    branch_tip = entry.get("branch_tip")
    if not isinstance(branch_tip, str) or _rev_parse(branch_tip, cwd=cwd) is None:
        raise GitDomainError(
            f"'{working}' can no longer be restored — its recorded commit is gone."
        )
    ledger_tip = entry.get("ledger_tip")
    if not (isinstance(ledger_tip, str) and _rev_parse(ledger_tip, cwd=cwd) is not None):
        ledger_tip = None  # pair deleted before its ledger ever spawned

    _run_git("update-ref", f"refs/heads/{working}", branch_tip, cwd=cwd)
    if ledger_tip is not None:
        _run_git("update-ref", f"refs/heads/{ledger}", ledger_tip, cwd=cwd)

    forked_from = entry.get("forked_from")
    if isinstance(forked_from, str) and forked_from:
        set_fork(project_root, working, forked_from)

    _run_git_ok("update-ref", "-d", _trash_ref(working), cwd=cwd)
    _run_git_ok("update-ref", "-d", _trash_ref(ledger), cwd=cwd)
    remove_trash(project_root, working)
    logger.info("working_pair_undeleted", working=working)
    return GitUndeleteResponse(status="restored", branch=working)


def restore_working_pair(
    branch: str, project_root: Path, cwd: Path | None = None
) -> GitRestoreResponse:
    """Un-archive a pair: rename ``archive/<X>`` → ``<X>`` and its ledger back
    (the inverse of archive_working_pair). Bidirectional (accepts either archived
    name); refuses if a live branch already occupies either restored name; rolls
    back the working rename if the ledger rename fails."""
    _assert_git_repo(cwd)
    _validate_ref_name(branch)
    archived_working = _normalize_to_working(branch)
    prefix = f"{_ARCHIVE_PREFIX}/"
    if not archived_working.startswith(prefix):
        raise GitDomainError(f"'{archived_working}' is not an archived branch.")
    if _rev_parse(archived_working, cwd=cwd) is None:
        raise GitDomainError(f"Branch '{archived_working}' does not exist.")

    restored = archived_working[len(prefix) :]
    _assert_eligible_working(restored)
    if _rev_parse(restored, cwd=cwd) is not None:
        raise GitDomainError(f"Cannot restore: a branch named '{restored}' already exists.")
    restored_ledger = ledger_name(restored)
    if _rev_parse(restored_ledger, cwd=cwd) is not None:
        raise GitDomainError(f"Cannot restore: a branch named '{restored_ledger}' already exists.")

    archived_ledger = ledger_name(archived_working)
    _run_git("branch", "-m", archived_working, restored, cwd=cwd)
    if _rev_parse(archived_ledger, cwd=cwd) is not None:
        try:
            _run_git("branch", "-m", archived_ledger, restored_ledger, cwd=cwd)
        except GitError:
            _run_git_ok("branch", "-m", restored, archived_working, cwd=cwd)
            raise

    from haute._git_state import rename_fork

    rename_fork(project_root, archived_working, restored)
    logger.info("working_pair_restored", restored=restored)
    return GitRestoreResponse(restored_as=restored)


# ---------------------------------------------------------------------------
# Local preferences (P5d) — per-clone UI settings, e.g. the switch-confirm
# "don't ask again" toggle (persisted to the whole local environment, S38).
# ---------------------------------------------------------------------------

_PREF_SKIP_SWITCH_CONFIRM = "skipSwitchConfirm"


def get_prefs(project_root: Path) -> GitPrefs:
    """This clone's local UI preferences (defaults when unset/malformed)."""
    from haute._git_state import read_prefs

    raw = read_prefs(project_root)
    return GitPrefs(skip_switch_confirm=bool(raw.get(_PREF_SKIP_SWITCH_CONFIRM, False)))


def set_prefs(prefs: GitPrefs, project_root: Path) -> GitPrefs:
    """Persist this clone's local UI preferences; returns the stored state."""
    from haute._git_state import write_pref

    write_pref(project_root, _PREF_SKIP_SWITCH_CONFIRM, prefs.skip_switch_confirm)
    return prefs
