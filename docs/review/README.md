# Engineering audit evidence archive

This directory preserves the point-in-time engineering audit, its
reproductions, and its later re-verification. It is evidence, not the working
backlog and not a statement of current product behaviour.

## Start with the component queues

Use the [component improvement catalogue](../roadmap/index.md) to choose work.
It assigns each candidate package to one component, puts it in an execution
order, and links back here for evidence. Do not create a second queue by
checking items off inside an old phase report.

Before implementation:

1. re-verify the package against `HEAD`;
2. update the owning component specification;
3. add the smallest failing regression;
4. implement and verify the package; and
5. update or retire the package in its component queue.

## Current audit status source

The latest audit-wide status is
[`06-reverification/REPORT.md`](06-reverification/REPORT.md), backed by
`06-reverification/status.json`. At that snapshot, 864 of 881 findings were
still valid and 17 were `FIXED` or `OBSOLETE`. The closed 17 are excluded from
the component queues. The remaining verdicts are still point-in-time and must
be checked again before work begins.

## Evidence map

| Path | Purpose |
|---|---|
| `00-map/` | Architecture and coverage baseline used to plan the audit. |
| `02-findings/` | Subsystem deep-dive findings, corrections, and reproductions. |
| `03-simplification/` | Behaviour-preserving simplification candidates and newly found bugs. |
| `04-exhaustive/` | File-by-file sweep, verified bugs, and coverage ledger. |
| `05-dimensions/` | Numerical, test, performance, type, security, CI, docs, dependency, API/DX, and frontend lenses. |
| `06-reverification/` | Most recent per-finding verdicts against the then-current branch. |
| `MASTER/` | Unified historical index and machine-readable finding records. |
| `_working/` | Intermediate machine artifacts and superseded process notes; ignore unless reconstructing the audit. |

The top-level remediation and architecture documents are historical
syntheses. Their clusters remain useful source packages, but ownership and
state now live in the component catalogue.

## Provenance and interpretation

The original audit was read-only and reviewed every file/function across five
phases and ten quality dimensions. Findings were reproduced or code-traced,
then normalized into the master index. Later re-verification checked the
records against a newer branch.

Line numbers, severity, and even applicability may have drifted. Preserve a
report when it carries unique evidence; remove it only after every accepted
outcome is represented by current specs/tests and Git remains the recovery
path.
