"""Deliberate remote synchronization and transport for Git branch pairs."""

from __future__ import annotations

import re
from pathlib import Path

from haute._failure_answers import record_incident
from haute._git_core import (
    _ARCHIVE_PREFIX,
    _CLONE_TIMEOUT_SECONDS,
    _FETCH_TIMEOUT_SECONDS,
    _PUSH_TIMEOUT_SECONDS,
    LABELS_NAMESPACE,
    LEDGER_SUFFIX,
    SAFE_FETCH,
    GitBootstrapIncompleteError,
    GitDomainError,
    GitError,
    GitPushRejectedError,
    GitRemoteUnavailableError,
    _assert_git_repo,
    _assert_no_git_op_in_progress,
    _assert_way_clear,
    _fetch_exec_lock,
    _fetch_refs,
    _free_pair_name,
    _get_current_branch,
    _GitProcessResult,
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
    _validate_managed_working_branch,
    _validate_ref_name,
    label_refspec,
    ledger_name,
)
from haute._git_operation import Operation
from haute._logging import get_logger

# The command core is the sole process boundary.  Its helpers are imported
# explicitly into this domain's namespace so extracted functions retain their
# original signatures and semantics without a facade dependency.
from haute._project_context import current_project
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
    "remote_push_urls",
    "adopt_cloned_lineage",
    "remote_is_empty",
    "remote_holds_other_history",
    "clone_project",
    "bundle_create",
    "bundle_verify",
    "fetch_bundle_refs",
    "pair_divergence",
    "commit_exists",
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


def remote_push_urls(name: str, cwd: Path | None = None) -> list[str]:
    """Where a push to remote *name* lands: its ``pushurl`` entries, else its URL.

    ``git remote get-url --push --all``, with ``insteadOf`` and ``pushInsteadOf``
    applied; empty when the remote is absent.
    """
    _validate_ref_name(name)
    ok, urls = _run_git_ok("remote", "get-url", "--push", "--all", name, cwd=cwd)
    return [url.strip() for url in urls.splitlines() if url.strip()] if ok else []


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
    ledger = ledger_name(working)
    ledger_tip = _rev_parse(f"refs/remotes/{remote}/{ledger}", cwd=cwd)
    adoption = Operation("adopt-clone", cwd or current_project().root, cwd)
    updates: dict[str, str | None] = {f"refs/heads/{working}": _rev_parse(tracking, cwd=cwd)}
    if ledger_tip is not None:
        updates[f"refs/heads/{ledger}"] = ledger_tip
    adoption.refs(updates)
    # Check out the ledger: saves commit there, and a ledger re-spawned from the
    # working tip would diverge from the published one.
    adoption.checkout(ledger if ledger_tip is not None else working)
    adoption.run()
    logger.info("cloned_lineage_adopted", working=working)


def remote_is_empty(remote: str, cwd: Path | None = None) -> bool:
    """Whether *remote* advertises no object ref, the state in which a Push bootstraps it.

    The strict advertisement read :func:`push_working_pair` makes; an inspection failure
    propagates as :class:`GitError`. The Push itself inspects again and owns the decision.
    """
    _validate_ref_name(remote)
    _, _, has_object_refs = _inspect_remote(remote, cwd=cwd)
    return not has_object_refs


