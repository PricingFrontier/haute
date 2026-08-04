# Databricks Apps deployment spike — learnings

> Update (4 August 2026): the `uc://` bundle transport was proven live on
> this app. Bind to `uc://workspace.default.haute_state/projects/uc-live-demo`
> → `adopted`, generation 1 bundle + HEAD.json on the volume; a pipeline
> save published generation 2 through the background queue; `apps stop` +
> `apps start` (container replaced) restored the project from the bundle —
> working branch `pricing/nick/uc-demo` resumed, ledger checked out, head
> at the pre-stop save, origin repointed to the uc:// URL; a post-restore
> save published generation 3 under a NEW writer_id (the restored-from
> generation exemption in the supersession fence, exercised for real).
> Two operational notes: `apps start` kicks off its own deployment of the
> last-synced source, so a deploy issued while it settles fails with
> "active deployment in progress" (retry); and a restored clone has no git
> identity (`identity_set: false` — same as the https transport), so the
> UI prompts for one before the first commit. Cleanup: the binding record
> was deleted (an older build cannot parse a uc:// binding and would gate
> at boot) and the app stopped; the demo bundles remain on the volume at
> `projects/uc-live-demo/` for inspection.

> Update (30 July 2026): the spike's shim has been graduated into haute
> proper as `haute.hosted` (spec: specs/hosted-databricks-app/), with
> SP-OAuth support in `_databricks_io` + `routes/databricks.py`, a
> `git-unavailable` readiness state, and secret-surface backstop tests.
> `app.py` here is now thin glue over `haute.hosted.create_app()`. The
> shim-era notes below are preserved as the record of how the boundary
> was discovered.

Status: DEPLOYED AND RUNNING (29 July 2026) —
https://haute-spike-2112915975510064.aws.databricksapps.com on workspace
`dbc-6abae023-c819` (CLI profile `haute-spike`, app service principal
`app-2xmuei haute-spike`, MEDIUM compute).

## Real-deploy findings (beyond the local simulation)

- End-to-end timings: `apps create` (compute provisioning) ~3 min;
  `sync` of the 2.2 MB bundle seconds; `apps deploy` including the full
  heavy pip install only ~56 s — the Apps build env pulls wheels at
  ~40 MB/s and pre-seeds many packages.
- The app venv comes **pre-seeded with framework packages** (streamlit,
  gradio, databricks-sql-connector 3.4.0…). Our install overwrote shared
  deps and pip printed resolver conflicts as warnings, not failures
  (e.g. preinstalled sql-connector 3.4.0 vs our numpy 2.4/pyarrow 24).
  Gotcha: `requirements.txt` installs the bare wheel, so haute's
  `databricks` extra (sql-connector >=4.2.5) was NOT installed and the
  stale preinstalled 3.4.0 is what an import would find — vendored-wheel
  requirements should use the `wheel[databricks]` extras form.
