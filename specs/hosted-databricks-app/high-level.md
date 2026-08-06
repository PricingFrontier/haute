# Hosted Databricks App Mode — High-Level Specification (DRAFT)

Status: DRAFT for review — proposes new functionality validated by the
deployment spike in `databricks_app/` (see its LEARNINGS.md for measured
runtime facts). Nothing in this spec is implemented in `src/haute` yet;
the spike ships as an out-of-tree bundle precisely so haute's local-only
security posture stays intact until this design is agreed.

## Purpose

Haute today is a local single-user UI: loopback-only binding, per-process
session token, and rejection of any proxied traffic. Databricks Apps is a
hosting surface that puts a workspace-SSO reverse proxy in front of an
app container, forwarding requests with `X-Forwarded-*` identity headers.
This component defines how haute runs correctly and safely behind such a
platform proxy without weakening the local posture that every other
deployment keeps.

## Scope

In scope:

- Runtime detection of the Databricks Apps container.
- The trusted-boundary adaptation currently performed by the out-of-tree
  shim (`databricks_app/app.py`): forwarded-header handling and host
  identity when a platform proxy has already authenticated the user.
- Service-principal OAuth support in Databricks data IO (today PAT-only
  via `DATABRICKS_TOKEN` — the container injects
  `DATABRICKS_CLIENT_ID`/`_SECRET` instead).
- First-boot project seeding and git enablement on ephemeral storage.

Out of scope: Databricks SQL IO mechanics (specs/databricks-io), deploy
targets and CI scaffolds (specs/deploy), running haute pipelines as
Databricks jobs (future component), any multi-user collaboration model.

## Behaviour

- In a Databricks Apps container (detected via `DATABRICKS_APP_NAME` +
  `DATABRICKS_APP_URL` + `DATABRICKS_WORKSPACE_ID`), haute serves proxied
  traffic: platform-forwarded requests reach the API and WebSocket
  endpoints instead of being rejected with 400.
- Outside such a container, behaviour is byte-for-byte today's local
  posture; hosted mode is never inferred from anything but the platform
  environment contract.
- The platform proxy is the authentication boundary. Haute does not
  re-authenticate, but it consumes (never blindly trusts elsewhere) the
  forwarded identity — at minimum surfacing `X-Forwarded-Email` for
  attribution (git identity, logs) rather than discarding it.
- Databricks data IO works with the app's service-principal OAuth
  credentials out of the box; `DATABRICKS_TOKEN` remains supported and
  takes precedence where set.
- First boot in an empty container yields a working project (standard
  `haute init` scaffold, databricks target) with git initialised and an
  identity configured, so the editor, values table, and git topbar are
  functional immediately.

Invariants:

- No hosted-mode branch may relax loopback rules when the hosted
  environment contract is absent.
- Header-rewriting alone must never bypass the local session token gate
  (pinned by `tests/test_hosted.py`).
- Secrets injected by the platform (`DATABRICKS_CLIENT_SECRET`) must
  never be logged or surfaced through the API.

## Design rationale

- The spike proved the boundary can be adapted entirely at the ASGI seam
  (strip forwarded metadata, rewrite host); the open design choice is
  whether that seam graduates into `src/haute` as an explicit hosted mode
  or remains a deployment artefact. Graduating it makes the trust
  decision inspectable and testable in-tree; keeping it external keeps
  haute's core posture untouched. This spec proposes graduating it.
- Ephemeral container storage means seeded git history and any local data
  die on redeploy. Retaining git *durably* requires an external remote or
  UC-volume persistence — deliberately deferred; the seeded repo is a
  session workspace, and the UI should be able to say so (which requires
  distinguishing "no git binary" from "no repo", a gap found in the
  spike).
- The app filesystem is invisible to the rest of the workspace (no
  /Workspace, /Volumes or /dbfs mounts — measured); Unity Catalog APIs
  are the only shared data plane, which is why SP-OAuth in the IO layer
  is the keystone rather than any filesystem arrangement.

## Performance considerations (deferred, recorded)

Hosted containers invert haute's local storage economics: ~175 GB of fast
local disk (measured) that is guaranteed disposable, while every byte of
real data crosses HTTPS to Unity Catalog via a warehouse. The right
posture is therefore aggressive local caching of query results and
intermediate artefacts with thresholds sized for the hosted case (larger
caches, longer retention within a container's lifetime), treating cache
loss on redeploy as routine. No caching mechanism changes are specified
yet; when cache thresholds are next revisited, the hosted profile should
be a first-class input rather than inheriting laptop-sized defaults.

## Secret-surface backstop

Two structural checks (implemented as repo tests) back the "secrets are
never surfaced" invariant without pinning API shape:

- A recursive walk of every registered route's response-model schema,
  failing on field names matching secret patterns (secret/token/
  password/credential/key) unless explicitly allowlisted with a
  justification.
- An AST scan of `src/haute` asserting secret-bearing environment
  variable names are referenced only from allowlisted modules.

## Interactions

- [specs/hosted-project-storage](../hosted-project-storage/high-level.md) —
  answers this spec's ephemeral-storage constraint: durable saves via a
  bound git remote (DRAFT).
- specs/databricks-io — gains the SP-OAuth credential path.
- specs/deploy — already owns the `databricks` scaffold target the seeded
  project uses.
- `src/haute/_local_security.py` — owns every trust decision this mode
  touches; changes land there, not around it.

## Failure model

- Hosted-mode detection with a partial environment contract (some vars
  missing) fails loud at startup rather than guessing.
- Missing git binary in a hosted container degrades to explicit
  "git unavailable" UI state, not the misleading "not initialised".
- IO credential resolution failure reports which of the three sources
  (explicit token, SP OAuth pair, .env) were consulted, without echoing
  secret values.
