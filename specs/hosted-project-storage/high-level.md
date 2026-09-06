# Hosted Project Storage — High-Level Specification

This specification is a companion to
[specs/hosted-databricks-app](../hosted-databricks-app/high-level.md),
which defines the constraint this component answers: the app
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

Out of scope: parallel multi-user editing against one project;
MLflow/model-artefact persistence; and any behaviour change in local
(non-hosted) mode, which remains byte-identical.

## Behaviour

The session lifecycle, from the user's chair:

1. **Open, no binding**: the startup flow, through the git-readiness
   surface, reports the project is **volatile** and
   offers to bind a storage location — either a git remote URL or a
   Unity Catalog volume location (`uc://catalog.schema.volume/path`).
   The user may decline; the session then runs with a persistent "work
   here is volatile" indicator.
2. **Bind**: given a remote whose repository has content, the project is
   lifted from it (clone; the working branch machinery then proceeds as
   on any fresh clone). Given an empty repository, the current project —
   seeded scaffold plus any work already done — is initialised onto it
   (remote added as `origin`, initial push). The binding is recorded
   durably, outside the container. A `uc://` location is additionally
   *claimed* at bind (see Design rationale): a location another app
   instance actively holds is refused with the holder named, and the
   user is offered the two honest ways forward — bind somewhere else,
   or fork the held location into a new one (provenance recorded).
   Binding does not hold the session hostage: the URL is checked
   immediately (a typo is rejected while the user is still looking at
   the field), then the network work — claim, inspect, publish, record
   — runs in the background while the app stays usable. Success
   surfaces as a passing confirmation; failure reopens the dialog with
   the reason and the URL still filled in.
3. **Save / milestone commit**: after the existing git machinery commits
   locally, the commit is pushed to `origin` asynchronously. Saves never
   wait on the network; the UI carries a small sync state — synced /
   *n* saves pending / sync failed — beside the branch indicator.
   A publish that succeeds also refreshes the binding's *restart
   target* to the working branch it just carried, so a later container
   resumes the branch the user last published on.
4. **Close**: nothing. Anything committed-and-pushed is durable;
   anything mid-edit is lost with the container, the same connection-loss
   exposure a laptop session does not have. The persistent volatile-state
   indicator makes that exposure explicit while the session is open.
5. **Reopen (new container)**: a recorded binding restores the project
   automatically before the server accepts traffic — clone from
   `origin`, resume on the restart target: the working branch in
   effect at the most recent successful publication. A branch
   selected, forked, archived or deleted without a later publish is
   clone-local and does not move the target; a target the stored
   project no longer contains serves the project and reopens the
   branch chooser.

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
- The restart target is only ever a published branch: it is written
  after the transport succeeds and left untouched by a failed publish,
  so a restore never advertises a branch the stored project lacks.
  Binding a populated location records no target; the first successful
  publish after the restart records the branch the user chose.
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
  The hosted app is a client of the project repo, converging with
  normal usage rather than diverging.
- **Transport constraint (measured, not assumed).** The app container
  has no `/Volumes`, `/Workspace` or `/dbfs` mounts; UC volumes are
  reachable only via the Files REST API, which git cannot speak — and
  laptops do not mount volumes either. Therefore a *volume cannot be a
  plain git remote*. Transport #1 is an HTTPS git host. Transport #2
  keeps everything inside the workspace: the repository is mirrored to
  a UC volume as `git bundle` artefacts — git-native content over the
  only available channel. The storage module owns both ends of this
  transport, so no custom remote-helper protocol is involved and git
  only ever sees local bundle files.
- **Full bundles, not incremental.** Each published generation is a
  complete `git bundle create --all` — O(history), not O(diff) — which
  for a pricing project (code plus config JSON; data is gitignored) is
  small, and every generation being independently complete removes a
  whole class of partial-chain failure. Every publish attempt records bundle
  creation/verification, Files API phases, local record writing, pruning,
  total time, and the compressed bundle size when one was produced. A
  reproducible 10/100/500-commit certificate gates the representative
  500-commit bundle at 25 MiB, local creation at 5 seconds, and verification
  at 2 seconds; production records, rather than the in-memory Files API fake,
  own real network-latency evidence, with 30 seconds p95 for a bundle upload at
  or below the size gate as the operational decision threshold. The newest five
  complete generations remain the hard storage bound. Incremental/checkpoint
  chains stay rejected until representative growth crosses one of those gates:
  their dependency, retry, recovery, and retention complexity is not justified
  by an unmeasured possibility.
