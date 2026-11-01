"""Git repository primitives and the sole subprocess boundary for Haute."""

from __future__ import annotations

import contextlib
import inspect
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache, wraps
from pathlib import Path
from typing import Generic, Literal, ParamSpec, TypeVar, overload

from haute._git_lock import repository_mutation
from haute._logging import get_logger
from haute._project_layout import VERSIONED_FILE_LIMIT_BYTES
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

_ARCHIVE_PREFIX = "archive"

# Reconstructable caches only. Not ``outputs``: the analyst's local results, which
# git cannot bring back. Not ``output``: optimiser results were published there by
# default, and Apply Optimisation prices from them.
_VOLATILE_ARTEFACTS = (
    "__pycache__",
    ".haute_cache",
    ".cache",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".webassets-cache",
)

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
# Serialises the actual ``git fetch`` subprocess: two concurrent callers must not launch
# parallel fetches, because git races on the local .git/objects index. Stays
# process-global on purpose: git worktrees share one object store, so the exec lock must
# span them all.
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


class GitBootstrapIncompleteError(GitDomainError):
    """An empty remote received its default branch but not the working/ledger pair.

    The message is hand-written (it names the published default and never carries
    git stderr), so the HTTP layer keeps it verbatim. The remote now holds only the
    validated default — an established remote — so the next push publishes the
    pair through the ordinary established-remote path.
    """


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


class GitRemoteUnavailableError(GitDomainError):
    """A deliberate read of or push to a remote did not go through, for a reason that passes.

    A timeout, an authentication or network failure, a transport that would not
    start or a non-zero exit, or the remote changing under a push's create-only
    lease, with git's stderr kept in the log. The message is hand-written, so the
    HTTP layer keeps it verbatim; hosted publishing retries it like any other
    transport failure rather than stopping for the analyst."""


class GitHistoryReadError(GitDomainError):
    """History could not be read, or a historical pipeline materialised or parsed safely."""


# How many files a refusal over files in use names.
_FILES_NAMED = 5


def files_in_use_message(files: list[str]) -> str:
    """The analyst's message for *files* another program holds open."""
    named = ", ".join(files[:_FILES_NAMED]) + (
        f" and {len(files) - _FILES_NAMED} more" if len(files) > _FILES_NAMED else ""
    )
    one = len(files) == 1
    return (
        f"{named} {'is' if one else 'are'} open in another program, so Haute could not "
        f"replace {'it' if one else 'them'}. Close {'it' if one else 'them'} and try again."
    )


class GitFilesInUseError(GitDomainError):
    """A command that rewrites the working tree could not write *files*: on Windows
    git cannot replace a file another program holds open."""

    def __init__(self, files: list[str]) -> None:
        self.files = files
        super().__init__(files_in_use_message(files))


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
    """Run Git with captured output, translating only an expired timeout.

    Text output is decoded as UTF-8 with *errors* (strict by default) and universal
    newlines, as a text-mode ``subprocess.run`` would."""
    try:
        completed = _run_process_group(
            ["git", *args], cwd=cwd or Path.cwd(), env=env, timeout=timeout
        )
    except subprocess.TimeoutExpired as exc:
        raise _GitProcessTimeoutError() from exc
    if binary:
        return _GitProcessResult(completed.returncode, completed.stdout, completed.stderr)
    return _GitProcessResult(
        completed.returncode,
        _decoded(completed.stdout, errors),
        _decoded(completed.stderr, errors),
    )


def _decoded(output: bytes, errors: str | None) -> str:
    text = output.decode("utf-8", errors or "strict")
    return text.replace("\r\n", "\n").replace("\r", "\n")


# How long a killed command's output pipes are drained before they are abandoned to a
# process that left the group and still holds them.
_KILLED_DRAIN_SECONDS = 5.0


