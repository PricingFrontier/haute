# Haute engineering roadmap

This folder is the single source of truth for planned engineering
improvements. Each active component roadmap contains the problem,
implementation direction, acceptance criteria, dependencies, and current
code/test evidence needed to take one package through delivery.

Shipped behaviour remains defined by code, tests, and the component
specifications. `Reverify` packages came from older evidence and must be
reproduced against `HEAD` before implementation. `Decision` packages require
an explicit product or architecture choice. Remove a package when its outcome
is covered by current specifications and ordinary regression tests.
`Start with` names a non-deferred package; it is `—` when a component has no
currently startable package. Priority `P1` marks a package that threatens
persisted work, computed results, or security; `P2` a material correctness, UX,
or maintenance issue; `P3` opportunistic work.

| Component | Improvement surface | Start with |
|---|---|---|
| [Assistant](assistant.md) | One capability catalogue and example format; harnesses out of the runtime package | `ASSIST-R01` |
| [Background jobs and API lifecycle](background-jobs-api.md) | Worker terminal states, artifacts, events, cleanup, one worker primitive | `ROAD-WORKER-05` |
| [Build and distribution](build-and-distribution.md) | A node reference that documents the node types that exist | `BUILD-R01` |
| [Caching](caching.md) | Automatic API-input table snapshots, planning and housekeeping cost, the shapes that cannot carry a write recipe, chunked-write bounds, cache usage, freshness and retention | `CACHE-S08` |
| [Codegen](codegen.md) | Config-backed decorators emitted directly | `CODEGEN-R01` |
| [Deploy](deploy.md) | A slim scoring runtime, offered targets | `DEP-R02` |
| [Engineering quality](engineering-quality.md) | Dead code, tracked artifacts, specification drift, coverage gates, test organisation | `ENGQ-R01` |
| [Execution engine](execution-engine.md) | Chunked runner, one execution walker, execution context | `EXEC-R05` |
| [Explore and EDA](explore-eda.md) | Report correctness, scale, UX, pivot tables, PivotCharts, analysis, export | `EDA-E09` |
| [Expression parsing](expression-parsing.md) | Traced formulas evaluated by Polars | `EXPR-R01` |
| [Frontend modelling and optimiser UI](frontend-modelling-optimiser-ui.md) | Shared result tabs, target configuration and charts; why a training estimate is unavailable | `FMO-R01` |
| [Frontend node editors](frontend-node-editors.md) | API Input and Output shared block | `FNE-R01` |
| [Frontend shared](frontend-shared.md) | Debounce, modal and table bases; results store | `FSH-R02` |
| [IO layer](io-layer.md) | One dtype vocabulary, one atomic write and file lock | `IO-R01` |
| [JSON shredding](json-shredding.md) | One non-finite float encoding, explicit output nesting | `JSON-R01` |
| [MLflow model registry](mlflow-model-registry.md) | Explicit MLflow clients | `MLF-R02` |
| [Modelling](modelling.md) | RustyStats 0.9.0 upgrade, GLM terms and interactions; invariant checks | `MOD-T00` |
| [Optimiser](optimiser.md) | Apply/save correctness, scaling, lifecycle, workers, auto-range, input isolation | `OPT-P11` |
| [Pipeline config](pipeline-config.md) | Repair, save validation, the error a rejected config reports, project context, typed configs, node specification | `PCFG-R01` |
| [Sandbox security](sandbox-security.md) | One path-containment check, the node-code guard | `SBX-R01` |
| [Server API](server-api.md) | Error translation, generated browser contract, recovery scope | `API-R01` |
| [Submodels](submodels.md) | One reuse mechanism | `SUB-R01` |
| [Tracing](tracing.md) | Row identity for traces, the preview-reader abstraction | `TRACE-R02` |

## Codebase review — 23 September 2026

The [codebase review](codebase-review-2026-09-23.md) is a whole-repository
review for brittleness, over-engineering, weak areas with stronger standard
solutions, and duplication, made at `main` `9319b11d`. It records its method,
limits and evidence, and maps every finding to the package that tracks it.
It is a dated supporting report and owns no work; the packages in the
component roadmaps above carry the plans.

## PR #227 review — 22 September 2026

The [cache and pipeline review](pr-227-review.md) records reproduced defects,
targeted verification, CI evidence and the merge recommendation for head
`97f3e99`. The [focused implementation plan](pipeline-cache-memory-design.md)
is the current plan for the existing Polars/Parquet pipeline and store, narrowed
at the user's request. Supporting probes, inventories and benchmark results are
linked from the review. The Fable report and its [reconciliation](pr-227-fable-reconciliation.md)
are review history; use the current plan for implementation scope. These are
dated review artifacts; component specifications remain the behavior authority.

The subsequent [independent Fable 5.1 review](pr-227-fable-5.1-review.md)
challenges the proposed architecture and validates the findings against source.
Read the [parent reconciliation](pr-227-fable-reconciliation.md) for accepted
simplifications, corrected assumptions and the refined next steps. Its
[runtime provenance](pr-227-fable-5.1-provenance.json) verifies the exact model;
the Fable report is preserved verbatim.

The [implementation evidence](pr-227-implementation-progress.md) records the
subsequent fixes, reproducible before/after memory and runtime measurements,
targeted tests, and remaining CI verification for the focused plan.

The supporting inventories record [cache lifecycle evidence](pr-227-cache-evidence.md),
[materialisation evidence](pr-227-materialisation-evidence.md), and
[CI implementation evidence](pr-227-ci-implementation-evidence.md). The
[Fable review request](pr-227-fable-review-request.md) preserves the scope and
instructions supplied for that independent review.

## MOD-F00 engine probes — 23 September 2026

The [engine probe record](mod-f00-engine-probes.md) holds the dependency,
packaging and native-behaviour evidence that settled the pre-implementation
gates of the model-family expansion in the [modelling roadmap](modelling.md).
It is a dated evidence artifact; component specifications remain the behavior
authority.

## MOD-F05 CPU release check — 23 September 2026

The [release check](mod-f05-release-check.md) maps every acceptance row of the
model-family expansion to its test evidence and records the release benchmarks,
EBM format limits and platform requirements. It is a dated evidence artifact;
component specifications remain the behavior authority.

## MOD-F06 GPU probes — 23 September 2026

The [GPU probes](mod-f06-gpu-probes.md) record the XGBoost CUDA and LightGBM
GPU backend evidence behind XGBoost GPU training, `haute gpu-setup`, and the
decision to keep LightGBM and EBM on the CPU. It is a dated evidence artifact;
component specifications remain the behavior authority.

## Working protocol

1. Pick one package from its owning component.
2. For `Reverify`, reproduce the stated failure against `HEAD`; retire the
   package if current code and tests already prove the outcome.
3. Update the owning component specification before changing behaviour.
4. Add the smallest failing regression, implement the change, and run the
   affected verification ladder.
5. Update or remove the package so this folder remains current.

Package IDs are stable and component-owned. Cross-component consumers name a
dependency instead of copying the package.
