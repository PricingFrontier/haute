# Optimiser roadmap

## Scope

Optimiser configuration, numerical solve/frontier behaviour, artifacts,
ratebooks, performance, interruptibility, and workflows remain reliable.
Current behaviour is specified in [the optimiser specification](../optimiser/low-level.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| OPT-P14 | Planned | P2 | Complete solver/result publication extraction. |

## Planned improvements

The setup steps are free functions in `_optimiser_input.py` that raise typed
failures (`OptimiserSetupError`); the service records them. The frontier sweep
stays serial (the `OPT-P06` measurement), and the frontier domain is
`OptimiserFrontierService` in `_optimiser_frontier.py`. `OPT-P14` extracts the
solver layer; later packages must not bypass those isolation boundaries. No optimiser path reads pipeline
rows in the server process: the input estimate runs on the warm interactive
worker pool and auto-range sizes its chunks in its worker.

### OPT-P14 — Extract solver execution and result publication
**Why:** Online/ratebook construction, `SolveContext`, result normalisation, inline frontier
policy, factor-table serialisation, and terminal publication are the final cohesive solver layer.

**Plan:** Move solver-context entry points and result builders to
`src/haute/routes/_optimiser_solver.py`; leave `OptimiserSolveService` as job admission plus
setup/worker composition. Retain the worker-context guard at the extracted public boundary.

**Acceptance:** Online/ratebook solve, cancellation, inline-frontier, golden response, factor
dtype, and save/apply agreement suites pass; `_optimiser_service.py` is an orchestration module
rather than a mixed domain/utilities module.

**Dependencies:** None.

**Evidence:** `src/haute/routes/_optimiser_service.py`; `tests/test_optimiser_routes.py`;
`tests/test_optimiser_golden.py`; `tests/test_optimiser_ratebook_apply_agreement.py`.
