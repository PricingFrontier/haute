# Fable Review

Deep engineering reviews of Haute subsystems, one folder per review area. Reviews are
point-in-time evidence: no source was changed while they were written, and fixes are implemented
separately with failing tests and the normal repository gates.

Completed review packages are removed once their accepted fixes or measured no-change decisions
are represented by current specifications and regression tests. The Polars backend review was
retired after the [v0.6.0 remediation](../trip/changelog/v0.6.0.md), and the tracing review was
retired after its behaviour moved into the [tracing specification](../specs/tracing/high-level.md)
and ordinary test suites.

The remaining packages each contain at least one unresolved or not-yet-reverified item:

The review folders retain detailed evidence and TDD plans. Working state,
ordering, cross-component dependencies, and retirement are owned by the
[component improvement catalogue](../roadmap/index.md).

| Review area | Owning component queue | Why it remains |
|---|---|---|
| [EDA node](eda-node/README.md) | [Explore / EDA](../roadmap/components/explore-eda/README.md) | Correctness, scalability, export, and UX packages still require a current closure pass. |
| [Git implementation](git-implementation/README.md) | [Git integration](../roadmap/components/git-integration/README.md) | Several proposed contracts are not current behaviour, including global mutation serialisation and removal of the status surface. |
| [I/O nodes](io-nodes/README.md) | [I/O layer](../roadmap/components/io-layer/README.md) | The review spans correctness, schema, format, performance, and editor packages that are not closed as one programme. |
| [Modelling node](modelling-node/README.md) | [Modelling](../roadmap/components/modelling/README.md) | Its implementation plan and capability packages have not all met their exit criteria. |
| [Optimisation](optimisation/README.md) | [Optimiser](../roadmap/components/optimiser/README.md) | Worker, lifecycle, scaling, and upstream packages still contain tracked work or explicit external follow-ups. |
