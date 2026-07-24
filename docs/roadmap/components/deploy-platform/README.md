# Deploy and platform improvement backlog

## Scope

Owns validation-to-serving equivalence, deployment artifact resolution,
scaffolding/CLI platform behaviour, process startup boundaries, and
cross-platform filesystem/resource assumptions. Current contracts live in the
[deploy](../../../specs/deploy/high-level.md) and
[CLI](../../../specs/cli/high-level.md) specifications.

## Work queue

| Package | State | Priority | Candidate outcome | Source |
|---|---|---|---|---|
| AUD-C04 | Reverify | P0 | Make the test-before-live path score the exact bundled artifacts that the deployed container serves. | [Audit cluster C4](../../../review/REMEDIATION-PLAN.md#c4-deploy-validate-vs-serve-artifact-divergence-test-before-live-gate-loads-a-different-model) |
| AUD-DEPLOY-01 | Reverify | P0 | Close the remaining deploy path, artifact-path guard, scaffold, and user-facing configuration failures in the must-fix cut. | [Re-verification Wave 5](../../../review/06-reverification/REPORT.md#wave-5--frontendbackend-contract--remaining-verified-highs--14-items) |
| AUD-C20 | Reverify | P1 | Harden cgroup RAM discovery, Windows file-sharing, and platform event/concurrency edges. | [Audit cluster C20](../../../review/REMEDIATION-PLAN.md#c20-platform-numericalconcurrency-edge-hardening-cgroup-ram-windows-file-sharing-eventbus-typing) |
| AUD-DEPLOY-02 | Reverify | P1 | Reconcile CLI/deployment documentation and generated secret/path examples with current runtime configuration. | [Documentation work package](../../../review/REMEDIATION-PROGRAM.md#c5-documentation-accuracy-the-docs-that-send-a-by-the-book-user-into-a-wall) |

## Dependencies

- [Caching](../caching/README.md) owns the artifact-identity fingerprint
  primitive used by validation and schema caches.
- [Security and supply chain](../security-supply-chain/README.md) owns path,
  deserialisation, and session-token trust boundaries.
- [Execution engine](../execution-engine/README.md) owns runtime strategy and
  admission; deploy consumes those contracts without a parallel planner.

## Evidence and retirement

Audit packages require a fresh reproduction against `HEAD`. Retire
validate/serve work only after one regression proves byte-identical artifact
selection through both paths. Platform packages require explicit Windows and
POSIX evidence or a documented support decision in the owning specifications.
