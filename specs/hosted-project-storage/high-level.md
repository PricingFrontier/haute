# Hosted Project Storage — High-Level Specification

Status: DELIVERED; this spec describes shipped behaviour. The change
contract lives in [low-level.md](low-level.md) §Approved change contract.
Companion to [specs/hosted-databricks-app](../hosted-databricks-app/high-level.md),
which established the constraint this component answers: the app
container's filesystem — including the seeded git repository — is
destroyed by every redeploy, platform restart, and app stop. There is no
development cycle on the hosted platform without durable saves.

## Purpose

Give a hosted haute session a durable home for its project: on open,
bind the session to a storage location; restore the project from it; on
every save and milestone commit, synchronise to it; require nothing on
close. Git is the synchronisation mechanism — the durable location is a
git remote, and the hosted app is a clone of it, exactly as a laptop
checkout would be.

## Scope

In scope: the storage lifecycle for hosted mode (bind, restore,
push-on-save, sync-state visibility, failure handling); credential
handling for the remote; the transport seam.

Out of scope: parallel multi-user editing against one project (unsolved
independently of storage; an external remote at least gives it a future
shape — branch-per-user — that container-local git never could);
MLflow/model-artefact persistence (own follow-up); any behaviour change
in local (non-hosted) mode, which remains byte-identical.

## Behaviour

The session lifecycle, from the user's chair:

1. **Open, no binding**: the startup flow (the same surface that today
   handles git readiness states) reports the project is **volatile** and
   offers to bind a storage location — either a git remote URL or a
   Unity Catalog volume location (`uc://catalog.schema.volume/path`).
   The user may decline; the session then runs with a persistent "work
   here is volatile" indicator.
2. **Bind**: given a remote whose repository has content, the project is
   lifted from it (clone; the working branch machinery then proceeds as
   on any fresh clone). Given an empty repository, the current project —
   seeded scaffold plus any work already done — is initialised onto it
   (remote added as `origin`, initial push). The binding is recorded
   durably, outside the container.
3. **Save / milestone commit**: after the existing git machinery commits
   locally, the commit is pushed to `origin` asynchronously. Saves never
   wait on the network; the UI carries a small sync state — synced /
   *n* saves pending / sync failed — beside the branch indicator.
4. **Close**: nothing. Anything committed-and-pushed is durable;
   anything mid-edit is lost with the container, the same connection-loss
   exposure a laptop session does not have. Ruled acceptable (Nick,
   30 July 2026): the delta is real, small, and irreducible.
5. **Reopen (new container)**: a recorded binding restores the project
   automatically before the server accepts traffic — clone from
   `origin`, resume on the recorded working branch.

Invariants:

- A bound session never silently diverges from its durable state: if the
  remote is unreachable at boot, the session does **not** quietly start
  from the seed — it gates, explains, and offers an explicitly volatile
  session as the fallback.
- Local commits are never lost by sync failure: a failed push leaves the
  ledger intact, the sync state visible, and retry available (automatic
  on the next save, manual from the indicator).
- No auto-merge: a non-fast-forward push (someone else moved the remote)
  stops loudly with the divergence explained. Single-writer is the v1
  assumption, as it is for a local checkout.
- Credentials never appear in URLs, git config, the repository, logs, or
  API responses (extends the secret-surface backstops), and never travel
  to a host the deployment has not approved: `GIT_ASKPASS` is
  process-wide and git offers the credential to whatever host a URL
  names, so a token without `HAUTE_GIT_ALLOWED_HOSTS` refuses every bind
  rather than letting a caller choose the recipient. A `uc://` binding
  involves no git credential at all: the volume is reached through the
  workspace SDK's own authentication, and git only ever touches local
  bundle files.
- A restored session is USABLE, not merely present: the working branch
  and its ledger exist as local refs and the session can publish again
  without the user re-adopting a branch.
- Local mode is untouched: every behaviour above is gated on the hosted
  environment contract.

Known limitation (measured, not designed around): saves do not wait on
the *network*, but git serialises operations per repository, so a save
issued while a publish is in flight waits for that publish — bounded by
the push timeout. Removing this would mean relaxing the repository
mutation lock for the publish path, which is a change to the git
engine's serialisation policy and deserves its own review. The `uc://`
transport is barely exposed to it: only the local bundle creation holds
the lock; the slow part — the upload — runs outside it.