def _run_process_group(
    cmd: list[str], *, cwd: Path, env: dict[str, str] | None, timeout: float | None
) -> subprocess.CompletedProcess[bytes]:
    """Run *cmd* with no stdin, with every process it starts, killed together at *timeout*.

    A remote command's transport (ssh, a credential helper) is a child of git: ending git
    alone would leave it running and holding the output pipes open, and the pipes cannot
    be closed under Windows' reader threads. On POSIX the processes share a new session,
    which also leaves them no terminal to ask for a password on, and its process group is
    killed. On Windows git starts suspended and joins a job object before it runs, so every
    process it starts belongs to the job whatever becomes of its parent, and the job is
    terminated.
    """
    if sys.platform == "win32":
        import psutil

        process = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            env=env,
            creationflags=_CREATE_SUSPENDED,
        )
        job = None
        try:
            job = _job_holding(process.pid)
            psutil.Process(process.pid).resume()
        except BaseException:
            process.kill()
            process.communicate()
            if job is not None:
                _kernel32.CloseHandle(job)
            raise
        try:
            return _communicate_until(
                process, timeout, kill=lambda: _kernel32.TerminateJobObject(job, 1)
            )
        finally:
            _kernel32.CloseHandle(job)
    process = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        env=env,
        start_new_session=True,
    )
    return _communicate_until(process, timeout, kill=lambda: _kill_session(process))


def _communicate_until(
    process: subprocess.Popen[bytes], timeout: float | None, kill: Callable[[], object]
) -> subprocess.CompletedProcess[bytes]:
    """*process*'s output and exit, or at *timeout* its processes killed and the pipes drained."""
    with process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.communicate(timeout=_KILLED_DRAIN_SECONDS)
            raise
    return subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)


def _kill_session(process: subprocess.Popen[bytes]) -> None:
    """Kill a timed-out POSIX command's process group, every process it started."""
    if sys.platform != "win32":
        # A member that cannot be signalled must not hide the timeout itself.
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGKILL)


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _CREATE_SUSPENDED = 0x00000004
    _PROCESS_SET_QUOTA = 0x0100
    _PROCESS_TERMINATE = 0x0001

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    _kernel32.TerminateJobObject.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _kernel32.CloseHandle.restype = wintypes.BOOL

    def _job_holding(pid: int) -> int:
        """A new job object holding process *pid*, still suspended, and so everything it
        will start. It is not killed when the handle closes: a credential cache git starts
        may outlive the command."""
        job = _kernel32.CreateJobObjectW(None, None)
        if not job:
            raise ctypes.WinError(ctypes.get_last_error())
        handle = _kernel32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid)
        assigned = bool(handle) and bool(_kernel32.AssignProcessToJobObject(job, handle))
        error = ctypes.get_last_error()
        if handle:
            _kernel32.CloseHandle(handle)
        if not assigned:
            _kernel32.CloseHandle(job)
            raise ctypes.WinError(error)
        return int(job)


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


# Haute's transport when the analyst has not chosen one: OpenSSH that never prompts.
_DEFAULT_SSH_COMMAND = "ssh -oBatchMode=yes -oConnectTimeout=10"


def _ssh_command_configured(cwd: Path | None = None) -> bool:
    """Whether Git has an ssh transport of the analyst's own to run: ``GIT_SSH_COMMAND``,
    ``core.sshCommand`` or ``GIT_SSH``, the three Git consults before plain ``ssh``."""
    if "GIT_SSH_COMMAND" in os.environ or "GIT_SSH" in os.environ:
        return True
    configured, _ = _run_git_ok("config", "--get", "core.sshCommand", cwd=cwd)
    return configured


def _remote_env(cwd: Path | None = None) -> dict[str, str]:
    """Environment for remote Git commands that must never prompt interactively.

    A transport the analyst configured reaches Git unchanged, in Git's own order of
    precedence: OpenSSH takes the first value of an option, and a wrapper or Plink may
    not take OpenSSH options at all, so nothing is appended to it. It must not prompt,
    which no terminal, no stdin and the caller's deadline enforce whatever it is.
    Haute's batch-mode OpenSSH command applies only when none is configured."""
    overrides = {"GIT_TERMINAL_PROMPT": "0", "SSH_ASKPASS_REQUIRE": "never"}
    if not _ssh_command_configured(cwd):
        overrides["GIT_SSH_COMMAND"] = _DEFAULT_SSH_COMMAND
    return _git_env(overrides)


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
            env=_remote_env(cwd),
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


def _assert_git_repo(cwd: Path | None = None) -> None:
    if not _is_git_repo(cwd):
        raise GitDomainError("Not a git repository. Run 'git init' first.")


def _mib(size: int) -> str:
    return f"{size / (1024 * 1024):.4g} MiB"