- **One pointer per generation, created once.** The Files API offers
  upload-with-overwrite and a create-only upload (`overwrite=false` is
  refused when the path exists) but no atomic rename or
  compare-and-swap, so the volume layout is generation-numbered
  bundles plus one small immutable pointer record per committed
  generation, created only after its bundle is fully uploaded and
  verified; the highest committed pointer is the head. A torn or
  partial upload is therefore harmless: readers only ever follow a
  generation that is already complete. The last five generations are
  retained as cheap rollback; older ones are pruned best-effort.
- **Single-writer fencing.** Each pointer carries a `writer_id`.
  Single-writer remains the design assumption (one container, one
  project), but the create-only pointer write is the fence: two
  writers racing to the same generation contend on one path that only
  one of them can create, so a superseded container stops loudly
  instead of silently interleaving generations with — or overwriting —
  its replacement. A read-before-write comparison still runs first,
  only to stop early without packaging when the loss is already
  visible.
- **A claim makes the location behave like a locally-owned file.** The
  fence prevents corruption but only fires at write time; the claim
  (`CLAIM.json` beside the pointer) is the *steering* layer that stops
  two writers binding to one location in the first place. It is a
  lease: the holder refreshes it on a heartbeat and on every publish,
  and a claim whose heartbeat is older than the staleness threshold is
  dead and may be taken over. "Has the holding session ended?" is
  deliberately NOT a liveness probe of another container (unknowable):
  it is lease expiry, plus one shortcut — a claim held by this app's
  own name is always taken over immediately, because the platform runs
  one container per app, so that claim can only be a predecessor's.
  (The shortcut requires a real platform app name: processes without
  one share a fallback scope and must wait out each other's lease like
  strangers.)
  Clean shutdown releases the claim best-effort; unclean death (every
  redeploy, restart, and stop) is the normal case and is what the
  lease expiry exists for. The Files API has no compare-and-swap, so
  acquisition is write-then-verify (claim with a fresh nonce, read
  back, proceed only if yours); the claim is advisory — the per-write
  fence remains the correctness layer underneath it, and publishing
  additionally verifies the claim so a stolen lease stops the old
  holder loudly rather than letting two writers interleave.
- **Fork now, merge later.** Binding to a location someone else
  actively holds is refused with the holder named — and with a way
  forward: fork the location. A fork copies the parent's latest
  *published* generation to a fresh location as its generation 1 and
  records the provenance (`LINEAGE.json`: parent URL, generation, tip)
  so the fork is signposted, not silent.
- **A fork can see and catch up to its parent, but not merge it.** The
  recorded lineage makes the parent fetchable: its published bundle is
  a git repository, and the fork was cut from it, so the two share real
  ancestry and git can measure the distance between them. A fork
  therefore reports how far ahead and behind its parent it is, and can
  fast-forward onto the parent when it has no work of its own. What it
  does not merge divergent history — that is the existing
  never-merge-locally rule (`fast_forward_pair`), and honouring it here
  keeps one rule across both transports instead of two. When both sides
  have moved, the fork says so plainly and stops. A text conflict inside
  a generated pipeline file is not something this product's users can
  resolve, so node-level merge semantics remain explicitly out of scope.
- **Async push** honours the ruling that close requires no action: if
  close needed a flush, close would become a failure point. The pending
  counter makes the exposure visible instead.
- **Async bind, for the same reason.** A bind publishes the whole
  project, so its duration is the project's size and the volume's
  latency — neither of which the user should sit through behind a modal.
  Only the checks that are instant and local stay synchronous, because
  those are the ones whose answer belongs beside the input field. The
  slow remainder reports through the same readiness surface the sync
  chip already uses.
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
- Bind to a location under a live claim: refused with the holder named
  (app, who bound it, how fresh the heartbeat is) and the two ways
  forward stated — bind elsewhere, or fork it into a new location.
- Boot restore of a location under a live foreign claim: gates (a boot
  cannot offer a dialog); the message names the holder. The normal
  restart case never hits this — the predecessor's claim carries this
  app's own name and is taken over immediately.
- Claim lost mid-session (lease expired while the process was stalled
  and another writer took over): the next publish stops terminally with
  the new holder named; local commits remain intact.
- Checking a parent that has been deleted, emptied, or is unreadable:
  the check fails naming the parent location; the fork is untouched and
  keeps working — a fork is a complete project, not a dependent one.
- Catching up when the fork has its own work: refused, stating that both
  sides have changed and by how much. This is the honest edge of the
  feature, not a fault, and it says so rather than implying breakage.
