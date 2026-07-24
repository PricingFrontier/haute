# Engineering quality improvement backlog

## Scope

Owns shared invariant/oracle strategy, production-shaped fixtures, regression
policy, test health, CI enforcement, type-contract generation, and
documentation accuracy. Feature components retain ownership of their own
behaviour and feature-specific tests. Current policy lives in the
[engineering-quality specification](../../../specs/engineering-quality/high-level.md).

## Work queue

| Package | State | Priority | Candidate outcome | Source |
|---|---|---|---|---|
| ROAD-TEST-01 | Active | P0 | Inventory high-risk boundaries and ratchet each with an owner, invariant, and evidence tier. | [Test-suite milestone 1](../../test-suite-hardening.md#milestone-1--boundary-contract-inventory-and-ratchet) |
| ROAD-TEST-02 | Active | P0 | Complete optimiser property and chunk-oracle matrices around production shapes. | [Test-suite milestone 2](../../test-suite-hardening.md#milestone-2--complete-the-optimiser-property-and-chunk-oracle-matrix) |
| ROAD-TEST-03 | Active | P0 | Extend ratebook canonicalisation properties across save/apply dtype boundaries. | [Test-suite milestone 3](../../test-suite-hardening.md#milestone-3--ratebook-canonicalisation-properties) |
| ROAD-TEST-04 | Active | P1 | Add seeded parser fuzzing and differential evidence against real Polars semantics. | [Test-suite milestone 4](../../test-suite-hardening.md#milestone-4--expression-parser-fuzzing-and-polars-differential-evidence) |
| ROAD-TEST-05 | Active | P1 | Establish cumulative regression, fixture provenance, and test-health policy. | [Test-suite milestone 5](../../test-suite-hardening.md#milestone-5--regression-fixture-and-test-health-policy) |
| AUD-QUALITY-01 | Reverify | P1 | Generate or encode closed backend/frontend contract vocabularies instead of maintaining stringly typed mirrors. | [Type-design package](../../../review/REMEDIATION-PROGRAM.md#c2-stringly-typed--literalstrenum-closing-typo-routes-to-wrong-branch-holes) |
| AUD-QUALITY-02 | Reverify | P1 | Invert or replace tests that currently pin a known bug before fixing the underlying behaviour. | [Bug-pinning test package](../../../review/REMEDIATION-PROGRAM.md#c4-the-12-tests-that-codify-bugs-delete-or-invert-before-fixing) |
| AUD-QUALITY-03 | Reverify | P2 | Batch remaining CI, documentation-truth, dependency-monitoring, and static-analysis debt by policy rather than isolated edits. | [Tracked-debt summary](../../../review/06-reverification/REPORT.md#tracked-debt-opportunistic) |

## Dependencies

- Each feature component owns the expected behaviour and the smallest
  regression that proves its package.
- [Frontend and canvas](../frontend-canvas/README.md) owns UI journey,
  visual, keyboard, and accessibility semantics; this queue owns shared
  harness/tier policy only.
- [Security and supply chain](../security-supply-chain/README.md) owns whether
  a dependency advisory is actionable; this queue owns the monitoring gate.

## Evidence and retirement

The [test-suite hardening roadmap](../../test-suite-hardening.md) is the active
acceptance source. Audit-derived policy packages must be rechecked against
current configuration. Retire a package only when its convention has a named
owner, executable enforcement where appropriate, and no competing duplicate
policy.