def _refuse_too_large_for_history(paths: list[str], root: Path, retry: str) -> None:
    """Refuse *paths*, relative to *root*, when one is a file larger than version history
    takes; *retry* ends the message ("commit again")."""
    too_large: list[str] = []
    for path in paths:
        target = root / path
        if not target.is_file():
            continue  # a deletion
        size = target.stat().st_size
        if size > VERSIONED_FILE_LIMIT_BYTES:
            too_large.append(f"{path} ({_mib(size)})")
    if too_large:
        one = len(too_large) == 1
        raise GitDomainError(
            f"{', '.join(too_large)} {'is' if one else 'are'} too large for version history, "
            f"which takes files up to {_mib(VERSIONED_FILE_LIMIT_BYTES)}. Move "
            f"{'it' if one else 'them'} to the project's data/ folder, which stays on this "
            f"machine, and {retry}."
        )


#: One compensating action of a rolled-back mutation. It reports ``False`` when it did not
#: take effect; ``True`` or ``None`` (an action that raises when it fails) when it did.
CompensationStep = Callable[[], bool | None]


def _compensate(failure: Exception, action: str, steps: Iterable[CompensationStep]) -> None:
    """Undo a failed mutation: run every step in order, the later ones even after one fails,
    and return so the caller re-raises *failure*. When a step reported ``False`` or raised a
    git or file error, raise :class:`GitTransactionError` from *failure*, naming *action*."""
    complete = True
    for step in steps:
        try:
            if step() is False:
                complete = False
        except (GitError, OSError):
            complete = False
    if not complete:
        raise GitTransactionError(
            f"{action} failed and automatic rollback was incomplete. "
            "Inspect the repository before retrying."
        ) from failure


def _head_position(cwd: Path | None = None) -> tuple[str, bool]:
    """Where HEAD is, for :func:`_checkout_back`: the branch it is on, or the commit it is
    detached at (a checkout outside Haute), and whether it is attached."""
    attached, branch = _run_git_ok("symbolic-ref", "--short", "HEAD", cwd=cwd)
    if attached:
        return branch, True
    return _run_git("rev-parse", "HEAD", cwd=cwd), False


def _checkout_back(position: tuple[str, bool], cwd: Path | None = None) -> bool:
    """Put HEAD back where :func:`_head_position` found it."""
    target, attached = position
    ok, _ = _run_git_ok("checkout", *(() if attached else ("--detach",)), target, cwd=cwd)
    return ok


def _delete_branch_if_present(branch: str, cwd: Path | None = None) -> bool:
    if _rev_parse(branch, cwd=cwd) is None:
        return True
    ok, _ = _run_git_ok("branch", "-D", branch, cwd=cwd)
    return ok


def _free_pair_name(stem: str, *, bare_first: bool, cwd: Path | None = None) -> str:
    """The first of ``<stem>`` (when *bare_first*), ``<stem>-<YYYYMMDD>``,
    ``<stem>-<YYYYMMDD>-2``, … for which BOTH the name and its ledger are free, so the pair
    collides with no existing branch on either ref."""

    def taken(name: str) -> bool:
        return (
            _rev_parse(name, cwd=cwd) is not None
            or _rev_parse(ledger_name(name), cwd=cwd) is not None
        )

    if bare_first and not taken(stem):
        return stem
    dated = f"{stem}-{datetime.now(UTC).strftime('%Y%m%d')}"
    candidate = dated
    counter = 2
    while taken(candidate):
        candidate = f"{dated}-{counter}"
        counter += 1
    return candidate


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


def _workbench_form_part(path: Path) -> str | None:
    """A workbench form folder's file as a save's message names it: ``workbench/`` and its
    path in the folder without ``.json``, a sheet's layout as ``layout``; None for any other
    file."""
    parts = path.parts
    if "workbench" not in parts or path.suffix != ".json":
        return None
    inside = parts[len(parts) - 1 - parts[::-1].index("workbench") :]
    name = "/".join(inside)
    if name.endswith(".layout.json"):
        return name.removesuffix(".layout.json") + " layout"
    return name.removesuffix(".json")


def _generate_commit_message(changed_files: list[str]) -> str:
    """Generate a human-readable commit message from changed file paths."""
    if not changed_files:
        return "Save progress"

    # Extract meaningful names
    names: list[str] = []
    for f in changed_files:
        p = Path(f)
        form_part = _workbench_form_part(p)
        if form_part is not None:
            names.append(form_part)
        elif p.suffix == ".py" and p.stem != "__init__":
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
    ahead-behind walk, because no caller consumes commit counts. Branches sort by
    name with archived ones last (the branch-manager render order and the startup
    modal's ``eligible[0]`` preselection follow it)."""
    _assert_git_repo(cwd)

    current = _get_current_branch(cwd)

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
                    is_current=name == current,
                    is_archived=name.startswith(f"{_ARCHIVE_PREFIX}/"),
                    last_commit_time=commit_time,
                )
            )

    branches.sort(key=lambda b: (b.is_archived, b.name))

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