def remote_holds_other_history(remote: str, project_root: Path, cwd: Path | None = None) -> bool:
    """Whether *remote* holds history other than what this clone bootstraps there.

    Hosted binding uses this to choose between adopting the local project onto
    the remote and lifting an existing project from it. ``False`` for an empty
    remote, and for the one state an interrupted bootstrap Push leaves behind:
    the remote's only object ref is the default branch this clone bootstraps, at
    exactly the local default's tip (an advertised ``HEAD`` must agree). Any other
    branch, tag or tip is someone's history. Propagates :class:`GitError` when the
    remote cannot be inspected at all — an unreachable remote must never read as
    adoptable and trigger an adopt that would publish over someone's project.
    """
    from haute._git_state import read_working_branch

    _validate_ref_name(remote)
    _, head_target, object_refs = _read_advertisement(remote, cwd=cwd)
    if not object_refs:
        return False
    # An object-valued HEAD with no branch beside it (hidden refs) is still content.
    refs = {ref: sha for ref, sha in object_refs.items() if ref != "HEAD"}
    working = read_working_branch(project_root)
    if working is None or len(refs) != 1:
        return True
    ((ref, sha),) = refs.items()
    if object_refs.get("HEAD", sha) != sha:
        return True
    try:
        default = _resolve_push_default(
            set(), head_target, False, working, ledger_name(working), remote, cwd=cwd
        )
    except GitDomainError:
        # No unambiguous default of our own to bootstrap, so the ref is not ours.
        return True
    return ref != f"refs/heads/{default}" or _rev_parse(f"refs/heads/{default}", cwd=cwd) != sha


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

    args = ("clone", *(("--branch", branch) if branch is not None else ()), url, str(destination))
    try:
        result = _run_git_process(
            *args,
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
    """Populate ``refs/remotes/<namespace>/*`` from *bundle*'s branches, and its
    version labels into ``LABELS_NAMESPACE``.

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
            *SAFE_FETCH,
            str(bundle),
            f"+refs/heads/*:refs/remotes/{namespace}/*",
            label_refspec(namespace),
            cwd=cwd or current_project().root,
            env=_remote_env(cwd),
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


def labels_to_adopt(namespace: str, tip: str, cwd: Path | None = None) -> dict[str, str | None]:
    """The version labels a fetch for *namespace* brought (``LABELS_NAMESPACE``) on
    *tip*'s history that the clone has no label of that name for, as the ref updates
    creating them. A label the clone already has is left as it is."""
    prefix = f"{LABELS_NAMESPACE}/{namespace}/"
    fetched = _run_git(
        "for-each-ref", "--format=%(refname)%00%(objectname)", f"--merged={tip}", prefix, cwd=cwd
    )
    held = set(
        _run_git("for-each-ref", "--format=%(refname)", "refs/tags/version/", cwd=cwd).split()
    )
    adopt: dict[str, str | None] = {}
    for line in fetched.splitlines():
        ref, label_object = line.split("\0")
        label = "refs/tags/" + ref.removeprefix(prefix)
        if label not in held:
            adopt[label] = label_object
    return adopt


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

    Fetch the remote's namespace once — *forced* past the demand throttle, because
    a rejection is authoritative, not a poll, and in the namespace form, so a pair
    leg the remote lacks reads as a missing tracking ref instead of failing the
    whole fetch — then recompute both legs so the payload shows
    the live fork. ``--atomic`` means a fast-forwardable leg is rejected
    alongside a non-FF one, so the message names the **blocking** leg(s) (the ones
    the remote has moved ahead on), reconciling with the per-leg counts rather
    than blaming whichever ref git happened to print (M6). When the remote dropped
    a commit we published (X3), the message says so distinctly and points at the
    person-reconciles off-ramp. A failed fetch degrades to the last-known tracking
    refs — still honest, never a hang (F1)."""
    with _fetch_exec_lock:
        _fetch_refs(remote, cwd=cwd)
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
            cwd=cwd or current_project().root,
            env=_remote_env(cwd),
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


def _refresh_failed(remote: str) -> GitRemoteUnavailableError:
    """A deliberate read of *remote* that failed: the words Push, Catch up and Spin off share."""
    return GitRemoteUnavailableError(
        f"Could not refresh '{remote}'. Check your connection or credentials and try again."
    )


#: What git's own client answers, before it sends a ref, when a host takes no atomic push
#: (Azure Repos): in English, since every git command runs under ``_GIT_LOCALE_ENV``.
_NOT_ATOMIC = "the receiving end does not support --atomic push"


def _could_not_push(remote: str) -> GitRemoteUnavailableError:
    return GitRemoteUnavailableError(
        f"Could not push to '{remote}'. Check your connection or credentials and try again."
    )


def _branch_not_sent(remote: str, working: str) -> str:
    """A working branch that could not be sent after its ledger landed in the same Push."""
    return (
        f"Your save history is on '{remote}', but the branch '{working}' could not be "
        "pushed. Check your connection or credentials and push again to finish."
    )


def _push_failed(remote: str, stderr: str, *, unpublished: str | None = None) -> GitDomainError:
    """Why a push failed for something other than diverged history, in the analyst's words.

    *unpublished* is the working branch when its ledger landed in this Push before it, on a
    host without atomic pushes: nothing then says that nothing was published. A failure
    recognised by none of git's words is not put down to the connection alone: its incident
    holds what git said."""
    if "(stale info)" in stderr:
        # The bootstrap's create-only lease: the default appeared since inspection. The
        # next push finds an established remote, so this passes like a transport failure.
        return GitRemoteUnavailableError(
            f"'{remote}' changed while Haute was publishing to it, so nothing was published. "
            "Push again."
        )
    if "[remote rejected]" in stderr:
        incident = record_incident(event="git_push_rejected", remote=remote, stderr=stderr)
        refused = (
            f"'{remote}' refused the push, so nothing was published"
            if unpublished is None
            else f"'{remote}' took your save history but refused the branch '{unpublished}', "
            "so the branch is not published yet"
        )
        return GitDomainError(
            f"{refused} and your work is unchanged. Its reason, for example a branch rule or "
            f"missing write access, is recorded under incident {incident}."
        )
    incident = record_incident(event="git_push_unrecognised", remote=remote, stderr=stderr)
    said = (
        str(_could_not_push(remote))
        if unpublished is None
        else _branch_not_sent(remote, unpublished)
    )
    return GitRemoteUnavailableError(
        f"{said} Git's own message is recorded under incident {incident}."
    )


def _inspect_remote(remote: str, cwd: Path | None = None) -> tuple[set[str], str | None, bool]:
    """Strictly inspect one remote; empty means a successful zero-object advertisement."""
    heads, head_target, object_refs = _read_advertisement(remote, cwd=cwd)
    return heads, head_target, bool(object_refs)


def _read_advertisement(
    remote: str, cwd: Path | None = None
) -> tuple[set[str], str | None, dict[str, str]]:
    """The strict ``ls-remote --symref`` parse: branch names, HEAD target, object refs."""
    try:
        result = _run_git_process(
            "ls-remote",
            "--symref",
            remote,
            cwd=cwd or current_project().root,
            env=_remote_env(cwd),
            timeout=_FETCH_TIMEOUT_SECONDS,
        )
    except _GitProcessTimeoutError as exc:
        logger.warning("git_remote_inspection_timed_out", remote=remote)
        raise _refresh_failed(remote) from exc
    except OSError as exc:
        logger.warning("git_remote_inspection_failed", remote=remote, error=str(exc))
        raise _refresh_failed(remote) from exc
    except UnicodeError as exc:
        raise GitError("unreadable git ls-remote advertisement") from exc
    if result.returncode != 0:
        logger.warning("git_remote_inspection_failed", remote=remote, stderr=result.stderr.strip())
        raise _refresh_failed(remote)
    heads: set[str] = set()
    head_target: str | None = None
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
    return heads, head_target, object_refs


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
                *SAFE_FETCH,
                remote,
                f"refs/heads/{branch}:{destination}",
                "--quiet",
                errors="replace",
                cwd=cwd or current_project().root,
                env=_remote_env(cwd),
                timeout=_FETCH_TIMEOUT_SECONDS,
            )
    except _GitProcessTimeoutError as exc:
        logger.warning("git_default_fetch_timed_out", remote=remote, branch=branch)
        raise _refresh_failed(remote) from exc
    except OSError as exc:
        logger.warning("git_default_fetch_failed", remote=remote, branch=branch, error=str(exc))
        raise _refresh_failed(remote) from exc
    if result.returncode != 0:
        logger.warning(
            "git_default_fetch_failed", remote=remote, branch=branch, stderr=result.stderr.strip()
        )
        raise _refresh_failed(remote)
    sha = _rev_parse(destination, cwd=cwd)
    if sha is None:
        raise GitError("fetched default branch did not resolve")
    return sha


