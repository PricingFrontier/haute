# Explore / EDA improvement backlog

## Scope

Owns Explore report correctness, bounded statistics collection, cache
interaction, panel state, chart/relationship capability, export, and
analyst-facing data-quality semantics. Current behaviour is specified under
[explore-eda](../../../specs/explore-eda/high-level.md).

## Work queue

| Package | State | Priority | Candidate outcome | Source |
|---|---|---|---|---|
| EDA-E01 | Reverify | P0 | Keep ordinary Duration columns from crashing the whole report. | [Duration value-count crash](../../../fable-Review/eda-node/E01-duration-value-counts-crash.md) |
| EDA-E02 | Reverify | P0 | Make NaN/inf, null distinctness, constant detection, and percentage text numerically honest. | [Silent stats wrongness](../../../fable-Review/eda-node/E02-silent-stats-wrongness.md) |
| EDA-E03 | Reverify | P0 | Make statistics collection memory-bounded and cancellable. | [Memory-safe stats collection](../../../fable-Review/eda-node/E03-memory-safe-stats-collect.md) |
| EDA-E04 | Reverify | P0 | Avoid synchronously content-hashing unchanged large inputs on warm requests. | [Stat-gated input fingerprint](../../../fable-Review/eda-node/E04-stat-gated-input-fingerprint.md) |
| EDA-E05 | Reverify | P1 | Count binary values natively and isolate decode failures. | [Binary value counts](../../../fable-Review/eda-node/E05-binary-native-value-counts.md) |
| EDA-E06 | Reverify | P1 | Remove or deliberately replace empty/dead tabs. | [Dead tabs](../../../fable-Review/eda-node/E06-dead-tabs.md) |
| EDA-E07 | Reverify | P1 | Make stale, loading, failure, and default panel states explicit. | [Panel state UX](../../../fable-Review/eda-node/E07-panel-state-ux.md) |
| EDA-E08 | Reverify | P1 | Keep cards scalable and accessible at wide schemas. | [Card scalability and accessibility](../../../fable-Review/eda-node/E08-card-scalability-a11y.md) |
| EDA-E09 | Reverify | P2 | Add server-binned distribution charts on the bounded report/cache path. | [Charts and histograms](../../../fable-Review/eda-node/E09-charts-histograms.md) |
| EDA-E10 | Reverify | P2 | Add target-aware one-way relationship analysis. | [Relationships analysis](../../../fable-Review/eda-node/E10-relationships-target-analysis.md) |
| EDA-E11 | Reverify | P2 | Extend the data-quality profile through explicit bounded packages. | [Quality profile extensions](../../../fable-Review/eda-node/E11-quality-profile-extensions.md) |
| EDA-E12 | Reverify | P2 | Wire a deliberate export workflow and supporting guidance. | [Export wiring](../../../fable-Review/eda-node/E12-export-wiring.md) |
| EDA-E13 | Reverify | P2 | Bound cache lifetime and harden job/cache paths. | [Cache and job robustness](../../../fable-Review/eda-node/E13-cache-robustness.md) |

## Dependencies

- EDA-E03 follows EDA-E01 and EDA-E02 because all three reshape the same
  statistics path.
- EDA-E09 through EDA-E11 should consume the bounded collection established by
  EDA-E03.
- [Caching](../caching/README.md) owns generic fingerprint/lifetime policy;
  this component owns Explore-specific cache behaviour.

## Evidence and retirement

The [Explore Fable review](../../../fable-Review/eda-node/README.md) provides
ordering, evidence, TDD plans, rejected scope, and the regression-protection
list. Reverify each package against `HEAD`; remove the component queue when all
accepted packages are implemented or explicitly declined and durable behaviour
is represented by specs/tests.
