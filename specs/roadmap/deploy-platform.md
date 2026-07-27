# Deploy and platform roadmap

## Scope

Owns deployment artifact/path handling, CLI scaffolding, validation and
container boundaries, process startup, and operating-system/resource
assumptions. Current behaviour is specified in
[deploy](../deploy/high-level.md) and [CLI](../cli/high-level.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---|---|
| — | — | — | No active deploy/platform roadmap package remains. |

## Planned improvements

There are no active deploy/platform roadmap packages.

## Delivered outcomes

- `AUD-C20` clamps Linux host availability to finite cgroup v2 headroom with
  a v1 fallback while preserving `None`/unlimited/malformed semantics. Windows
  training-artifact publication retries only access-denied/sharing-violation
  contention, raises a typed integrity failure on exhaustion, and restores the
  old model/contract pair. `GraphUpdatePayload` now requires
  `graph_fingerprint`, matching its publishers, subscribers, and WebSocket
  frame. Linux controller regressions, Windows publication/rollback
  regressions, platform cleanup/path tests, and static typing cover the
  correctness contract.
- The proposed Linux RSS-sampler setup micro-optimisation is deliberately
  dropped rather than turned into an unobserved cache: sampling occurs at
  coarse admitted stage boundaries, not in a row/solver loop, and no runtime
  profile identifies it as material. A future real-Linux profile showing
  sampler p95 above 1 ms in a representative run is the explicit trigger for a
  separately scoped performance package; correctness does not depend on that
  optimisation.
- `AUD-DEPLOY-01` now canonicalises configured and standalone pipeline paths,
  enforces project containment for every local deploy read and MLflow
  identifier, bundles explicit feature contracts under one canonical key,
  limits fallback input discovery to `dataInput`, validates and projects
  `output_fields`, makes configured quote suites mandatory, and preserves
  unexpected backend exceptions while typing expected operational failures as
  `DeployError`.
- `AUD-DEPLOY-02` parses every generated CI document across all targets and
  structurally checks release flow and secret placement. Deployment
  documentation trees and starter-node counts are pinned to a real scaffold,
  documented commands are compared with root CLI help, and documented Haute
  Python surfaces must import; negative drift fixtures prove each parity gate
  fails closed.

**Evidence:** `src/haute/_ram_estimate.py`,
`src/haute/routes/_train_service.py`, `src/haute/_event_bus.py`,
`tests/test_ram_estimate.py`, `tests/test_training_worker_protocol.py`,
`tests/test_event_bus_gaps.py`, `tests/test_train_service_coverage.py`,
`tests/test_path_case_audit.py`, `src/haute/cli/_deploy.py`,
`src/haute/deploy/_config.py`,
`src/haute/deploy/_bundler.py`, `src/haute/deploy/_validators.py`,
`src/haute/deploy/_container.py`, `src/haute/deploy/_mlflow.py`,
`src/haute/_scaffold.py`, `docs/deployment/index.md`,
`tests/test_cli_deploy.py`, `tests/test_deploy_config_and_bundle.py`,
`tests/test_deploy_internals.py`, `tests/test_deploy_validators_gaps.py`,
`tests/test_scaffold.py`, and `tests/test_docs_accuracy.py`.