def _run_push(
    remote: str, options: list[str], refspecs: list[str], cwd: Path | None = None
) -> _GitProcessResult[str]:
    """Run one prompt-proof, time-bounded ``git push``; timeout or launch failure raises."""
    try:
        return _run_git_process(
            "push",
            *options,
            remote,
            *refspecs,
            errors="replace",
            cwd=cwd or current_project().root,
            env=_remote_env(cwd),
            timeout=_PUSH_TIMEOUT_SECONDS,
        )
    except _GitProcessTimeoutError as exc:
        logger.warning("git_push_timed_out", remote=remote, refs=refspecs)
        raise _could_not_push(remote) from exc
    except OSError as exc:
        logger.warning("git_push_launch_failed", remote=remote, refs=refspecs, error=str(exc))
        raise _could_not_push(remote) from exc


def _create_remote_default(
    remote: str, default: str, default_sha: str, cwd: Path | None = None
) -> None:
    """Initialise an empty *remote* with its default branch, alone.

    GitHub never triggers ``push`` or ``pull_request`` workflows in a repository
    whose first push created more than one ref, atomic or not (observed 27 October
    2026), so this push carries exactly one: ``--no-follow-tags`` also overrides a
    configured ``push.followTags``. The empty-value lease keeps it a
    compare-and-create — an existing default is never advanced or replaced, and one
    already at *default_sha* is an up-to-date no-op. A refusal, including a lost
    create race (``[rejected] ... (stale info)``), is a plain :class:`GitError` and
    is never read as working/ledger divergence.
    """
    refspec = f"{default_sha}:refs/heads/{default}"
    options = ["--no-follow-tags", f"--force-with-lease=refs/heads/{default}:"]
    result = _run_push(remote, options, [refspec], cwd=cwd)
    if result.returncode != 0:
        stderr = result.stderr.strip()
        logger.warning("git_push_failed", remote=remote, refs=[refspec], stderr=stderr)
        raise _push_failed(remote, stderr)