def _branch_name_problem(name: str) -> str | None:
    """The first of git's branch-name rules *name* breaks, in words; None when it keeps them."""
    if not name:
        return "it is empty"
    if name in {"HEAD", "@"}:
        return f"git keeps '{name}' for the version open now"
    if name.startswith("-"):
        return "it starts with '-'"
    if " " in name:
        return "it contains a space"
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in name):
        return "it contains a control character"
    for char in "~^:?*[\\":
        if char in name:
            return f"it contains '{char}'"
    for sequence in ("..", "@{", "//"):
        if sequence in name:
            return f"it contains '{sequence}'"
    if name.startswith("/"):
        return "it starts with '/'"
    for ending in ("/", ".lock", "."):
        if name.endswith(ending):
            return f"it ends with '{ending}'"
    if name.startswith("."):
        return "it starts with '.'"
    parts = name.split("/")
    if any(part.startswith(".") for part in parts):
        return "a part of it starts with '.'"
    if any(part.endswith(".lock") for part in parts):
        return "a part of it ends with '.lock'"
    return None


def _branch_name_refused(name: str, problem: str) -> GitDomainError:
    # A name git cannot use (a space typed into the name box) would otherwise fail as
    # raw git stderr, which the routes can only report as "Operation failed".
    return GitDomainError(
        f"'{name}' can't be used as a branch name: {problem}. "
        "Use letters, numbers, dashes and slashes."
    )


def _assert_new_branch_name(name: str, cwd: Path | None = None) -> None:
    """Refuse a typed branch name git cannot use, naming the rule it breaks.

    ``git check-ref-format --branch`` is the authority: a name the in-process rules let
    through is still refused, in words, if git would not take it."""
    problem = _branch_name_problem(name)
    if problem is None:
        accepted, checked = _run_git_ok("check-ref-format", "--branch", name, cwd=cwd)
        if not accepted or checked != name:
            problem = "git does not accept it as a branch name"
    if problem is not None:
        raise _branch_name_refused(name, problem)