## Design rationale

- **Git itself is the store.** The alternative (copying files to a UC
  volume via the Files API) was rejected as the primary design: haute's
  save/commit model is already git, teams get a repo they can clone
  locally, and the scaffold already generates CI for exactly that world.
  The hosted app becomes a client of the project repo, converging with
  normal usage rather than diverging.
- **Transport constraint (measured, not assumed).** The app container
  has no `/Volumes`, `/Workspace` or `/dbfs` mounts; UC volumes are
  reachable only via the Files REST API, which git cannot speak — and
  laptops do not mount volumes either. Therefore a *volume cannot be a
  plain git remote*. Transport #1 is an HTTPS git host. Transport #2
  keeps everything inside the workspace: the repository is mirrored to
  a UC volume as `git bundle` artefacts — git-native content over the
  only available channel. (The original sketch was a custom git remote
  helper, `uc::`; the delivered shape is simpler — the storage module
  owns both ends of the channel, so no helper protocol is needed and
  git only ever sees local bundle files.)
- **Full bundles, not incremental.** Each published generation is a
  complete `git bundle create --all` — O(history), not O(diff) — which
  for a pricing project (code plus config JSON; data is gitignored) is
  small, and every generation being independently complete removes a
  whole class of partial-chain failure. The bundle size is logged on
  every publish so growth is visible; incremental chains are a
  possible future optimisation only if that log shows real growth.
- **Pointer written last.** The Files API offers upload-with-overwrite
  but no atomic rename, so the volume layout is generation-numbered
  bundles plus a small `HEAD.json` pointer written only after its
  bundle is fully uploaded and verified. A torn or partial upload is
  therefore harmless: readers only ever follow a generation that is
  already complete. The last five generations are retained as cheap
  rollback; older ones are pruned best-effort.
- **Single-writer fencing.** The pointer carries a `writer_id`.
  Single-writer remains the design assumption (one container, one
  project), but a read-before-write comparison lets a superseded
  container stop loudly instead of silently interleaving generations
  with its replacement.
- **Async push** honours the ruling that close requires no action: if
  close needed a flush, close would become a failure point. The pending
  counter makes the exposure visible instead.
- **Binding must outlive the container**, so it cannot live only in the
  repo or on local disk. It is a small state record on a UC volume
  (Files API — fine for JSON, unlike git), with the credential itself
  held separately in a Databricks secret resource.

## Performance considerations

Pushes are network-bound and serialised; save latency is unaffected by
design (async). Clone-at-boot adds seconds to cold start for typical
project sizes and is dwarfed by the container's own dependency install.

## Interactions

- [specs/hosted-databricks-app](../hosted-databricks-app/high-level.md) —
  supplies the environment contract and the boundary; its LEARNINGS
  record the mount measurements this design rests on.
- specs/frontend-git-ui and the git engine (`src/haute/_git.py`) — the
  push hook rides the existing save/milestone commit machinery; the
  startup flow extends the existing readiness states.
- Secret-surface backstops (`tests/test_secret_surface.py`) — extended
  to the new credential env name.
- `databricks_app/` bundle — boot order gains restore-before-serve.

## Failure model

Every failure surface follows the MAGINOT low-context-error class:
name the user-model objects (remote alias, branch, pending-save count)
and the action, never raw library text.

- Remote unreachable at boot (bound): gate before serving; message names
  the remote and offers retry or an explicitly volatile session.
- Push failure mid-session: sync indicator turns failed with the pending
  count; retried on next save; manual retry offered; commits remain
  local and intact.
- Non-fast-forward: sync stops; message states the remote moved, names
  both tips, and directs to resolve outside the app (v1).
- Auth failure: message names the secret resource and env var to check,
  echoes no value.
- Invalid binding target (not a git remote, unsupported scheme, or a
  malformed `uc://` location): rejected at bind time with the accepted
  forms stated.
- Superseded writer (`uc://`): another container has published a newer
  generation — publishing stops terminally so nothing is overwritten;
  the message says the storage moved on and directs to a restart.
- Bound `uc://` location with no published generation at boot: gates
  with the location named rather than seeding a fresh project — a
  binding promises history that should exist.
