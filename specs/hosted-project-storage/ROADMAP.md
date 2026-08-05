# Hosted project storage — roadmap

The `uc://` transport branch deliberately ships the simple cases: one app
session, one project, one storage location, fast-forward-only catch-up.
This document collects what comes next, folding in the follow-ups from
[low-level.md](low-level.md) §Approved change contract and what the
two-actor live proof (5 August 2026, recorded in
`databricks_app/LEARNINGS.md`) surfaced. Items are grouped by theme, not
strictly ordered; the first group is nearest-term.

## Surfaced by live proof

- **Identity on a restored container — DELIVERED on this branch.** Every
  restore lands a fresh clone with no git identity, so autosaves kept
  writing files while version capture failed quietly ("empty ident") and
  nothing published — and the UI never prompted. Now the save path checks
  identity before committing (same source of truth as `identity_set`),
  the save response carries a structural `identity_required` flag plus a
  hand-authored warning, and the frontend opens an identity prompt that
  retries the save once a name and email are recorded (dismissable per
  browser session; the warning keeps appearing). Next step: skip the
  prompt entirely by attaching identity to the workspace user — the SSO
  proxy forwards the authenticated user on every request and the binding
  already records it as `bound_by`, so a restore can stamp the clone's
  identity automatically (email as the address; the display name either
  derived from the email or resolved via a SCIM lookup, which needs a
  directory-read scope the app's service principal does not hold today).
  The prompt then remains only as the fallback for the unknowable case.
  This is the front half of "per-commit authorship" below.
- **Graceful-shutdown claim release.** `apps stop` kills the container
  without running Python `atexit`, so the shutdown release never happens
  hosted and every stop leaves a stale claim (foreign machines see "in
  use" for up to the 150 s staleness window). The platform nominally
  delivers SIGTERM with a grace period; one cheap experiment remains —
  wiring the release into an explicit SIGTERM/lifespan shutdown handler —
  and if that also fails, the release is documented as local-dev-only for
  good. Correctness is already covered by staleness expiry and own-app
  takeover; this only narrows the false-"in use" window.

## Running cost

- **Idle self-stop, with tab-close as a hint.** The app bills while its
  compute runs, and today only a manual `apps stop` ends that. Because
  every real interaction passes through the backend (the SSO proxy fronts
  all tabs), the app can track last-activity and, after an idle window,
  stop itself via the Apps API — first flushing the publish queue and
  releasing the claim, which makes this the one shutdown path that CAN be
  graceful (self-initiated, so the platform's kill behaviour never
  applies). Browser tab-close (`pagehide` + `sendBeacon`) should only
  shorten the idle window, never kill directly: close events also fire on
  refresh and navigation, several tabs may be open, and a cold start
  costs minutes. Needs the app's service principal to hold permission to
  stop its own app, and the `uc://` binding for durability — which is
  the point.

## The desktop new/open loop

One app session is currently committed to one project in one storage
location until the process is replaced: rebinding is refused live and
joining a populated location works only via the bind → "restart-required"
→ boot-restore path. The desktop analogue — New / Open / Save As without
closing the application — needs:

- **Save elsewhere (save-as).** Publish the session's current project to
  a fresh location and continue working against it, live: claim the new
  location, publish, swap the binding and writer fence, release the old
  claim. The publish half exists; the swap of session state under a
  running queue is the work.
- **Open or create a project without restart.** Switch the running
  session to a different stored project (or a brand-new one at a new
  location) in place. Harder than save-as: the project directory itself
  is replaced, so the watcher, parser caches, and every open panel must
  re-point — the boot restore path does this today only because nothing
  is running yet. Needs its own spec; likely the session-state container
  (`_SessionState`, `_WriterState`) becomes swappable as a unit behind a
  quiesce point.

## Convergence between fork and parent

From the approved change contract, unchanged in scope:

- **Parent-side sync (fork → parent publishing).** The pull-request
  direction. Writing to a location another app holds the claim on is
  precisely what the claim layer prevents, so it needs a design for
  delegated or queued writes rather than a direct publish.
- **Node-level merge for diverged history.** Fast-forward-only catch-up
  is a staging position. The destination is a merge at pipeline-node
  level ("you and they both changed `banding_5` — keep which?"), never a
  text merge of generated files; reverses the never-merge-locally rule
  and needs its own spec, including the hand-written preamble and
  preserved blocks that are not graph-structured.

## Attribution and multi-actor

From the approved change contract, unchanged in scope:

- **Per-commit authorship from the forwarded user identity.** Commits are
  authored by whatever repo-level identity is set today; `bound_by` and
  request logs already carry the real user on every request, so the
  forwarding mechanism is proven — the remaining work is passing it
  per-commit (author env on the commit call) rather than per-clone, which
  is what sharing one app between two people requires. The restore-time
  auto-stamp above is this item's single-user front half.
- **A per-browser-session claim on the app itself.** Two tabs of one app
  are one writer to storage, so the volume claim cannot arbitrate them;
  that gate lives at the app boundary.
