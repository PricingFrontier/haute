# OPT-D01 — Optimiser Error-Detail Policy

**Status:** Accepted
**Date:** 2026-07-27, amended 2026-11-18 and 2026-11-25

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

The fixed detail for unknown failures, "Check the server logs for details.",
sent the analyst to a log a hosted editor does not give them, and treated a
failure in the analyst's own node, whose message a preview already shows in
full, as an unknown one.

## Decision

Failure classification follows the boundary that owns the operation, not the
Python exception class, and every boundary words a failure through
`haute._failure_answers` (specs/server-api/low-level.md, "Failure answers"):

| Boundary/outcome | HTTP/job classification | Client detail |
| --- | --- | --- |
| Explicit configuration, schema, projection, bounded-memory, or versioned public-contract validation | Existing 4xx / `contract_error` | The stable actionable validation detail or structured public payload. |
| A failure the graph walk marked with the node whose own code raised it | 422 / `contract_error` | `node_failed`: "'<node>' failed: <message>" with the node id, the message a preview records on the node. |
| The pipeline's own contracts, settings or data (`PIPELINE_ERROR_TYPES`, a Polars error included) | 422 / `contract_error` | `pipeline_failed`: the message a preview records for the node (a Polars error's without the plan it appends). |
| A file the operating system refused, out of memory in any form | 409, 422 or 507 / `contract_error` or `memory_limited` | The file named relative to the project, or the memory limit and what to change. |
| Unknown pipeline or grid-construction catch-all | 500 / `error` | `unexpected_failure`: words and an incident id, the job's message too; the exception and its traceback recorded under the id. |
| Solver input whose quotes are not one after another (checked once the grid builder refuses the file) | 400 / `contract_error` | Names the quote column, the scenario index column and the first quote out of place. |
| Solver input whose scenario values do not rise with the scenario index, or whose quotes do not share the first quote's values (checked once the grid builder refuses the file, after the layout) | 400 / `contract_error` | Names the scenario value and scenario index columns, the first step that does not rise and its value beside the step before, and says an Expander's Min must be below its Max; or names the quote, the step, its value and the first quote's. |
| Invalid server-owned apply/ratebook artifact handle | 500 | A fixed “artifact reference is invalid; re-run the solve” detail. |
| Valid handle whose artifact is absent | 410 | A fixed “artifact is no longer available; re-run the solve” detail. |
| Present artifact that cannot be read as its declared format | 500 | A fixed “artifact is corrupt; re-run the solve” detail. |

Every optimiser setup and auto-range step that runs the pipeline is a pipeline boundary:
writing the solver input and the quote-analysis table sink the pipeline's frames, so a failure
there (a 128-bit integer column, which no Parquet file Haute writes can hold) is the
pipeline's, said as a preview says it, and so is one that reaches the setup and auto-range
classification. The grid build reads only the file Haute wrote, so a refusal of it that is
neither named cause above stays an incident.

A failure in the analyst's own node is public: it is their code, the message
is the one a preview shows, and naming the node is what lets them fix it. A
failure with no node stays an incident: its library text and file paths are
recorded, never returned. A missing artifact is logged with its server-owned
path, but no exception text or path is returned to the client.

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
  such as the exact missing column, and a node's own message, and makes
  correctable requests harder to fix.
- **Return 400/404 for invalid server-owned handles.** Clients select a job or
  frontier point; they do not author these handles. An invalid handle is
  therefore a server invariant failure, while an absent artifact behind a
  valid handle is the distinct 410 lifecycle outcome.

## Consequences

Client-visible generic details are stable in shape and carry an incident id the
analyst can report. The record under that id remains the diagnostic source for
unknown failures. Callers can distinguish an expired/evicted result and offer a
re-run action, while a corrupt or invalid server-owned artifact remains a
genuine internal error.
