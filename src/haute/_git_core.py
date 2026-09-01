"""Git repository primitives and the sole subprocess boundary for Haute."""

from __future__ import annotations

import getpass
import inspect
import os
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache, wraps
from pathlib import Path
from typing import Generic, Literal, ParamSpec, TypeVar, overload

from haute._git_lock import repository_mutation
from haute._logging import get_logger
from haute.errors import HauteError
from haute.schemas import (
    GitBranchItem,
    GitBranchListResponse,
    GitMilestoneFork,
    GitPushRejection,
    GitRemoteLeg,
)

logger = get_logger(component="git")

DEFAULT_PROTECTED_BRANCHES = frozenset({"main", "master", "develop", "production"})
PROTECTED_BRANCHES = DEFAULT_PROTECTED_BRANCHES

# Branch names created by haute follow: pricing/<user>/<slug>
_BRANCH_PREFIX = "pricing"
_ARCHIVE_PREFIX = "archive"

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

# Minimum seconds between `git fetch` calls per (cwd, remote, kind).
_FETCH_COOLDOWN_SECONDS: float = 30.0
# Hard ceiling on a single background fetch so a slow / unreachable / auth-walled
# remote can never wedge the request thread (F1).
_FETCH_TIMEOUT_SECONDS: float = 10.0
# Publication can legitimately take longer than a ref advertisement/fetch, but
# it must still release the repository mutation lock on a wedged transport.
_PUSH_TIMEOUT_SECONDS: float = 60.0
# A hosted restore clones a whole project before the server accepts traffic, so
# it is allowed materially longer than a push — but still bounded, because an
# unreachable remote must surface as a gate, never as a container that never
# finishes booting.
_CLONE_TIMEOUT_SECONDS: float = 300.0
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


class GitTransactionError(GitDomainError):
    """A Git mutation failed and its compensating rollback also failed."""


class GitHistoryReadError(GitDomainError):
    """A historical pipeline could not be materialised or parsed safely."""


class _GitProcessTimeoutError(TimeoutError):
    """A deliberately bounded Git process exceeded its timeout."""


_Output = TypeVar("_Output", str, bytes)


@dataclass(frozen=True, slots=True)
class _GitProcessResult(Generic[_Output]):
    """Immutable captured result of a Git process invocation."""

    returncode: int
    stdout: _Output
    stderr: _Output


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

_P = ParamSpec("_P")
_R = TypeVar("_R")