- Unauthenticated requests to the app URL 302 to the workspace OIDC
  authorize endpoint (PKCE flow against the app's own OAuth client) —
  the platform SSO gate is real and in front of everything.
- Runtime logs via `databricks apps logs <name>` (CLI ≥ v1.10) without
  needing the browser-authed `/logz`; `[BUILD]` and `[APP]` streams are
  interleaved in one feed.
- The deployed cwd is `/app/python/source_code` (a snapshot copy of the
  synced source, owned by the app SP); haute's file watcher runs happily
  on it and the boot log matched the local simulation exactly.
- Cost control: the app bills while compute is ACTIVE —
  `databricks apps stop haute-spike` when idle.

## What a Databricks app needs from haute

The Databricks Apps runtime is: serverless container, Python 3.11 venv,
`requirements.txt` installed at deploy time, process must listen on
`0.0.0.0:$DATABRICKS_APP_PORT`, all traffic arrives via a workspace-SSO
reverse proxy that adds `X-Forwarded-*` headers and a
`<app>-<workspace-id>.<region>.databricksapps.com` host.

## Runtime probe results (probe.py, second deploy, 29 July 2026)

- Ubuntu 22.04-class container, Python 3.11.15, non-root uid 1000,
  HOME=/home/app, ~175 GB free local disk; cwd, /tmp and HOME all writable.
- **git 2.34.1 IS preinstalled at /usr/bin/git** — no bundling needed. The
  topbar "git not initialised" is literal: the project dir just isn't a
  repo (and no git user.name/email are configured — commits need seeding).
- **Runtime detection**: `DATABRICKS_APP_NAME` / `DATABRICKS_APP_URL` /
  `DATABRICKS_WORKSPACE_ID` env vars are the clean hosted-mode sentinel.
- **No FUSE mounts**: /Workspace, /Volumes, /dbfs all absent. Native data
  access from inside the app is API-only (SQL warehouse via connector,
  UC volume files via SDK Files API). The app's local disk is likewise
  invisible to jobs/notebooks — UC is the only shared ground.
- The platform pre-sets framework env (PORT, UVICORN_HOST/PORT,
  FLASK_RUN_*, STREAMLIT_*, GRADIO_*) — bare `uvicorn app:app` would be
  configured automatically via UVICORN_* click env vars.
- App SP credentials are injected as `DATABRICKS_CLIENT_ID`/`_SECRET`
  (+ `DATABRICKS_HOST`), i.e. databricks-sdk default auth works in-app
  with zero config. Gap: haute's `_databricks_io` reads `DATABRICKS_TOKEN`
  (PAT) only — hosted data access needs an SP-OAuth path (small,
  spec-first change; sql-connector 4.x supports credentials providers).

## Third staleness layer: `databricks sync` honours .gitignore

Gitignoring the vendored wheel (right for git — a 2 MB build artifact) makes
it INVISIBLE to `databricks sync`. Source files then update on every deploy
while the installed package silently freezes at whatever wheel the workspace
last received — the failure looks like an `ImportError` for a module that is
demonstrably present in the local wheel. Diagnose by comparing sizes:
`databricks workspace list <dir>` vs the local file.

Fix: `scripts/deploy.sh` imports the wheel explicitly
(`databricks workspace import --format RAW --overwrite`) after the sync.
Always deploy through that script while the wheel stays gitignored.

## The vendored-wheel staleness trap (hit on the graduation redeploy)

Two independent layers silently keep an OLD haute running after a deploy
when the wheel's content changes but its version doesn't:

1. Databricks decides whether to run pip at all by hashing the TEXT of
   requirements.txt ("Requirements have not changed. Skipping
   installation."). Mitigation: build_bundle.sh stamps the wheel sha256
   into requirements.txt.
2. Even when pip runs, the app venv persists across deployments and pip
   skips a same-version wheel as "Requirement already satisfied" —
   requirements files cannot carry --force-reinstall.

Mitigation for (2): app.py force-reinstalls the vendored wheel
(--no-deps, single package, seconds) at process start, then runs a plain
install to resolve any newly added dependencies. The durable fix is real
per-build versions (e.g. CI-stamped .devN), at which point both layers
resolve naturally.

## Confirmed failure points (reproduced locally)

Stock haute on `0.0.0.0` behind a simulated proxy:

| Probe | Result |
| --- | --- |
| Plain loopback request | 200 |
| Any request with `X-Forwarded-For` | **400** "Forwarded headers are not supported by the local Haute UI" |
| `Host: …databricksapps.com` | **400** "Invalid host header" |

Both come from `LocalTrustedHostMiddleware` (`src/haute/_local_security.py`),
which is added unconditionally in `src/haute/server.py` and is NOT relaxed by
`HAUTE_DISABLE_LOCAL_SESSION_AUTH`. `HAUTE_TRUSTED_HOSTS` cannot help: it
raises unless every entry is loopback. `haute serve` additionally refuses
non-loopback binds outright (`src/haute/cli/_serve.py`), so the CLI is not
usable as the app entry point at all.

## What works (via the shim in this directory)

`app.py` wraps the stock ASGI app: strips `Forwarded`/`X-Forwarded-*`,
rewrites `Host` to `127.0.0.1:<port>`, sets
`HAUTE_DISABLE_LOCAL_SESSION_AUTH=1`. With the full Databricks header set
(`X-Forwarded-For/Host/Proto/Email/User`, databricksapps `Host`):

- SPA shell: 200 `text/html`
- `/api/session`: 200; `/api/pipelines`: 200 `[]`
- `/ws/sync` handshake with external `Origin`: 101 (WebSocket auth honours
  the disable flag)
- Empty cwd (= fresh app container) boots cleanly; logs
  `haute_toml_missing` ("Run 'haute init'") and starts the file watcher on
  cwd. Nothing is written to cwd at boot.

Other de-risking:

- The wheel (frontend baked in) is ~2.2 MB — comfortably under the
  **10 MB per-file** upload gate; vendoring it in the app dir works with
  `./haute-…-py3-none-any.whl` in `requirements.txt`.
- Full dependency set (catboost, mlflow, scipy, polars…) resolves and
  installs clean on Python 3.11 — but it is heavy, so expect slow deploys.
- Nuance: uvicorn's default `proxy_headers=True` consumes `X-Forwarded-For`
  for the client IP before the shim strips the headers; harmless here.

## Open items for a real deploy

1. **Workspace + auth**: `databricks auth login --host <workspace-url>`
   (interactive OAuth), then:
   `databricks apps create haute-spike`,
   `databricks sync databricks_app /Workspace/Users/<me>/haute-spike`,
   `databricks apps deploy haute-spike --source-code-path …`.
2. **Project content**: an empty app has no `haute.toml`; seed a demo
   project into the app dir or target a UC Volume. Local state
   (mlflow.db, outputs/) is ephemeral in the container.
3. **Security model decision**: the shim discards the forwarded identity;
   everyone reaching the app acts as the app's service principal, and the
   haute editor can execute arbitrary Python. App "Can use" grants must be
   tight. A production hosted mode should consume `X-Forwarded-Email`/
   on-behalf-of tokens instead of stripping them, and haute would need a
   first-class hosted-mode seam (spec-first change) rather than this shim.
4. **CI/CD**: promote via a Databricks Asset Bundle target once the manual
   loop works.
