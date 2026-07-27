# OPT-D01 — Optimiser Error-Detail Policy

**Status:** Accepted
**Date:** 2026-07-27

## Context

Optimiser setup had two incompatible catch-all policies: pipeline execution
returned a fixed internal-error detail, while grid construction returned the
underlying exception text as a client-visible 400. Persisted artifact loaders
also treated an artifact removed by TTL in the same way as a corrupt
server-owned parquet, and invalid server-owned handles exposed validation
details that may contain filesystem information.

The API needs to keep genuine user/configuration errors actionable without
misclassifying implementation defects or disclosing internal paths and library
details.

## Decision

Failure classification follows the boundary that owns the operation, not the
Python exception class:

| Boundary/outcome | HTTP/job classification | Client detail |
| --- | --- | --- |
| Explicit configuration, schema, projection, bounded-memory, or versioned public-contract validation | Existing 4xx / `contract_error` | The stable actionable validation detail or structured public payload. |
| Unknown pipeline catch-all | 500 / `error` | `Pipeline execution failed. Check the server logs for details.` |
| Unknown grid-construction catch-all | 500 / `error` | `Grid construction failed. Check the server logs for details.` |
| Invalid server-owned apply/ratebook artifact handle | 500 | A fixed “artifact reference is invalid; re-run the solve” detail. |
| Valid handle whose artifact is absent | 410 | A fixed “artifact is no longer available; re-run the solve” detail. |
| Present artifact that cannot be read as its declared format | 500 | A fixed “artifact is corrupt; re-run the solve” detail. |

The known grid chunk-size validation branch remains a 400 because it is an
explicit configuration/data-contract check. All catch-all branches log the
underlying exception and traceback server-side. A missing artifact is also
logged with its server-owned path, but no exception text or path is returned to
the client.

HTTP 410 is used for an absent artifact because the handle proves that the
resource previously existed and may legitimately have been removed by the
bounded TTL lifecycle. It tells the user to repeat the solve without implying
that their current request was malformed or that an unknown resource never
existed.

## Rejected policies

- **Return every underlying exception.** This is actionable in a few cases but
  leaks filesystem, library, or implementation details and lets internal
  defects masquerade as user mistakes.
- **Sanitize every failure.** This discards safe, deliberate validation detail
  such as the exact missing column or invalid chunk-size field and makes
  correctable requests harder to fix.
- **Return 400/404 for invalid server-owned handles.** Clients select a job or
  frontier point; they do not author these handles. An invalid handle is
  therefore a server invariant failure, while an absent artifact behind a
  valid handle is the distinct 410 lifecycle outcome.

## Consequences

Client-visible generic details are stable and safe to test exactly. Server
logs remain the diagnostic source for unknown failures. Callers can distinguish
an expired/evicted result and offer a re-run action, while a corrupt or invalid
server-owned artifact remains a genuine internal error.