def _bootstrap_incomplete(
    remote: str, default: str, *, refused: bool, unpublished: str | None = None
) -> GitBootstrapIncompleteError:
    """The hand-written error for a bootstrap whose default landed but whose pair did not:
    neither of the pair, or on a host without atomic pushes the working branch *unpublished*
    alone, its ledger having landed."""
    incident = record_incident(
        event="git_bootstrap_incomplete", remote=remote, branch=default, refused=refused
    )
    if unpublished is not None:
        outcome = (
            f"'{remote}' refused the branch '{unpublished}'. Nothing was overwritten and "
            "your work is unchanged. Try again; if it fails the same way, its reason (for "
            "example a branch rule or missing write access) is recorded under incident "
            f"{incident}."
            if refused
            else f"Haute could not confirm that the branch '{unpublished}' was published. "
            "Nothing was overwritten and your work is unchanged. Try again to finish."
        )
        return GitBootstrapIncompleteError(
            f"'{default}' is now on '{remote}' as your merge target and your save history "
            f"is published, but {outcome}"
        )
    if refused:
        outcome = (
            f"'{remote}' did not accept your branch and save history. Nothing was "
            "overwritten and your work is unchanged. Try again; if it fails the same "
            f"way, '{remote}' is refusing these branches (for example a branch rule or "
            f"missing write access), and its reason is recorded under incident {incident}."
        )
    else:
        outcome = (
            "Haute could not confirm that your branch and save history were published. "
            "Nothing was overwritten and your work is unchanged. Try again to finish."
        )
    return GitBootstrapIncompleteError(
        f"'{default}' is now on '{remote}' as your merge target, but {outcome}"
    )


