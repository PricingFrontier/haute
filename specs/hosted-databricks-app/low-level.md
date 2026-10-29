# Hosted Databricks App — Low-Level Specification

## Module map

| Path | Responsibility |
| --- | --- |
| `src/haute/hosted.py` | The component. Detects the Databricks Apps environment contract, adapts platform-proxied traffic to the loopback shape the local security middleware expects, records the forwarded workspace user on the request scope, and builds the served ASGI application. |
| `databricks_app/app.py` | Container entry point: installs the locked runtime (`_install_locked_runtime`: the vendored wheel is checked against `wheel.sha256`, then `pip install --no-deps --require-hashes -r requirements.txt` and the wheel with `--no-deps --force-reinstall`, so nothing is resolved at boot), runs the boot probe, prepares the project, and serves `haute.hosted.create_app()` on the platform-assigned port. |
| `databricks_app/app.yaml` | Databricks App manifest defining the container command (`python app.py`) and environment configuration (session auth disable, state volume, and git credential settings). |
| `databricks_app/bootstrap.py` | Resolves the interpreter before changing directory, configures git credentials, restores a bound project (or seeds a volatile one), and `chdir`s into the project directory before the server is imported. |
| `databricks_app/probe.py` | One-shot boot diagnostics — interpreter, filesystem mounts, writability, git availability — printed as a HAUTE_DBX_PROBE-prefixed indented JSON blob into the platform log stream. |
| `databricks_app/requirements.txt` | Written by `build_bundle.sh`, never committed: the lock of haute's dependencies with the `[databricks]` extra, which the platform installs at deploy and the boot installs again. `uv pip compile` resolves it for the app's Python and platform, every distribution pinned by version and hash; its index lines are kept as comments with any credentials removed, since `--emit-index-url` writes a credentialed index URL as it is. |
| `databricks_app/wheel.sha256` | Written by `build_bundle.sh`, never committed: the vendored wheel's SHA-256 and file name, which the boot checks before installing it. |
| `databricks_app/scripts/build_bundle.sh` | Shell script that builds the wheel into a directory of its own and requires exactly one (a shared `dist/` can hold other versions), copies it into the app directory, enforces the 10 MB per-file limit, records its digest in `wheel.sha256`, and writes the lock as `requirements.txt`, for `HAUTE_APP_PYTHON` (default 3.11) on `HAUTE_APP_PLATFORM` (default `x86_64-manylinux_2_28`). |
| `databricks_app/scripts/deploy.sh` | Shell script taking the profile, workspace directory and app name: runs `build_bundle.sh`, syncs `databricks_app/` to the workspace, raw-imports the three gitignored build outputs after the sync (which honours `.gitignore` and may remove copies a previous deploy imported), and runs `databricks apps deploy` for the named app. |
| `databricks_app/LEARNINGS.md` | Operational deployment findings, platform traps (relative interpreter, absent container-shutdown signals), and two-actor live verification log for Databricks Apps. |
| `src/haute/_worker_isolation.py` | Owned by [execution-engine](../execution-engine/low-level.md); this component depends on its interpreter resolution. Owns worker spawning, including `ensure_spawnable_interpreter`, which absolutises a relative `sys.executable` before any directory change so `multiprocessing` can still exec it. |
| `src/haute/routes/databricks.py` | Owned by [databricks-io](../databricks-io/low-level.md); this component only adds the hosted credential shape. Workspace-browsing endpoints; resolves either a personal access token or the service-principal client id/secret the container injects. |

## Key types and data structures

- `DatabricksAppEnvironment` — frozen dataclass `{app_name, app_url, workspace_id}`, the detected platform contract.
- `DATABRICKS_APP_ENV_VARS` — the three variables that constitute that contract. All three present means hosted; none means local; a partial set is an environment this module was not designed for and raises rather than guessing.
- `FORWARDED_USER_SCOPE_KEY` — the ASGI scope key under which `PlatformProxyBoundary` records `X-Forwarded-Email`, read by request logging and by the storage bind and fork endpoints for attribution.
- `PlatformProxyBoundary` — an ASGI middleware wrapping the server application.

