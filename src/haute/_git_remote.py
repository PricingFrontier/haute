"""Deliberate remote synchronization and transport for Git branch pairs."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

# The command core is the sole process boundary.  Its helpers are imported
# explicitly into this domain's namespace so extracted functions retain their
# original signatures and semantics without a facade dependency.
from haute._git_core import (
    _ARCHIVE_PREFIX,
    _CLONE_TIMEOUT_SECONDS,
    _FETCH_TIMEOUT_SECONDS,
    _PUSH_TIMEOUT_SECONDS,
    LEDGER_SUFFIX,
    GitDomainError,
    GitError,
    GitPushRejectedError,
    GitTransactionError,
    _assert_git_repo,
    _assert_no_git_op_in_progress,
    _fetch_exec_lock,
    _fetch_refs,
    _get_current_branch,
    _GitProcessTimeoutError,
    _is_ancestor,
    _is_valid_full_ref_name,
    _leg_state,
    _merge_base,
    _remote_env,
    _remote_names,
    _rev_parse,
    _run_git,
    _run_git_ok,
    _run_git_process,
    _serialized_mutation,
    _should_fetch,
    _validate_managed_working_branch,
    _validate_ref_name,
    _wipe_volatile_artefacts,
    ledger_name,
)
from haute._git_setup import resolve_ledger
from haute._logging import get_logger
from haute.schemas import (
    GitBranchAwayResponse,
    GitFastForwardResponse,
    GitPushRejection,
    GitPushResponse,
    GitRemote,
    GitRemoteLeg,
    GitRemotesResponse,
)

logger = get_logger(component="git")

__all__ = [
    "ensure_remote",
    "remote_url",
    "adopt_cloned_lineage",
    "remote_has_content",
    "clone_project",
    "bundle_create",
    "bundle_verify",
    "fetch_bundle_refs",
    "pair_divergence",
    "commit_exists",
    "fetch_pair",
    "list_remotes",
    "push_working_pair",
    "fast_forward_pair",
    "fast_forward_pair_from_tracking",
    "branch_away",
]


def ensure_remote(name: str, url: str, cwd: Path | None = None) -> None:
    """Point remote *name* at *url*, adding it when absent (idempotent).

    Used by hosted project storage to bind a clone to its durable remote.
    The URL is never written into a log line: it can carry a host, a path,
    and — for a malformed user entry — credential material.
    """
    _validate_ref_name(name)
    if not url or url.strip() != url or any(char.isspace() for char in url):
        raise GitDomainError("A remote URL must be a single non-empty token without whitespace.")
    if name in _remote_names(cwd):
        _run_git("remote", "set-url", name, url, cwd=cwd)
    else:
        _run_git("remote", "add", name, url, cwd=cwd)
    logger.info("git_remote_bound", remote=name)


def remote_url(name: str, cwd: Path | None = None) -> str | None:
    """Return the configured URL for remote *name*, or ``None`` when absent."""
    _validate_ref_name(name)
    ok, url = _run_git_ok("remote", "get-url", name, cwd=cwd)
    return url.strip() if ok and url.strip() else None


def adopt_cloned_lineage(working: str, remote: str = "origin", cwd: Path | None = None) -> None:
    """Materialise a cloned project's managed lineage as local branches.

    ``git clone`` checks out only the remote's default branch, so a restored
    hosted project would hold the working branch and its ledger solely as
    remote-tracking refs — which ``_rev_parse`` does not resolve. The session
    would then report an invalid repository, show the default branch's file
    contents rather than the user's latest saves, and fail every publish with
    "working branch does not exist".

    Creates local ``working`` and (when the remote has one) its ledger, then
    checks out the ledger — the same shape :func:`set_working_branch` leaves
    behind, so a restored session is indistinguishable from an adopted one.
    """
    _validate_ref_name(working)
    _validate_ref_name(remote)
    tracking = f"refs/remotes/{remote}/{working}"
    if _rev_parse(tracking, cwd=cwd) is None:
        raise GitDomainError(
            f"The stored project does not contain branch '{working}'. "
            "Its storage binding names a branch the remote no longer has."
        )
    _run_git("branch", "--force", working, tracking, cwd=cwd)

    ledger = ledger_name(working)
    ledger_tracking = f"refs/remotes/{remote}/{ledger}"
    if _rev_parse(ledger_tracking, cwd=cwd) is not None:
        # Check out the ledger: saves commit there, and a ledger re-spawned
        # from the working tip would diverge from the published one.
        _run_git("checkout", "-B", ledger, ledger_tracking, cwd=cwd)
    else:
        _run_git("checkout", working, cwd=cwd)
    logger.info("cloned_lineage_adopted", working=working)


def remote_has_content(remote: str, cwd: Path | None = None) -> bool:
    """Whether *remote* advertises any object refs (i.e. is not an empty repo).

    Hosted binding uses this to choose between adopting the local project
    onto a fresh remote and lifting an existing project from a populated
    one. Propagates :class:`GitError` when the remote cannot be inspected
    at all — an unreachable remote must never read as "empty" and trigger
    an adopt that would publish over someone's project.
    """
    _validate_ref_name(remote)
    _, _, has_object_refs = _inspect_remote(remote, cwd=cwd)
    return has_object_refs


def clone_project(url: str, destination: Path, branch: str | None = None) -> None:
    """Clone *url* into *destination*, which must not already exist.

    The prompt-proof remote environment applies (``GIT_TERMINAL_PROMPT=0``
    plus SSH ``BatchMode``) and the transport is time-bounded, so an
    unreachable or credential-walled remote fails within
    ``_CLONE_TIMEOUT_SECONDS`` instead of wedging a hosted boot. Raw
    stderr is logged, never returned: clone stderr routinely embeds the
    remote URL and any credential the caller supplied inside it.
    """
    if destination.exists():
        raise GitDomainError(f"Clone destination already exists: {destination.name}")
    if not url or any(char.isspace() for char in url):
        raise GitDomainError("A remote URL must be a single non-empty token without whitespace.")
    if branch is not None:
        _validate_ref_name(branch)

    cmd = ["git", "clone"]
    if branch is not None:
        cmd.extend(["--branch", branch])
    cmd.extend([url, str(destination)])
    try:
        result = _run_git_process(
            *cmd[1:],
            env=_remote_env(),
            timeout=_CLONE_TIMEOUT_SECONDS,
        )
    except _GitProcessTimeoutError as exc:
        logger.warning("git_clone_timeout", seconds=_CLONE_TIMEOUT_SECONDS)
        raise GitError(
            f"The remote did not respond within {int(_CLONE_TIMEOUT_SECONDS)} seconds."
        ) from exc
    except (OSError, UnicodeError) as exc:
        logger.warning("git_clone_failed", error=str(exc))
        raise GitError("The clone could not be started.") from exc
    if result.returncode != 0:
        logger.warning("git_clone_failed", stderr=result.stderr.strip())
        raise GitError("The remote could not be cloned.")


@_serialized_mutation
def bundle_create(destination: Path, cwd: Path | None = None) -> str:
    """Write the whole repository — every ref and its history — to *destination*.

    A bundle is git's own single-file repository interchange format and
    ``git clone`` reads one directly, which is what lets hosted storage
    mirror a repository over a channel git cannot speak (the Files API).
    ``--all`` carries every branch, tag, and ``HEAD``, so each bundle is
    independently complete. Runs under the repository mutation lock so the
    snapshot can never capture a save mid-commit. Returns the bundled
    ``HEAD`` commit so the caller can label the artefact.
    """
    _assert_git_repo(cwd)
    _run_git("bundle", "create", str(destination), "--all", cwd=cwd)
    sha = _rev_parse("HEAD", cwd=cwd)
    if sha is None:  # pragma: no cover - _assert_git_repo guarantees a HEAD
        raise GitError("bundled HEAD did not resolve")
    return sha


def bundle_verify(bundle: Path, cwd: Path | None = None) -> None:
    """Prove *bundle* is a readable, self-contained bundle.

    Hosted storage's bundle is the only durable copy of the project, so it
    must be verified before it is trusted. ``git bundle verify`` checks the
    format and that every prerequisite is present — a ``--all`` bundle has
    none, so a pass means the file stands alone. Needs a repository for
    context (*cwd*); any repository will do.
    """
    _run_git("bundle", "verify", str(bundle), cwd=cwd)


def fetch_bundle_refs(bundle: Path, namespace: str, cwd: Path | None = None) -> None:
    """Populate ``refs/remotes/<namespace>/*`` from *bundle*'s branches.

    Deliberately configures NO git remote: fetching straight from a bundle
    path fills the tracking namespace and leaves ``git remote`` untouched,
    so ``_canonical_remote`` still picks ``origin`` as the one divergence
    baseline. The tracking refs outlive the (temporary) bundle file, which
    is all an upstream comparison needs. Time-bounded like a clone; stderr
    is logged, never returned.
    """
    _validate_ref_name(namespace)
    if not bundle.exists():
        raise GitDomainError("The stored project bundle to compare against is missing.")
    try:
        result = _run_git_process(
            "fetch",
            str(bundle),
            f"+refs/heads/*:refs/remotes/{namespace}/*",
            cwd=cwd or Path.cwd(),
            env=_remote_env(),
            timeout=_CLONE_TIMEOUT_SECONDS,
        )
    except _GitProcessTimeoutError as exc:
        logger.warning("git_bundle_fetch_timeout", seconds=_CLONE_TIMEOUT_SECONDS)
        raise GitError(
            f"Reading the stored project took longer than {int(_CLONE_TIMEOUT_SECONDS)} seconds."
        ) from exc
    except (OSError, UnicodeError) as exc:
        logger.warning("git_bundle_fetch_failed", error=str(exc))
        raise GitError("The stored project bundle could not be read.") from exc
    if result.returncode != 0:
        logger.warning("git_bundle_fetch_failed", stderr=result.stderr.strip())
        raise GitError("The stored project bundle could not be read.")


def pair_divergence(
    namespace: str, project_root: Path, cwd: Path | None = None
) -> tuple[GitRemoteLeg, GitRemoteLeg]:
    """The working branch's and ledger's divergence vs ``<namespace>/<branch>``.

    Reads locally-known tracking refs only — the caller decides how they
    were freshened (a fetch from a remote, or from a bundle).
    """
    from haute._git_state import read_working_branch

    _validate_ref_name(namespace)
    working = read_working_branch(project_root)
    if working is None:
        raise GitDomainError("No working branch is set for this clone.")
    ledger = ledger_name(working)
    return (
        _leg_state(working, namespace, cwd=cwd),
        _leg_state(ledger, namespace, cwd=cwd),
    )


def commit_exists(sha: str, cwd: Path | None = None) -> bool:
    """Whether *sha* names a commit object present in this repository.

    Hosted storage uses this to decide whether an existing clone derives
    from the durable location's published tip (the tip resolves locally)
    or the location has moved on without it (it does not).
    """
    _validate_ref_name(sha)
    return _rev_parse(f"{sha}^{{commit}}", cwd=cwd) is not None


@_serialized_mutation
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
    vs that remote (F6), using only locally-known remote-tracking refs. Merely
    opening or refreshing the panel never performs network I/O."""
    from haute._git_state import read_working_branch

    _assert_git_repo(cwd)
    working = read_working_branch(project_root)
    remotes: list[GitRemote] = []
    for name in _remote_names(cwd):
        ok_url, url = _run_git_ok("remote", "get-url", name, cwd=cwd)
        working_leg: GitRemoteLeg | None = None
        ledger_leg: GitRemoteLeg | None = None
        if working:
            working_leg = _leg_state(working, name, cwd=cwd)
            ledger_leg = _leg_state(ledger_name(working), name, cwd=cwd)
        remotes.append(
            GitRemote(
                name=name,
                url=_redact_remote_url(url) if ok_url and url.strip() else None,
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
    try:
        result = _run_git_process(
            "ls-remote",
            "--tags",
            remote,
            "refs/tags/version/*",
            cwd=cwd or Path.cwd(),
            env=_remote_env(),
            timeout=_FETCH_TIMEOUT_SECONDS,
        )
    except (_GitProcessTimeoutError, OSError, UnicodeError):
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


def _inspect_remote(remote: str, cwd: Path | None = None) -> tuple[set[str], str | None, bool]:
    """Strictly inspect one remote; empty means a successful zero-object advertisement."""
    try:
        result = _run_git_process(
            "ls-remote",
            "--symref",
            remote,
            cwd=cwd or Path.cwd(),
            env=_remote_env(),
            timeout=_FETCH_TIMEOUT_SECONDS,
        )
    except _GitProcessTimeoutError as exc:
        raise GitError("git ls-remote timed out") from exc
    except (OSError, UnicodeError) as exc:
        raise GitError("git ls-remote failed") from exc
    if result.returncode != 0:
        logger.warning("git_remote_inspection_failed", remote=remote, stderr=result.stderr.strip())
        raise GitError(result.stderr.strip() or "git ls-remote failed")
    heads: set[str] = set()
    head_target: str | None = None
    has_object_refs = False
    object_refs: dict[str, str] = {}
    object_id_width: int | None = None
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 2:
            raise GitError("malformed git ls-remote advertisement")
        value, ref = parts
        if value.startswith("ref: "):
            if ref != "HEAD" or not value.startswith("ref: refs/heads/"):
                raise GitError("malformed git ls-remote advertisement")
            target_ref = value[len("ref: ") :]
            target = target_ref[len("refs/heads/") :]
            if not _is_valid_full_ref_name(target_ref) or (
                head_target is not None and head_target != target
            ):
                raise GitError("malformed git ls-remote advertisement")
            head_target = target
        elif re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", value):
            object_id = value.lower()
            if not object_id.strip("0"):
                raise GitError("malformed git ls-remote advertisement")
            if object_id_width is None:
                object_id_width = len(object_id)
            elif len(object_id) != object_id_width:
                raise GitError("malformed git ls-remote advertisement")
            peeled = ref.endswith("^{}")
            canonical_ref = ref[:-3] if peeled else ref
            if ref != "HEAD" and (
                not _is_valid_full_ref_name(canonical_ref)
                or (peeled and not canonical_ref.startswith("refs/tags/"))
            ):
                raise GitError("malformed git ls-remote advertisement")
            previous = object_refs.get(ref)
            if previous is not None and previous != object_id:
                raise GitError("malformed git ls-remote advertisement")
            object_refs[ref] = object_id
            has_object_refs = True
            if canonical_ref.startswith("refs/heads/"):
                name = ref[len("refs/heads/") :]
                heads.add(name)
        else:
            raise GitError("malformed git ls-remote advertisement")
    if head_target is not None:
        head_object = object_refs.get("HEAD")
        target_object = object_refs.get(f"refs/heads/{head_target}")
        if head_object is not None and target_object is not None and head_object != target_object:
            raise GitError("malformed git ls-remote advertisement")
    return heads, head_target, has_object_refs


def _local_unmanaged_bases(working: str, ledger: str, cwd: Path | None = None) -> set[str]:
    ok, raw = _run_git_ok("for-each-ref", "--format=%(refname:lstrip=2)", "refs/heads/", cwd=cwd)
    if not ok:
        raise GitError("could not list local branches")
    names = {name.strip() for name in raw.splitlines() if name.strip()}
    return {
        name
        for name in names
        if name not in {working, ledger}
        and not name.endswith(LEDGER_SUFFIX)
        and not name.startswith(f"{_ARCHIVE_PREFIX}/")
        and f"{name}{LEDGER_SUFFIX}" not in names
        and _rev_parse(f"refs/heads/{name}", cwd=cwd) is not None
    }


def _resolve_push_default(
    heads: set[str],
    symbolic_head: str | None,
    has_object_refs: bool,
    working: str,
    ledger: str,
    remote: str,
    cwd: Path | None = None,
) -> str:
    local_bases = _local_unmanaged_bases(working, ledger, cwd=cwd)
    if has_object_refs:
        if symbolic_head is not None:
            if symbolic_head in {working, ledger}:
                raise GitDomainError(
                    "The remote default cannot be the working branch or save ledger."
                )
            if symbolic_head not in heads:
                raise GitDomainError("The remote's default branch is missing or dangling.")
            return symbolic_head
        # A clone need not have a local ``main``/``master`` head after it has
        # checked out only a working branch.  The selected remote's tracking
        # ref is still an authoritative local baseline, but refs belonging to
        # another remote must never influence this decision.
        remote_base_names = {
            name
            for name in heads
            if name not in {working, ledger}
            and not name.endswith(LEDGER_SUFFIX)
            and not name.startswith(f"{_ARCHIVE_PREFIX}/")
            and f"{name}{LEDGER_SUFFIX}" not in heads
        }
        selected_remote_bases = {
            name
            for name in remote_base_names
            if _rev_parse(f"refs/remotes/{remote}/{name}", cwd=cwd) is not None
        }
        matches = remote_base_names & (local_bases | selected_remote_bases)
        # A real local canonical base is an explicit expectation, not merely a
        # weak preference: an established remote missing it is unsafe to guess
        # around by selecting a different branch.
        if "main" in local_bases:
            if "main" not in heads:
                raise GitDomainError(
                    "The expected local default branch 'main' is missing on the remote."
                )
            return "main"
        if "master" in local_bases:
            if "master" not in heads:
                raise GitDomainError(
                    "The expected local default branch 'master' is missing on the remote."
                )
            return "master"
        for name in ("main", "master"):
            if name in matches:
                return name
        if len(matches) == 1:
            return next(iter(matches))
        raise GitDomainError("Could not determine the remote default branch safely.")
    if symbolic_head in {working, ledger}:
        raise GitDomainError("The remote default cannot be the working branch or save ledger.")
    if symbolic_head is not None and symbolic_head in local_bases:
        return symbolic_head
    for name in ("main", "master"):
        if name in local_bases:
            return name
    if len(local_bases) == 1:
        return next(iter(local_bases))
    raise GitDomainError("Could not determine a local default branch for remote bootstrap.")


def _fetch_expected_default(remote: str, branch: str, cwd: Path | None = None) -> str:
    destination = f"refs/remotes/{remote}/{branch}"
    try:
        with _fetch_exec_lock:
            result = _run_git_process(
                "fetch",
                "--no-tags",
                remote,
                f"refs/heads/{branch}:{destination}",
                "--quiet",
                cwd=cwd or Path.cwd(),
                env=_remote_env(),
                timeout=_FETCH_TIMEOUT_SECONDS,
            )
    except _GitProcessTimeoutError as exc:
        raise GitError("git fetch timed out") from exc
    except (OSError, UnicodeError) as exc:
        raise GitError("git fetch failed") from exc
    if result.returncode != 0:
        logger.warning(
            "git_default_fetch_failed", remote=remote, branch=branch, stderr=result.stderr.strip()
        )
        raise GitError(result.stderr.strip() or "git fetch failed")
    sha = _rev_parse(destination, cwd=cwd)
    if sha is None:
        raise GitError("fetched default branch did not resolve")
    return sha


@_serialized_mutation
def push_working_pair(remote: str, project_root: Path, cwd: Path | None = None) -> GitPushResponse:
    """Deliberately publish managed history to an existing *remote* atomically.

    A successfully inspected empty remote receives the resolved default branch
    plus the working branch and optional ledger under a create-only default-ref
    lease. An established remote receives only the working/ledger pair after its
    default has been validated. No existing remote ref is ever force-updated.
    """
    from haute._git_state import read_working_branch

    _assert_git_repo(cwd)
    _validate_ref_name(remote)
    if remote not in _remote_names(cwd):
        raise GitDomainError(f"No remote named '{remote}' is configured.")

    working = read_working_branch(project_root)
    if working is None:
        raise GitDomainError("No working branch is set for this clone — nothing to push.")
    _validate_managed_working_branch(working, cwd=cwd)
    working_sha = _rev_parse(f"refs/heads/{working}", cwd=cwd)
    if working_sha is None:
        raise GitDomainError(f"Working branch '{working}' does not exist.")
    ledger = ledger_name(working)
    ledger_sha = _rev_parse(f"refs/heads/{ledger}", cwd=cwd)
    ledger_exists = ledger_sha is not None
    advertised_heads, symbolic_head, has_object_refs = _inspect_remote(remote, cwd=cwd)
    default = _resolve_push_default(
        advertised_heads, symbolic_head, has_object_refs, working, ledger, remote, cwd=cwd
    )
    bootstrapping = not has_object_refs
    default_sha: str | None = None

    if bootstrapping:
        default_sha = _rev_parse(f"refs/heads/{default}", cwd=cwd)
        if default_sha is None:
            raise GitDomainError("The local default and working branches must resolve before push.")
        related = [default_sha, working_sha] + ([ledger_sha] if ledger_sha is not None else [])
        if any(_merge_base(related[0], sha, cwd=cwd) is None for sha in related[1:]):
            raise GitDomainError("The local default and working history are unrelated.")
    else:
        remote_default = _fetch_expected_default(remote, default, cwd=cwd)
        related = [working_sha] + ([ledger_sha] if ledger_sha is not None else [])
        if any(_merge_base(remote_default, sha, cwd=cwd) is None for sha in related):
            raise GitDomainError("The remote default and local working history are unrelated.")

    # X4: version labels are canonical org-wide (one `version/<label>` per
    # release). Pre-check for a label already on the remote at a DIFFERENT object
    # and refuse with a friendly message before the push, rather than letting it
    # surface as a raw atomic-push rejection (best-effort: an unreachable remote
    # skips the check and git's own tag-reject backstops a real collision).
    collisions = _tag_collisions(remote, working_sha, cwd=cwd)
    if collisions:
        labels = ", ".join(sorted(c[len("version/") :] for c in collisions))
        plural = "s" if len(collisions) > 1 else ""
        raise GitDomainError(
            f"Version label{plural} ({labels}) already exist on '{remote}' pointing "
            "at a different version. Each release name is shared across the team — "
            "pick a different label, or coordinate with whoever published it."
        )

    # Include the ledger only when it has been spawned. Bootstrap's empty-value
    # lease is compare-and-create only; no existing ref can be force-updated (S33).
    # --follow-tags carries the annotated version/<label> tags reachable from the
    # pushed commits (X4: labels travel with the work they mark).
    refspecs: list[str] = []
    if bootstrapping:
        if default_sha is None:  # guarded above; keep the snapshot invariant explicit
            raise GitError("default branch snapshot is missing")
        refspecs.append(f"{default_sha}:refs/heads/{default}")
    refspecs.append(f"{working_sha}:refs/heads/{working}")
    if ledger_sha is not None:
        refspecs.append(f"{ledger_sha}:refs/heads/{ledger}")

    cmd = ["git", "push", "--atomic", "--follow-tags"]
    if bootstrapping:
        cmd.append(f"--force-with-lease=refs/heads/{default}:")
    cmd.extend([remote, *refspecs])
    try:
        result = _run_git_process(
            *cmd[1:],
            errors="replace",
            cwd=cwd or Path.cwd(),
            env=_remote_env(),
            timeout=_PUSH_TIMEOUT_SECONDS,
        )
    except _GitProcessTimeoutError as exc:
        logger.warning("git_push_timed_out", remote=remote, refs=refspecs)
        raise GitError("git push timed out") from exc
    except OSError as exc:
        logger.warning("git_push_launch_failed", remote=remote, refs=refspecs, error=str(exc))
        raise GitError("git push failed") from exc
    if result.returncode != 0:
        stderr = result.stderr.strip()
        logger.warning("git_push_failed", remote=remote, refs=refspecs, stderr=stderr)
        if not bootstrapping and any(
            s in stderr for s in ("non-fast-forward", "fetch first", "[rejected]")
        ):
            # M7: a rejection is the moment we KNOW we're diverged — turn it into
            # the data-bearing fork the UI needs, not a generic dead-end string.
            raise _push_rejection(remote, working, ledger, project_root, cwd=cwd)
        raise GitError(stderr or "git push failed")

    pushed = ([default] if bootstrapping else []) + [working] + ([ledger] if ledger_exists else [])
    # X3 robustness (§6.8): record the tips we just published so rewrite detection
    # survives a pruned reflog (keyed <remote>/<ref>).
    from haute._git_state import record_pushed_shas

    pushed_shas = {f"{remote}/{working}": working_sha}
    if ledger_sha is not None:
        pushed_shas[f"{remote}/{ledger}"] = ledger_sha
    record_pushed_shas(project_root, pushed_shas)

    logger.info("pushed_working_pair", remote=remote, branches=pushed)
    return GitPushResponse(
        remote=remote,
        working_branch=working,
        ledger_branch=ledger,
        default_branch=default,
        bootstrapped_default=bootstrapping,
        pushed_refs=pushed,
    )


@_serialized_mutation
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
    watcher for the tree replacement (M4).

    This half is only "refresh the namespace": the configured-remote check
    and the fetch. Every safety check and the transaction itself live in
    :func:`fast_forward_pair_from_tracking`, which the upstream catch-up
    path shares."""
    _assert_git_repo(cwd)
    _assert_no_git_op_in_progress(cwd)
    _validate_ref_name(remote)
    if remote not in _remote_names(cwd):
        raise GitDomainError(f"No remote named '{remote}' is configured.")

    # Fetch + prune the configured namespace so the catch-up decision is on an
    # authoritative snapshot. A missing remote leg is a distinct domain state,
    # not a transport failure, and a deleted tracking ref cannot remain stale.
    with _fetch_exec_lock:
        refreshed = _fetch_refs(remote, cwd=cwd)
    if not refreshed:
        raise GitDomainError(
            f"Could not refresh '{remote}'. Check your connection or credentials and try again."
        )
    return fast_forward_pair_from_tracking(remote, project_root, cwd=cwd)


@_serialized_mutation
def fast_forward_pair_from_tracking(
    namespace: str,
    project_root: Path,
    cwd: Path | None = None,
    *,
    source_label: str | None = None,
) -> GitFastForwardResponse:
    """Apply a fast-forward from ALREADY-REFRESHED ``refs/remotes/<namespace>/*``.

    The safety half of :func:`fast_forward_pair`, shared verbatim with the
    upstream catch-up path so a fork's catch-up cannot drift from a
    remote's: HEAD-on-ledger, dirty-tree refusal, both legs present and
    neither ahead/diverged, the volatile-cache wipe, and the rollback. The
    caller owns how the namespace was freshened — a fetch from a configured
    remote, or from a downloaded bundle. *source_label* names that source
    in user-facing messages when it is not the namespace itself.
    """
    from haute._git_state import read_working_branch

    _assert_git_repo(cwd)
    _assert_no_git_op_in_progress(cwd)
    _validate_ref_name(namespace)
    remote = namespace
    label = source_label if source_label is not None else namespace

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

    for branch, kind in ((working, "working branch"), (ledger, "save ledger")):
        tracking = f"refs/remotes/{remote}/{branch}"
        if _rev_parse(tracking, cwd=cwd) is None:
            raise GitDomainError(
                f"Can't catch up from '{label}': {kind} '{branch}' is missing on the remote."
            )
    w_leg = _leg_state(working, remote, cwd=cwd)
    l_leg = _leg_state(ledger, remote, cwd=cwd)

    if any(leg.status in ("ahead", "diverged") for leg in (w_leg, l_leg)):
        # A fork's dead end reads differently from a remote's: nothing is
        # "the remote's" to a user who forked, and naming both sides is the
        # whole point. Default text stays byte-identical for the remote path.
        if source_label is None:
            raise GitDomainError(
                "Can't catch up — you have local changes the remote doesn't have. Spin "
                "off a copy to keep them, then reconcile."
            )
        raise GitDomainError(
            f"Can't catch up — both this project and {label} have changed since "
            "the fork, so there is no clean fast-forward. Spin off a copy to keep "
            "your work, then reconcile."
        )
    if w_leg.status != "behind" and l_leg.status != "behind":
        raise GitDomainError(f"Already up to date with '{label}'.")

    # Volatile caches must not survive into the caught-up tree (S12).
    _wipe_volatile_artefacts(cwd or Path.cwd())

    fast_forwarded: list[str] = []
    old_ledger = _rev_parse(ledger, cwd=cwd)
    old_working = _rev_parse(working, cwd=cwd)
    ledger_advanced = False
    working_advanced = False
    try:
        # Ledger first (it's HEAD; merge --ff-only advances it and the working tree).
        if l_leg.status == "behind":
            _run_git("merge", "--ff-only", f"refs/remotes/{remote}/{ledger}", cwd=cwd)
            ledger_advanced = True
            fast_forwarded.append(ledger)
        # Working ref (not checked out): CAS-advance it to its remote tip.
        if w_leg.status == "behind":
            target = _rev_parse(f"refs/remotes/{remote}/{working}", cwd=cwd)
            if old_working is None or target is None:
                raise GitError("could not resolve refs for the working-branch fast-forward")
            _run_git("update-ref", f"refs/heads/{working}", target, old_working, cwd=cwd)
            working_advanced = True
            fast_forwarded.append(working)
    except GitError as exc:
        restored = True
        if working_advanced and old_working is not None:
            ok, _ = _run_git_ok(
                "update-ref",
                f"refs/heads/{working}",
                old_working,
                cwd=cwd,
            )
            restored &= ok
        if ledger_advanced and old_ledger is not None:
            ok, _ = _run_git_ok("reset", "--hard", old_ledger, cwd=cwd)
            restored &= ok
        if not restored:
            raise GitTransactionError(
                "Catch-up failed and automatic rollback was incomplete. Inspect the "
                "repository before retrying."
            ) from exc
        raise

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
) -> bool:
    """Undo a partially-applied branch-away and report complete success."""
    succeeded = True
    if created_l:
        ok, _ = _run_git_ok("branch", "-D", ledger, cwd=cwd)
        succeeded &= ok
    if created_w:
        ok, _ = _run_git_ok("branch", "-D", working, cwd=cwd)
        succeeded &= ok
    if renamed_l:
        ok, _ = _run_git_ok("branch", "-m", aside_ledger, ledger, cwd=cwd)
        succeeded &= ok
    if renamed_w:
        ok, _ = _run_git_ok("branch", "-m", aside, working, cwd=cwd)
        succeeded &= ok
    ok, _ = _run_git_ok("checkout", ledger, cwd=cwd)
    return succeeded and ok


@_serialized_mutation
def branch_away(remote: str, project_root: Path, cwd: Path | None = None) -> GitBranchAwayResponse:
    """M3: resolve a remote fork by setting the local pair aside under a dated name
    and repointing the canonical name to the remote's tips — both lineages
    preserved, the baton intact, zero rewrites (the never-merge-locally escape).

    The canonical name keeps tracking the SHARED line (decision: shared line keeps
    the name); the local divergent work is preserved under ``<W>-local-<date>``
    (S35: surfaced, never silent). NOT the move-mode rewind — no ref is ever wound
    back. ``oL`` absent (X2) → repoint only ``W`` and let the ledger respawn at the
    refreshed tip. Atomic with rollback; the caller pauses the watcher (M4)."""
    from haute._git_state import read_working_branch, write_working_branch

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
        refreshed = _fetch_refs(remote, cwd=cwd)
    if not refreshed:
        raise GitDomainError(
            f"Could not refresh '{remote}'. Check your connection or credentials and try again."
        )
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
    except (GitError, OSError) as exc:
        restored = _rollback_branch_away(
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
        if not restored:
            raise GitTransactionError(
                "Spinning off the local branch failed and automatic rollback was "
                "incomplete. Inspect the repository before retrying."
            ) from exc
        raise

    logger.info("branched_away", working=working, set_aside=aside, remote=remote)
    return GitBranchAwayResponse(working_branch=working, set_aside_as=aside)
