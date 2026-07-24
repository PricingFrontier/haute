# Security and supply-chain roadmap

## Scope

Owns deserialisation allowlists, project/path containment, local-session and
request trust boundaries, dependency advisories, and accepted-risk evidence.
Current runtime policy is specified in
[sandbox security](../specs/sandbox-security/high-level.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---|---|
| `AUD-C18` | Reverify | P0 | Apply containment and bounded request metadata at execution/server trust boundaries. |
| `AUD-SEC-01` | Reverify | P0 | Close reachable critical/high dependency advisories with compatibility evidence. |
| `AUD-SEC-02` | Reverify | P0 | Protect local-session bootstrap material with origin/token authentication. |

## Planned improvements

### AUD-C18 — Execution and request trust boundaries

**Why:** The exact-symbol pickle/joblib allowlist is now in place, but executor
path resolution and request-ID reflection still need the same strict boundary
discipline as HTTP route validation.

**Plan:**

- Enforce project containment in execution-time runtime-file resolution, not
  only at route validation.
- Reject symlink/traversal escapes after resolution and preserve the explicit
  operator-controlled exception for named external connections.
- Accept an inbound request ID only when it matches a short ASCII token
  grammar; otherwise generate a server ID and record the rejection safely.
- Keep the exact `(module, qualname)` deserialisation allowlist and add new
  entries only with a real artifact fixture and gadget review.

**Acceptance:**

- Direct executor calls cannot read a config/artifact outside the project via
  relative paths, absolute paths, symlinks, or mixed separators.
- Oversized/control-character request IDs never reach logs or response headers
  and valid IDs retain correlation.
- Negative pickle/joblib gadget tests cover callable globals and near-prefix
  module names while supported model fixtures still load.

**Dependencies:** Deploy consumes the path policy; I/O owns credential-free
provider path rules.

**Evidence:** `src/haute/execution.py`, `src/haute/_path_resolution.py`,
`src/haute/server.py`, `src/haute/_sandbox.py`, `tests/test_path_resolution.py`,
`tests/test_path_resolution_properties.py`, `tests/test_server.py`, and
`tests/test_sandbox.py`.

### AUD-SEC-01 — Dependency advisory closure

**Why:** Advisory status changes independently of source. A planned upgrade
must be based on the current lockfiles and reachable runtime/build surface,
not a copied historical CVE list.

**Plan:**

- Audit the locked Python and frontend dependency graphs with current advisory
  data and record direct/transitive reachability.
- Upgrade every reachable critical/high advisory to a clearing version; make
  any temporary accepted risk explicit with owner, exposure, compensating
  control, and review date.
- Run backend, frontend, build, packaging, browser, and deployment compatibility
  gates for breaking upgrades.
- Treat deprecation, future, and resource warnings as drift signals in the
  relevant CI lanes.

**Acceptance:**

- Current lockfiles have no unaccepted reachable critical/high advisory.
- Each upgrade has a compatibility test or release-note rationale for the
  affected surface.
- CI periodically re-runs the audit and fails only on the documented severity
  and reachability policy.

**Dependencies:** [Engineering quality](engineering-quality.md) owns scheduled
CI mechanics; security owns remediation and risk acceptance.

**Evidence:** `pyproject.toml`, `uv.lock`, `frontend/package.json`,
`frontend/package-lock.json`, `.github/workflows/`, and the repository's
dependency-audit/preflight commands.

### AUD-SEC-02 — Local-session bootstrap protection

**Why:** The SPA index can expose the live local-session token before the
`/api/` middleware boundary. A client that can reach the server should not gain
the bearer secret solely by loading the bootstrap page.

**Plan:**

- Define the trusted-origin/host policy for the index, websocket, and API as
  one local-session contract.
- Require a valid origin or an existing token before embedding bootstrap
  material; do not accept absent Origin as automatically trusted.
- Prefer non-URL token transport and ensure query strings, access logs, error
  pages, and browser history never receive the secret.
- Keep remote bind/forwarded-host configurations fail-closed.

**Acceptance:**

- Untrusted, absent-origin, mismatched-host, and forwarded-host requests cannot
  retrieve the token or open a websocket.
- A normal local browser bootstrap and reconnect still work without copying a
  secret manually.
- Secret-corpus tests cover HTML, headers, URLs, logs, websocket failures, and
  exception responses.

**Dependencies:** Deploy/startup owns bind configuration; frontend owns
bootstrap consumption but not the trust policy.

**Evidence:** `src/haute/_local_security.py`, `src/haute/server.py`,
`frontend/src/api/`, `tests/test_local_security.py`, `tests/test_server.py`,
and browser reconnect/security tests.
