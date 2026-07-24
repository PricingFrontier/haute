# Edge Join improvement backlog

## Scope

Owns the remaining user-facing completion work for the `edgeJoin` node:
discoverable insertion, role-preserving browser behaviour, and accurate
interaction/join-mode documentation. Runtime join semantics are already
defined in the
[pipeline-config specification](../../../specs/pipeline-config/high-level.md).

## Work queue

| Package | State | Priority | Candidate outcome | Source |
|---|---|---|---|---|
| ROAD-EDGE-01 | Active | P0 | Show accessible pre-release feedback when a source connection can insert an Edge Join into an existing edge. | [Edge Join milestone 1](../../edge-join-completion.md#1-make-edge-insertion-discoverable-and-accessible) |
| ROAD-EDGE-02 | Active | P1 | Add a deterministic browser journey for insertion, configuration, preview, persistence, repeated joins, named handles, and tracing. | [Edge Join milestone 2](../../edge-join-completion.md#2-exercise-the-real-browser-workflow) |
| ROAD-EDGE-03 | Active | P1 | Align public/spec documentation with role-handle geometry and all supported Polars join modes. | [Edge Join milestone 3](../../edge-join-completion.md#3-align-public-and-specification-documentation) |

## Dependencies

- [Frontend and canvas](../frontend-canvas/README.md) owns shared browser
  fixtures, visual/accessibility conventions, and generic connection UX.
- [Engineering quality](../engineering-quality/README.md) owns shared oracle
  and regression-policy conventions.
- Backend execution or join-semantics changes require a newly demonstrated
  contract gap; they are not implied by this completion queue.

## Evidence and retirement

The [Edge Join completion roadmap](../../edge-join-completion.md) contains the
full tests-first acceptance criteria. Remove this component queue when all
three packages pass and ongoing behaviour is carried by public docs, specs,
and ordinary component/browser tests.