## Control flow

`create_app()` requires a complete environment contract, then records the hosted trust decision by disabling the local session gate *before* importing `haute.server`, so the middleware stack initialises with that decision already in force — and, in a deployment that restores a bound project, after the working directory is already the restored clone. It returns the server application wrapped in `PlatformProxyBoundary`.

For each HTTP or WebSocket scope, the boundary records `X-Forwarded-Email` (when present and non-blank, stripped of surrounding whitespace; blank or whitespace-only values are treated as absent) on the scope, strips `Forwarded` and every `X-Forwarded-*` header, and replaces `Host` with the loopback authority of the bound server. Non-HTTP scopes (lifespan) pass through untouched.

## Edge cases and invariants

- A partial environment contract raises at startup; hosted mode is never inferred from one variable.
- `create_app()` outside a recognised hosted environment raises rather than silently degrading — hosting is an explicit deployment decision, and `haute serve` remains the local entry point.
- Header rewriting alone grants nothing: with the local session gate active, a proxied request whose headers now look local is still refused. Only the explicit hosted trust decision opens the API.
- The forwarded-user scope key is absent rather than empty when the proxy sends no identity or a blank or whitespace-only `X-Forwarded-Email` header (and a non-blank value is stripped of surrounding whitespace), so consumers cannot mistake "no identity" for a blank user.
- Local mode is byte-identical: every behaviour here is gated on the environment contract.

## Error handling

Environment-contract failures raise `RuntimeError` at startup with the missing variables named — the container log is the only reader, so the message states what was set and what was not. Everything downstream keeps the server's existing error handling; the boundary adds no failure modes of its own.

## Testing

`tests/test_hosted.py` covers environment detection (absent, complete, each partial combination, whitespace-only values), the boundary's scope rewriting (forwarded metadata stripped, host rewritten to loopback, forwarded email recorded then removed, lifespan untouched), that stock haute still rejects proxied traffic, that `create_app` refuses outside a hosted environment and serves API and WebSocket traffic inside one, and — the security-relevant negative — that the boundary alone never bypasses the local session gate. `tests/test_databricks_routes_auth.py` covers credential resolution for the workspace-browsing routes: personal-access-token precedence, the service-principal pair the container injects, and a missing-credential error that names both accepted forms without echoing a value. `tests/test_worker_isolation.py` covers `ensure_spawnable_interpreter`, including the relative-interpreter case this platform produces. `tests/test_secret_surface.py` is the structural backstop over both: it walks every registered response model for secret-shaped field names and scans `src/haute/` for references to secret-bearing environment variables, so a new credential surface — the platform injects one into every hosted container — cannot reach the API or a log without a reviewed allowlist entry. `tests/test_hosted_app_bundle.py` runs `build_bundle.sh` and `deploy.sh` under the bash a CI runner uses, with `uv` and `databricks` stood in by exported functions: the bundle holds the wheel just built beside stale ones in `dist/` (a newer build and a lower rebuild), a build making two wheels is refused, the lock is resolved for the app's Python and platform with the `databricks` extra, every pin carries its hash and no line keeps a credential, and the deploy imports the three outputs to the app it names; it also checks the outputs are gitignored and untracked, and that the boot installs the lock and then the checked wheel with `--no-deps`, refusing a wheel unlike its digest or a bundle without exactly one wheel before any install.

Known gaps: the platform proxy itself is simulated from its observed header set rather than exercised live; the live behaviour is recorded in `databricks_app/LEARNINGS.md`. The locked install was verified once by hand on 29 October 2026, not in CI (it downloads the whole closure): a bundle built on Windows installed on Linux CPython 3.11 with the boot's two commands, `pip check` found no broken requirements and the installed set was the lock plus haute. The boot does not run `pip check` itself: the platform pre-seeds its venv with framework packages haute never installs, whose requirements it cannot answer for.
