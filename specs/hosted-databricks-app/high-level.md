# Hosted Databricks App Mode — High-Level Specification

## Purpose

Hosted Databricks App mode runs Haute behind the workspace-authenticated
Databricks Apps reverse proxy without weakening the default local-only server
posture. It makes the platform trust decision explicit, adapts proxied HTTP and
WebSocket traffic at one ASGI boundary, and retains the forwarded workspace
identity for attribution.

## Scope

In scope:

- Complete, fail-loud detection of the Databricks Apps environment contract.
- Forwarded-header removal, loopback host adaptation, and request-scope user
  attribution after the platform proxy has authenticated the request.
- The hosted application factory and container entry/bootstrap boundary.
- Integration with the Databricks personal-access-token or service-principal
  credential forms supplied to the data-IO component.

Project binding, durable Unity Catalog bundle storage, claims, and restore are
owned by [hosted project storage](../hosted-project-storage/high-level.md).
Databricks SQL mechanics are owned by
[Databricks IO](../databricks-io/high-level.md), deployment scaffolds by
[deploy](../deploy/high-level.md), and local session-token/loopback policy by
[sandbox security](../sandbox-security/high-level.md). Multi-user live
collaboration and running pipelines as Databricks jobs are out of scope.

## Behaviour

- Hosted mode requires non-empty `DATABRICKS_APP_NAME`, `DATABRICKS_APP_URL`,
  and `DATABRICKS_WORKSPACE_ID`. All three present selects hosted mode; none is
  the ordinary local environment; any partial combination is invalid.
- `haute.hosted.create_app()` is an explicit hosted entry point. It requires the
  complete environment, records the hosted trust decision before the server
  middleware is imported, and returns the normal server wrapped by the proxy
  boundary. It never silently becomes the local entry point.
- For HTTP and WebSocket traffic, the boundary records a non-blank
  `X-Forwarded-Email` stripped of surrounding whitespace on the ASGI scope
  (treating a blank or whitespace-only header as absent), removes `Forwarded`
  and every `X-Forwarded-*` header, and replaces `Host` with the loopback
  authority that the server expects. Lifespan and other non-request scopes pass
  through.
- The boundary alone grants no authority. If the explicit hosted trust
  decision has not disabled the local session gate, rewritten proxied traffic
  remains unauthorized. Outside the hosted entry point, local behavior is
  unchanged.
- Project code (node text, preambles, utility modules) runs as trusted
  first-party code with the app's own identity inside its single-tenant
  container. The boundary adapts traffic; it does not contain code. Access to a
  project is governed by the workspace permissions on the app, as
  [sandbox-security](../sandbox-security/high-level.md) records under Trust
  boundary.
- Databricks browsing and SQL acquisition accept `DATABRICKS_HOST` plus either
  `DATABRICKS_TOKEN` or the complete
  `DATABRICKS_CLIENT_ID`/`DATABRICKS_CLIENT_SECRET` pair. The token takes
  precedence. Secret values never enter API responses or logs.
- The container bootstrap restores a bound durable project when configured or
  seeds a volatile project, configures Git, changes to the selected project,
  and only then imports/serves the hosted application. Interpreter resolution
  is made absolute before that directory change so worker processes remain
  spawnable.

Repository tests recursively inspect registered response schemas for
secret-shaped fields and restrict secret-bearing environment-variable
references to reviewed modules.

## Design rationale

The platform proxy is the authentication boundary; Haute's ASGI wrapper owns
only the narrow translation from that trusted platform shape to the already
tested local server shape. Keeping detection closed and the hosted factory
separate prevents an ordinary `haute serve` process from inferring a weaker
posture from a stray header or single environment variable.

Removing forwarded metadata after extracting the one attribution field avoids
letting downstream middleware reinterpret proxy assertions. Recording absence
as an absent scope key, rather than an empty user, keeps attribution consumers
from inventing identity.

Databricks Apps local disk is disposable. Runtime caches may use it, but durable
project state crosses the explicit hosted-storage boundary; no filesystem mount
is treated as an implicit shared data plane.

## Interactions

- [Hosted project storage](../hosted-project-storage/high-level.md) prepares the
  project directory before the application factory is imported.
- [Databricks IO](../databricks-io/high-level.md) owns credential resolution and
  SQL/Workspace client behavior for both supported authentication forms.
- [Sandbox security](../sandbox-security/high-level.md) owns the local trust and
  session-token policy that the explicit hosted factory configures.
- [Execution engine](../execution-engine/high-level.md) owns spawnable worker
  interpreter resolution used before the bootstrap changes directory.

## Failure model

- Whitespace-only values count as absent, so an all-whitespace environment is
  the local posture; an environment contract that is partial after stripping
  fails at startup naming the missing variables without exposing their values.
- Calling the hosted factory outside the complete environment fails rather than
  falling back to a local server.
- Missing Databricks authentication identifies the accepted credential forms;
  incomplete service-principal credentials are rejected and no supplied value
  is echoed.
- Proxy-boundary processing adds no recovery fallback: downstream server
  failures retain the normal API/WebSocket error behavior.
- Live platform-proxy behavior is simulated from the observed header contract
  in repository tests; the platform probe record is operational evidence, not
  a replacement for these invariants.