@_serialized_mutation
def push_working_pair(remote: str, project_root: Path, cwd: Path | None = None) -> GitPushResponse:
    """Deliberately publish managed history to an existing *remote*.

    The working branch and optional ledger travel in one atomic push, or on a host that
    takes none (Azure Repos) in two ordinary ones, the ledger first. A
    successfully inspected empty remote first receives the resolved default branch
    on its own, in a single-ref create-only push (:func:`_create_remote_default`).
    An established remote receives only the pair after its default has been
    validated. No existing remote ref is ever force-updated.
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

    # Include the ledger only when it has been spawned. --follow-tags carries the
    # annotated version/<label> tags reachable from the pushed commits (X4: labels
    # travel with the work they mark). The pair push never names the default and
    # carries no lease, so no existing ref can be force-updated (S33).
    working_refspec = f"{working_sha}:refs/heads/{working}"
    ledger_refspec = None if ledger_sha is None else f"{ledger_sha}:refs/heads/{ledger}"
    refspecs = [working_refspec] + ([] if ledger_refspec is None else [ledger_refspec])

    if bootstrapping:
        if default_sha is None:  # guarded above; keep the snapshot invariant explicit
            raise GitError("default branch snapshot is missing")
        # Nothing else touches the network between the two pushes: the pair was
        # validated against default_sha above, and this publishes exactly that commit.
        _create_remote_default(remote, default, default_sha, cwd=cwd)

    from haute._git_state import record_pushed_shas

    # The working branch, once its ledger has landed in this Push ahead of it: only a host
    # without atomic pushes can leave that, and what is said of a failure then names it.
    unpublished: str | None = None
    try:
        result = _run_push(remote, ["--atomic", "--follow-tags"], refspecs, cwd=cwd)
        if result.returncode != 0 and _NOT_ATOMIC in result.stderr:
            # Git ended that push itself, before it sent a ref. The pair goes in order
            # instead, the ledger first: a ledger without its branch is the one half-way
            # state there can be, which nothing opens and the next Push completes.
            logger.info("git_push_host_not_atomic", remote=remote)
            if ledger_refspec is not None and ledger_sha is not None:
                result = _run_push(remote, ["--follow-tags"], [ledger_refspec], cwd=cwd)
                if result.returncode == 0:
                    record_pushed_shas(project_root, {f"{remote}/{ledger}": ledger_sha})
                    unpublished = working
            if ledger_refspec is None or unpublished is not None:
                result = _run_push(remote, ["--follow-tags"], [working_refspec], cwd=cwd)
    except GitError as exc:
        if bootstrapping:
            raise _bootstrap_incomplete(
                remote, default, refused=False, unpublished=unpublished
            ) from exc
        if unpublished is not None:
            raise GitRemoteUnavailableError(_branch_not_sent(remote, unpublished)) from exc
        raise
    if result.returncode != 0:
        stderr = result.stderr.strip()
        logger.warning("git_push_failed", remote=remote, refs=refspecs, stderr=stderr)
        if any(s in stderr for s in ("non-fast-forward", "fetch first", "[rejected]")):
            # M7: a rejection is the moment we KNOW we're diverged — turn it into
            # the data-bearing fork the UI needs, not a generic dead-end string.
            # During a bootstrap the default exists by now, so this is a real fork.
            raise _push_rejection(remote, working, ledger, project_root, cwd=cwd)
        if bootstrapping:
            # Only an explicit refusal proves the pair did not land: git also exits
            # non-zero when the connection drops after the remote applied the refs.
            refused = "[remote rejected]" in stderr
            raise _bootstrap_incomplete(remote, default, refused=refused, unpublished=unpublished)
        raise _push_failed(remote, stderr, unpublished=unpublished)

    pushed = ([default] if bootstrapping else []) + [working] + ([ledger] if ledger_exists else [])
    # X3 robustness (§6.8): record the tips we just published so rewrite detection
    # survives a pruned reflog (keyed <remote>/<ref>).
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
        raise _refresh_failed(remote)
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
    # The version labels the remote has on the milestones caught up to, as a colleague
    # sees them after their own Catch up: also when they are all the remote has new,
    # a label published for a milestone this clone already holds.
    remote_working = _rev_parse(f"refs/remotes/{remote}/{working}", cwd=cwd)
    labels = labels_to_adopt(remote, remote_working, cwd) if remote_working is not None else {}
    behind = [
        branch for branch, leg in ((ledger, l_leg), (working, w_leg)) if leg.status == "behind"
    ]
    if not behind and not labels:
        raise GitDomainError(f"Already up to date with '{label}'.")
    remote_ledger = _rev_parse(f"refs/remotes/{remote}/{ledger}", cwd=cwd)
    if ledger in behind and remote_ledger is not None:
        # The fast-forward of the checked-out ledger replaces the files; a catch-up of the
        # working ref or of labels alone writes none.
        _assert_way_clear("catching up", remote_ledger, cwd, leaving=False)

    # One recorded operation: the ledger first (it's HEAD; merge --ff-only advances
    # it and the working tree), then the working ref (not checked out) by CAS, then the
    # labels. Any failure puts the refs and the files back from the record, so a file
    # git could not remove leaves nothing moved. Volatile caches must not survive into
    # the caught-up tree (S12); they go once it is in place, so a refused catch-up
    # deletes nothing, and a catch-up of labels alone writes no file and keeps them.
    catch_up = Operation("catch-up", project_root, cwd, wipes_caches=bool(behind))
    for branch in behind:
        target = _rev_parse(f"refs/remotes/{remote}/{branch}", cwd=cwd)
        if target is None:
            raise GitError("could not resolve refs for the fast-forward")
        if branch == ledger:
            catch_up.fast_forward(target)
        else:
            catch_up.refs({f"refs/heads/{working}": target})
    catch_up.refs(labels)
    catch_up.run()

    logger.info("fast_forwarded_pair", remote=remote, refs=behind, labels=sorted(labels))
    return GitFastForwardResponse(
        remote=remote,
        working_branch=working,
        fast_forwarded=behind,
        labels_added=sorted(ref.removeprefix("refs/tags/version/") for ref in labels),
    )


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
    from haute._git_state import read_working_branch

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
    # Fresh tips so we adopt the current shared line (deliberate action).
    with _fetch_exec_lock:
        refreshed = _fetch_refs(remote, cwd=cwd)
    if not refreshed:
        raise _refresh_failed(remote)
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
    # The adopted ledger's checkout replaces the files.
    adopted = remote_l if remote_l is not None else remote_w
    _assert_way_clear("spinning off a copy", adopted, cwd, leaving=False)

    aside = _free_pair_name(f"{working}-local", bare_first=False, cwd=cwd)

    # One recorded operation, put back whole on any failure. HEAD (on the ledger)
    # first detaches at its tip: same commit, so the working tree doesn't change.
    # One ref transaction then sets the pair aside under the dated name and moves
    # the canonical names to the remote's tips (X2: no remote ledger, so the ledger
    # moves to the adopted working tip), and the ledger is checked out again. The
    # canonical name and the recorded working branch are unchanged. Volatile caches
    # must not bleed from the local tree into the adopted one (S12); they go once it
    # is in place, so a refused spin-off deletes nothing.
    spin_off = Operation("spin-off", project_root, cwd, wipes_caches=True)
    spin_off.checkout_detached(old_l)
    spin_off.refs(
        {
            f"refs/heads/{aside}": old_w,
            f"refs/heads/{ledger_name(aside)}": old_l,
            f"refs/heads/{working}": remote_w,
            f"refs/heads/{ledger}": remote_l if remote_l is not None else remote_w,
            **labels_to_adopt(remote, remote_w, cwd),
        }
    )
    spin_off.checkout(ledger)
    spin_off.run()

    logger.info("branched_away", working=working, set_aside=aside, remote=remote)
    return GitBranchAwayResponse(working_branch=working, set_aside_as=aside)
