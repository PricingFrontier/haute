# Hosted project storage — roadmap

The `uc://` transport branch deliberately ships the simple cases: one app
session, one project, one storage location, fast-forward-only catch-up.
This document collects what comes next, folding in the follow-ups from
[low-level.md](low-level.md) §Approved change contract and what the
two-actor live proof (5 August 2026, recorded in
`databricks_app/LEARNINGS.md`) surfaced. Items are grouped by theme, not
strictly ordered; the first group is nearest-term.

## Surfaced by live proof

- **Identity on a restored container.** Every restore lands a fresh clone
  with no git identity, so autosaves keep writing files but version
  capture fails quietly ("empty ident") and nothing publishes until an
  identity is set — and during the walkthrough the UI never prompted,
  leaving the user's change on the container only. Either prompt promptly
  off `identity_set: false` when the first save's capture fails, or carry
  the identity in the binding record so a restore can restore it too.
- **Graceful-shutdown claim release.** `apps stop` kills the container
  without running Python `atexit`, so the shutdown release never happens
  hosted and every stop leaves a stale claim (foreign machines see "in
  use" for up to the 150 s staleness window). The platform nominally
  delivers SIGTERM with a grace period; one cheap experiment remains —
  wiring the release into an explicit SIGTERM/lifespan shutdown handler —
  and if that also fails, the release is documented as local-dev-only for
  good. Correctness is already covered by staleness expiry and own-app
  takeover; this only narrows the false-"in use" window.

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
  authored by the app identity today; `bound_by` and request logs carry
  the real user.
- **A per-browser-session claim on the app itself.** Two tabs of one app
  are one writer to storage, so the volume claim cannot arbitrate them;
  that gate lives at the app boundary.