def _serialized_mutation(func: Callable[_P, _R]) -> Callable[_P, _R]:
    """Run a public mutator under the repository's shared reentrant lock."""
    signature = inspect.signature(func)

    @wraps(func)
    def wrapped(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        bound = signature.bind_partial(*args, **kwargs)
        location = bound.arguments.get("cwd")
        if location is None:
            location = bound.arguments.get("project_root")
        root = Path(location) if location is not None else Path.cwd()
        with repository_mutation(root):
            return func(*args, **kwargs)

    return wrapped


_GIT_LOCALE_ENV = {
    "LC_ALL": "C",
    "LANG": "C",
    "LANGUAGE": "C",
}


def _git_env(overrides: dict[str, str] | None = None) -> dict[str, str]:
    """Inherited process environment with stable Git output language."""
    return {**os.environ, **_GIT_LOCALE_ENV, **(overrides or {})}


@overload
def _run_git_process(
    *args: str,
    binary: Literal[False] = False,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
    errors: str | None = None,
) -> _GitProcessResult[str]: ...


@overload
def _run_git_process(
    *args: str,
    binary: Literal[True],
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
    errors: str | None = None,
) -> _GitProcessResult[bytes]: ...


def _run_git_process(
    *args: str,
    binary: bool = False,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
    errors: str | None = None,
) -> _GitProcessResult[str] | _GitProcessResult[bytes]:
    """Run Git with captured output, translating only an expired timeout."""
    try:
        if binary:
            binary_result = subprocess.run(
                ["git", *args],
                capture_output=True,
                cwd=cwd or Path.cwd(),
                env=env,
                timeout=timeout,
            )
            return _GitProcessResult(
                binary_result.returncode, binary_result.stdout, binary_result.stderr
            )
        text_result = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors=errors,
            cwd=cwd or Path.cwd(),
            env=env,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise _GitProcessTimeoutError() from exc
    return _GitProcessResult(text_result.returncode, text_result.stdout, text_result.stderr)


def _run_git(
    *args: str,
    check: bool = True,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
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
    if input_text is None:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=cwd or Path.cwd(),
            env=_git_env(env),
        )
        returncode = result.returncode
        stdout = result.stdout
        stderr = result.stderr
    else:
        # Text-mode stdin translates LF to CRLF on Windows. Git's stdin
        # transaction protocols require literal LF separators, so pass bytes
        # whenever a caller supplies input.
        byte_result = subprocess.run(
            cmd,
            capture_output=True,
            cwd=cwd or Path.cwd(),
            env=_git_env(env),
            input=input_text.encode("utf-8"),
        )
        returncode = byte_result.returncode
        stdout = byte_result.stdout.decode("utf-8", errors="replace")
        stderr = byte_result.stderr.decode("utf-8", errors="replace")
    if check and returncode != 0:
        stderr = stderr.strip()
        logger.warning("git_command_failed", cmd=cmd, stderr=stderr)
        raise GitError(stderr or f"git {args[0]} failed")
    return stdout.strip()


def _run_git_ok(*args: str, cwd: Path | None = None) -> tuple[bool, str]:
    """Run a git command and return (success, stdout).  Never raises."""
    cmd = ["git"] + list(args)
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=cwd or Path.cwd(),
        env=_git_env(),
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
        env=_git_env(),
    )
    return result.returncode, result.stdout.strip()


