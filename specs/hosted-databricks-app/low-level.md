# Hosted Databricks App — Low-Level Specification

## Module map

| Path | Responsibility |
| --- | --- |
| `src/haute/hosted.py` | The component. Detects the Databricks Apps environment contract, adapts platform-proxied traffic to the loopback shape the local security middleware expects, records the forwarded workspace user on the request scope, and builds the served ASGI application. |
| `databricks_app/app.py` | Container entry point: refreshes the vendored wheel, runs the boot probe, prepares the project, and serves `haute.hosted.create_app()` on the platform-assigned port. |
| `databricks_app/bootstrap.py` | Resolves the interpreter before changing directory, configures git credentials, restores a bound project (or seeds a volatile one), and `chdir`s into the project directory before the server is imported. |
| `databricks_app/probe.py` | One-shot boot diagnostics — interpreter, filesystem mounts, writability, git availability — printed as a single JSON line into the platform log stream. |
| `src/haute/_worker_isolation.py` | Owned by [execution-engine](../execution-engine/low-level.md); this component depends on its interpreter resolution. Owns worker spawning, including `ensure_spawnable_interpreter`, which absolutises a relative `sys.executable` before any directory change so `multiprocessing` can still exec it. |
| `src/haute/routes/databricks.py` | Owned by [databricks-io](../databricks-io/low-level.md); this component only adds the hosted credential shape. Workspace-browsing endpoints; resolves either a personal access token or the service-principal client id/secret the container injects. |

## Key types and data structures

- `DatabricksAppEnvironment` — frozen dataclass `{app_name, app_url, workspace_id}`, the detected platform contract.
- `DATABRICKS_APP_ENV_VARS` — the three variables that constitute that contract. All three present means hosted; none means local; a partial set is an environment this module was not designed for and raises rather than guessing.
- `FORWARDED_USER_SCOPE_KEY` — the ASGI scope key under which `PlatformProxyBoundary` records `X-Forwarded-Email`, read by request logging and by the storage bind endpoint for attribution.
- `PlatformProxyBoundary` — an ASGI middleware wrapping the server application.

## Control flow

`create_app()` requires a complete environment contract, then records the hosted trust decision by disabling the local session gate *before* importing `haute.server`, so the middleware stack initialises with that decision already in force — and, in a deployment that restores a bound project, after the working directory is already the restored clone. It returns the server application wrapped in `PlatformProxyBoundary`.

For each HTTP or WebSocket scope, the boundary records `X-Forwarded-Email` (when present) on the scope, strips `Forwarded` and every `X-Forwarded-*` header, and replaces `Host` with the loopback authority of the bound server. Non-HTTP scopes (lifespan) pass through untouched.

## Edge cases and invariants

- A partial environment contract raises at startup; hosted mode is never inferred from one variable.
- `create_app()` outside a recognised hosted environment raises rather than silently degrading — hosting is an explicit deployment decision, and `haute serve` remains the local entry point.
- Header rewriting alone grants nothing: with the local session gate active, a proxied request whose headers now look local is still refused. Only the explicit hosted trust decision opens the API.
- The forwarded-user scope key is absent rather than empty when the proxy sends no identity, so consumers cannot mistake "no identity" for a blank user.
- Local mode is byte-identical: every behaviour here is gated on the environment contract.

## Error handling

Environment-contract failures raise `RuntimeError` at startup with the missing variables named — the container log is the only reader, so the message states what was set and what was not. Everything downstream keeps the server's existing error handling; the boundary adds no failure modes of its own.

## Testing

`tests/test_hosted.py` covers environment detection (absent, complete, each partial combination, whitespace-only values), the boundary's scope rewriting (forwarded metadata stripped, host rewritten to loopback, forwarded email recorded then removed, lifespan untouched), that stock haute still rejects proxied traffic, that `create_app` refuses outside a hosted environment and serves API and WebSocket traffic inside one, and — the security-relevant negative — that the boundary alone never bypasses the local session gate. `tests/test_databricks_routes_auth.py` covers credential resolution for the workspace-browsing routes: personal-access-token precedence, the service-principal pair the container injects, and a missing-credential error that names both accepted forms without echoing a value. `tests/test_worker_isolation.py` covers `ensure_spawnable_interpreter`, including the relative-interpreter case this platform produces. `tests/test_secret_surface.py` is the structural backstop over both: it walks every registered response model for secret-shaped field names and scans `src/haute/` for references to secret-bearing environment variables, so a new credential surface — the platform injects one into every hosted container — cannot reach the API or a log without a reviewed allowlist entry.

Known gaps: the platform proxy itself is simulated from its observed header set rather than exercised live; the live behaviour is recorded in `databricks_app/LEARNINGS.md`.