def _assert_eligible_working(branch: str) -> None:
    problem = _branch_name_problem(branch)
    if problem is not None:
        raise _branch_name_refused(branch, problem)
    category = branch_category(branch)
    if category == "protected":
        raise GitGuardrailError(
            f"'{branch}' is a protected branch and cannot be used as a working branch."
        )
    if category == "ledger":
        raise GitGuardrailError(
            f"'{branch}' ends in '{LEDGER_SUFFIX}', which Haute keeps for each branch's "
            "saves. Choose another name."
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


def commit_parent(sha: str, cwd: Path | None = None) -> str | None:
    """The first parent of commit *sha*, or None for a root commit.

    *sha* must be a full or abbreviated hexadecimal commit id, so no ref
    syntax can reach git through it.
    """
    if not sha or any(char not in "0123456789abcdef" for char in sha):
        raise GitDomainError(f"Not a commit id: {sha!r}.")
    return _rev_parse(f"{sha}^", cwd=cwd)


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
    raw = _run_git(*args, cwd=cwd)
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
            "A git operation is in progress; finish or abort it outside Haute first."
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


# git's messages for a file Windows would not let it replace ("unlink old") or
# remove ("unlink"); LC_ALL=C keeps them fixed.
_UNLINK_FAILED = re.compile(r"unable to unlink (?:old )?'(.+?)'")


def _tracked_changes(cwd: Path | None = None) -> set[str]:
    """Tracked paths whose index or working content differs from HEAD."""
    # Read raw: each entry's two status letters may begin with a space, which a
    # stripped output would lose along with the path's first character.
    result = subprocess.run(
        ["git", "status", "--porcelain", "-z", "--untracked-files=no"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=cwd or Path.cwd(),
        env=_git_env(),
    )
    if result.returncode != 0:
        logger.warning("git_command_failed", cmd=["git", "status"], stderr=result.stderr.strip())
        raise GitError(result.stderr.strip() or "git status failed")
    tokens = result.stdout.split("\0")
    paths: set[str] = set()
    i = 0
    while i < len(tokens):
        entry = tokens[i]
        i += 1
        if len(entry) < 4:
            continue
        paths.add(entry[3:])
        if entry[0] in "RC" or entry[1] in "RC":
            i += 1  # a rename's or copy's source path follows
    return paths


def _held_files(stderr: str) -> list[str]:
    """The files git said it could not replace, in the order it said so."""
    return list(dict.fromkeys(_UNLINK_FAILED.findall(stderr)))


def _run_git_capture(*args: str, cwd: Path | None = None) -> tuple[int, str]:
    """Run a git command and return (returncode, stderr). Never raises."""
    result = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=cwd or Path.cwd(),
        env=_git_env(),
    )
    return result.returncode, result.stderr.strip()


def _rewrite_tree(*args: str, cwd: Path | None = None) -> None:
    """Run a git command that rewrites the working tree (a checkout, a fast-forward
    merge), failing loud on a file it could not write.

    On Windows git cannot replace or remove a file another program holds open. A
    checkout still exits 0, leaving that file at the old version under the new
    HEAD (or behind, untracked, when the new HEAD drops it); a fast-forward exits
    1 with the other files already written. So the files git names as not
    replaced or removed, and any tracked file that became changed, are out of
    step with HEAD: the stderr catches a file that was already changed before a
    forced checkout, or left untracked, which the status comparison cannot. HEAD
    is then put back where it was and the out-of-step files written from it; any
    still out of step record the clone interrupted, for the analyst to retry once
    the file is closed. Either way ``GitFilesInUseError`` names the files, and
    the caller's own compensation runs as for any failure."""
    _assert_no_git_op_in_progress(cwd)
    attached, head = _run_git_ok("symbolic-ref", "--short", "-q", "HEAD", cwd=cwd)
    previous = head if attached else _run_git("rev-parse", "HEAD", cwd=cwd)
    before = _tracked_changes(cwd)
    code, stderr = _run_git_capture(*args, cwd=cwd)
    reported = _held_files(stderr)
    out_of_step = sorted((_tracked_changes(cwd) - before) | set(reported))
    if not out_of_step:
        if code != 0:
            logger.warning("git_command_failed", cmd=["git", *args], stderr=stderr)
            raise GitError(stderr or f"git {args[0]} failed")
        return
    logger.warning("git_tree_out_of_step", cmd=["git", *args], files=out_of_step, stderr=stderr)
    held = reported or out_of_step
    moved_ok, now = _run_git_ok("symbolic-ref", "--short", "-q", "HEAD", cwd=cwd)
    current = now if moved_ok else _run_git("rev-parse", "HEAD", cwd=cwd)
    if current != previous:
        detach = () if attached else ("--detach",)
        if "-f" in args or "--force" in args:
            # The command was to discard changes anyway: the put-back may too,
            # and a file still held keeps what is on disk.
            _run_git_ok("checkout", "-f", *detach, previous, cwd=cwd)
        else:
            # A file git left unreplaced still holds the previous version while its
            # index entry moved on, and checkout refuses to overwrite such a "change".
            # Staging what is on disk lets checkout carry it back untouched.
            literal = [f":(literal){path}" for path in out_of_step]
            staged, _ = _run_git_ok("add", "--", *literal, cwd=cwd)
            # A file held with no sharing (Excel's open CSV) cannot even be read to
            # stage, and checkout then refuses over it. With nothing changed before
            # the command a forced put-back discards nothing, and the held file
            # keeps what is on disk.
            force = () if staged or before else ("-f",)
            _run_git_ok("checkout", *force, *detach, previous, cwd=cwd)
    left = sorted(_tracked_changes(cwd) - before)
    if left:
        _run_git_ok(
            "restore",
            "--source=HEAD",
            "--staged",
            "--worktree",
            "--",
            *[f":(literal){path}" for path in left],
            cwd=cwd,
        )
        left = sorted(_tracked_changes(cwd) - before)
    if left:
        from haute._git_state import record_interrupted

        record_interrupted(cwd or Path.cwd(), args[0], left)
    raise GitFilesInUseError(held)


def _remote_names(cwd: Path | None = None) -> list[str]:
    """Names of the configured remotes (empty when fully offline)."""
    ok, out = _run_git_ok("remote", cwd=cwd)
    return out.splitlines() if ok and out.strip() else []


def _leg_state(branch: str, remote: str, cwd: Path | None = None) -> GitRemoteLeg:
    """Divergence of one local *branch* vs ``<remote>/<branch>`` from the
    locally-known remote-tracking ref only — no fetch (a deliberate Push, Catch up or
    Spin off freshens it). Distinguishes (F2) "untracked" (never pushed to this
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