def _remote_env() -> dict[str, str]:
    """Environment for remote Git commands that must never prompt interactively."""
    return _git_env(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_SSH_COMMAND": "ssh -oBatchMode=yes -oConnectTimeout=10",
        }
    )


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

    With no explicit *refs*, fetch and prune the configured remote namespace.
    Authoritative actions use that form so a genuinely absent optional pair leg
    is represented by a missing tracking ref rather than a failed fetch.
    """
    cmd = (
        ["git", "fetch", remote, *refs, "--quiet"]
        if refs
        else ["git", "fetch", "--prune", remote, "--quiet"]
    )
    try:
        result = _run_git_process(
            *cmd[1:],
            cwd=cwd,
            env=_remote_env(),
            timeout=_FETCH_TIMEOUT_SECONDS,
        )
    except (_GitProcessTimeoutError, OSError, UnicodeError) as exc:
        logger.warning("git_fetch_degraded", remote=remote, error=str(exc))
        return False
    if result.returncode != 0:
        logger.debug("git_fetch_failed", remote=remote, stderr=result.stderr.strip())
        return False
    return True


def git_binary_available() -> bool:
    """Return whether a git executable exists on PATH.

    Distinguishes "no git installed" (some hosted containers) from "no
    repository here" — every subprocess helper below raises
    ``FileNotFoundError`` rather than a git exit code when the binary
    itself is absent, so callers that would otherwise 500 check this
    first.
    """
    return shutil.which("git") is not None


def _is_git_repo(cwd: Path | None = None) -> bool:
    ok, _ = _run_git_ok("rev-parse", "--is-inside-work-tree", cwd=cwd)
    return ok


def _get_current_branch(cwd: Path | None = None) -> str:
    """Return the current branch name, or 'HEAD' if detached."""
    ok, branch = _run_git_ok("symbolic-ref", "--short", "HEAD", cwd=cwd)
    return branch if ok else "HEAD"


def _get_default_branch(cwd: Path | None = None) -> str:
    """Detect the current default branch from live ref state.

    Remote selection and ``refs/remotes/<remote>/HEAD`` can both change during a
    server session, so this ref-name lookup is deliberately not memoized.
    """
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


def _get_user_slug(cwd: Path | None = None) -> str:
    """Get a slugified version of the git user name."""
    ok, name = _run_git_ok("config", "user.name", cwd=cwd)
    if ok and name:
        return _slugify(name)
    # Fallback to OS username
    return _slugify(getpass.getuser())


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


def _is_valid_full_ref_name(name: str) -> bool:
    """Whether *name* obeys Git's full ref-name rules without normalisation.

    This mirrors ``git check-ref-format`` for fully-qualified refs.  Keeping the
    check in-process lets strict remote-advertisement parsing validate every ref
    without spawning an unbounded number of subprocesses for tag-heavy repos.
    """
    if not name.startswith("refs/") or name.endswith("/") or "//" in name:
        return False
    if name == "@" or name.endswith(".") or ".." in name or "@{" in name:
        return False
    if any(ord(char) <= 0x20 or ord(char) == 0x7F for char in name):
        return False
    if any(char in {"~", "^", ":", "?", "*", "[", "\\"} for char in name):
        return False
    components = name.split("/")
    return all(
        component and not component.startswith(".") and not component.endswith(".lock")
        for component in components
    )


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


def _validate_managed_working_branch(branch: str, cwd: Path | None = None) -> None:
    """Validate the untracked clone-state value before it reaches Git commands."""
    full_ref = f"refs/heads/{branch}"
    if (
        not branch
        or branch in {"HEAD", "@"}
        or branch.startswith("-")
        or not _is_valid_full_ref_name(full_ref)
    ):
        raise GitDomainError(f"Invalid working branch {branch!r} in clone state.")
    _validate_ref_name(branch)
    ok, checked = _run_git_ok("check-ref-format", "--branch", branch, cwd=cwd)
    if not ok or checked.strip() != branch:
        raise GitDomainError(f"Invalid working branch {branch!r} in clone state.")
    _assert_eligible_working(branch)


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
# Keys include ``str(cwd)`` so repos served by one process never share entries.
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
    """Drop every content-addressed cache (test isolation + repo-reset hygiene)."""
    _merge_base_cached.cache_clear()
    _is_ancestor_cached.cache_clear()
    _first_parent_spine_cached.cache_clear()
    _commit_parents_cached.cache_clear()
    _graph_log_cached.cache_clear()
    _tree_of_cached.cache_clear()


# Cross-domain shared primitives.
def _ledger_or_branch_sha(branch: str, cwd: Path | None = None) -> str | None:
    """Short SHA of the branch's ledger tip, or the branch tip pre-spawn."""
    ledger = ledger_name(branch)
    tip = _rev_parse(ledger, cwd=cwd) or _rev_parse(branch, cwd=cwd)
    return tip[:8] if tip else None


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


_GraphLogRow = tuple[str, str, tuple[str, ...], str, str]


def _graph_log_rows(
    tip: str, limit: int | None, cwd: Path | None = None
) -> tuple[_GraphLogRow, ...]:
    """Read and parse a NUL-delimited, windowed first-parent log."""
    args = ["log", "--first-parent"]
    if limit is not None:
        args.append(f"--max-count={limit}")
    args.extend(["--format=%H%x00%h%x00%P%x00%aI%x00%s%x00", tip, "--"])
    ok, raw = _run_git_ok(*args, cwd=cwd)
    if not ok:
        raise GitError(f"git log failed for {tip}")
    rows: list[_GraphLogRow] = []
    fields = raw.split("\0")
    for index in range(0, len(fields) - 1, 5):
        parts = fields[index : index + 5]
        if len(parts) != 5:
            break
        sha, short_sha, parents_raw, timestamp, message = parts
        rows.append((sha.lstrip("\n"), short_sha, tuple(parents_raw.split()), timestamp, message))
    return tuple(rows)


@lru_cache(maxsize=256)
def _graph_log_cached(tip_sha: str, limit: int | None, cwd_key: str) -> tuple[_GraphLogRow, ...]:
    """Cache parsed graph rows for an immutable commit tip."""
    return _graph_log_rows(tip_sha, limit, cwd=Path(cwd_key) if cwd_key else None)


def _git_dir(cwd: Path | None = None) -> Path:
    """Return the repository Git directory, including linked-worktree paths."""
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
