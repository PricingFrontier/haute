# Deploy and platform roadmap

## Scope

Owns deployment artifact/path handling, CLI scaffolding, validation and
container boundaries, process startup, and operating-system/resource
assumptions. Current behaviour is specified in
[deploy](../specs/deploy/high-level.md) and [CLI](../specs/cli/high-level.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---|---|
| `AUD-DEPLOY-01` | Reverify | P0 | Close remaining deployment path, scaffold, and actionable-configuration failures. |
| `AUD-C20` | Reverify | P1 | Make resource discovery and cross-platform file/event behaviour explicit. |
| `AUD-DEPLOY-02` | Reverify | P1 | Keep deploy and CLI documentation generated from or checked against shipped configuration. |

## Planned improvements

### AUD-DEPLOY-01 — Deployment path and scaffold integrity

**Why:** Deployment still has several high-impact boundaries where a malformed
path/config can bypass an earlier guard or a generated CI file can look valid
without being executable. The already-completed validate/serve artifact parity
is not part of this package.

**Plan:**

- Reverify every deploy-facing input path, including model
  `artifact_path`/`feature_contract_path`, after CLI/config resolution and
  before bundling.
- Make the no-`haute.toml` `--pipeline` path use the same canonical resolution
  as configured discovery instead of allowing a raw argument to overwrite it.
- Parse every generated GitHub, GitLab, and Azure pipeline as complete YAML and
  validate required environment/secret placement.
- Replace generic configuration failures with actionable messages naming the
  node, real sidecar/config field, rejected value class, and correction.
- Preserve the fail-loud deploy scorer contract for missing or unsupported
  model artifacts.

**Acceptance:**

- Traversal/out-of-project artifact paths fail before any file is copied or
  model loaded, including direct service/CLI calls.
- A discovered and an explicitly supplied pipeline resolve identically with
  and without `haute.toml`.
- Full generated CI documents parse and retain the expected deploy stages,
  secret names, indentation, and branch conditions.
- Missing model/config inputs produce stable domain errors and never deploy as
  identity passthroughs.

**Dependencies:** [Security](security-supply-chain.md) owns trust policy;
[pipeline authoring](pipeline-authoring.md) owns sidecar/DSL semantics.

**Evidence:** `src/haute/cli/_deploy.py`, `src/haute/deploy/_config.py`,
`src/haute/deploy/_bundler.py`, `src/haute/deploy/_scorer.py`,
`src/haute/routes/pipeline.py`, `src/haute/_scaffold.py`,
`tests/test_cli_deploy.py`, `tests/test_deploy_config.py`,
`tests/test_deploy_identity_parity.py`, `tests/test_scaffold.py`, and
`tests/test_docs_accuracy.py`.

### AUD-C20 — Platform resource and concurrency edges

**Why:** Host RAM is not container RAM, Windows replacement semantics differ
from POSIX, and the graph-update event payload type still omits data its
publisher sends.

**Plan:**

- Read cgroup v2 `memory.max`/`memory.current`, fall back to v1 limits, and
  clamp host-available memory to observable cgroup headroom.
- Keep `None` as the fail-loud/unavailable result when capacity cannot be
  observed; never fabricate a default capacity.
- Reverify every atomic cache/output replacement on Windows. Where readers can
  block rename, use a short bounded retry and then a typed integrity error.
- Make Windows temporary-file and path-case regressions respect open-handle and
  case-preserving filesystem semantics instead of relying on POSIX behaviour.
- Benchmark Linux RSS sampler setup before changing it; keep the current path
  when the measured cost is immaterial.
- Add `graph_fingerprint` to the closed graph-update payload and remove widened
  subscriber assumptions.

**Acceptance:**

- Linux tests cover finite/unlimited cgroup v2, v1 fallback, malformed files,
  and host-vs-container clamping.
- Windows contention tests either complete within the retry budget or raise the
  documented typed error without losing the old artifact.
- `tests/test_train_service_coverage.py` and `tests/test_path_case_audit.py`
  pass on supported Windows Python without weakening their cleanup/path
  assertions.
- The Linux sampler gate records a workload, artifact, and implement/no-change
  decision.
- Static typing rejects a graph update without its fingerprint and publisher,
  subscribers, and websocket payloads agree.

**Dependencies:** [Execution](execution-engine.md) consumes available-memory
admission; I/O owns provider cache publication.

**Evidence:** `src/haute/_ram_estimate.py`, `src/haute/_databricks_io.py`,
`src/haute/_event_bus.py`, `src/haute/server.py`,
`tests/test_ram_estimate.py`, `tests/test_databricks_io.py`, and
`tests/test_server.py`, `tests/test_train_service_coverage.py`, and
`tests/test_path_case_audit.py`.

### AUD-DEPLOY-02 — Documentation/runtime configuration parity

**Why:** Deployment guidance must not send a by-the-book user through a
different path, secret name, or generated file tree than the CLI produces.

**Plan:**

- Derive example paths, node counts, commands, and secret names from scaffold
  constants or executable fixtures wherever practical.
- Remove phantom commands/APIs and qualify portability, validation, and memory
  claims until the corresponding runtime invariant is true.
- Extend the docs-accuracy gate when a new target, scaffold field, or public
  command is added.

**Acceptance:**

- Every documented target secret and `pipeline =` path matches generated
  configuration.
- Before/after trees are produced from an actual scaffold fixture.
- Every documented command appears in CLI help and every named public Python
  surface imports.
- Documentation checks fail on a stale node count, target secret, pipeline
  path, or phantom command.

**Dependencies:** Component specs remain the behavioural authority; this
package consumes, rather than invents, their contracts.

**Evidence:** `README.md`, `docs/deployment/`, `src/haute/_scaffold.py`,
`src/haute/cli/`, `tests/test_docs_accuracy.py`, `tests/test_scaffold.py`, and
`tests/test_cli.py`.
