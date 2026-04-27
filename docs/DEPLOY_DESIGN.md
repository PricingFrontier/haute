# Haute Deploy - Design Document

**Status:** Databricks and generic container targets implemented. Platform container targets (Azure Container Apps, AWS ECS, GCP Cloud Run) scaffolded - build+push works, service update pending.  
**Scope:** Deploy targets, configuration, CI/CD, safety gates, technical design  

---

## 1. Problem

A pricing team builds a pipeline in Haute. They need to deploy it as a **live scoring API** so policy admin systems can send a JSON quote and receive a premium - with one command, no DevOps.

Haute deploys a **pricing API**, not an ML model in the MLOps sense. The pipeline takes quote data in, runs it through a graph of transforms and models, and returns a premium. Models (CatBoost, GLMs) are internal nodes - the deployed artefact is the entire pipeline.

The insurance industry adds a hard constraint: **a single wrong factor can misprice millions of pounds of premium before anyone notices**. The release process must catch bugs, unintended impact, and compliance violations.

The full pipeline contains branches irrelevant for live scoring (training data joins, exports). Deployment **prunes** to the scoring path only:

```
claims ──→ claims_aggregate ─┐
                              ├─→ frequency_set → frequency_write   (NOT deployed)
exposure ─────────────────────┘

policies ──→ frequency_model ──→ calculate_premium ──→ output       ← DEPLOYED
policies ──→ severity_model  ──┘                                    ← DEPLOYED
```

---

## 2. Design Principles

1. **`haute.toml` is the single source of truth** - what gets deployed, where, and with what safety checks
2. **Secrets never go in config** - credentials live in `.env` (local) or CI secrets (remote)
3. **Deploy targets and CI providers are independent choices** - Databricks + GitLab, Container + GitHub, any combination
4. **Convention over configuration** - sensible defaults; most teams only fill in credentials
5. **Merge to main = deploy** - production deployments triggered by merging, never by manual command
6. **Any target, same rigour** - Databricks, container, SageMaker, or Azure ML - the safety pipeline is identical

---

## 3. Deploy Philosophy

Haute owns the **full release cycle** - from `git push` to live production endpoint. The analyst merges to main and Haute's CI pipeline handles everything: build, push, service update, smoke test, impact analysis, promotion.

**IT provisions infrastructure once.** The platform team creates the container app / ECS service / Cloud Run service, sets up networking, configures the registry. This is a one-time setup.

**The pricing team owns every release from there.** Haute calls the platform SDK to update the running service with the new image. No tickets, no handoffs, no waiting for IT on every deploy.

This mirrors the Databricks target where Haute calls the Databricks SDK to create/update the serving endpoint - the same pattern, different SDK.

### 3.0 Local vs CI Commands

Haute is designed so analysts never need Docker, cloud CLIs, or DevOps tooling installed locally. The split:

| Command | Where it runs | Needs Docker? | Needs cloud creds? |
|---|---|---|---|
| `haute init` | Local | No | No |
| `haute serve` | Local | No | No |
| `haute deploy` | **CI only** | Yes (CI runner has it) | Yes (CI secrets) |
| `haute smoke` | **CI only** | No | Yes (CI secrets) |
| `haute impact` | **CI only** | No | Yes (CI secrets) |

The analyst's workflow is: edit pipeline → `haute serve` to preview → `git push` → CI does the rest. If an analyst runs `haute deploy` locally for a container target, they'll get a clear error pointing them to CI.

### 3.1 Target Architecture

Targets fall into three categories:

1. **Databricks** - MLflow/Databricks SDK. Fully implemented.
2. **Container-based platforms** - share a common build+push step (FastAPI app + Docker image), then each calls its own platform SDK to update the running service.
3. **ML platforms** - SageMaker, Azure ML. Different packaging approach. Planned.

All container-based targets share:
- The same `[deploy.container]` config (registry, port, base_image)
- The same FastAPI app generation (`POST /quote`, `GET /health`)
- The same `Dockerfile` and `docker build` + `docker push`
- The same smoke test and impact analysis (HTTP-based)

What differs per platform is the **service update** step after push.

---

## 4. Deploy Targets

### 4.1 Target Table

| Target | `[deploy].target` | Build | Service Update | Status |
|---|---|---|---|---|
| **Databricks** | `"databricks"` | MLflow pyfunc model | Databricks SDK | ✅ Implemented |
| **Container** | `"container"` | Docker image (FastAPI) | None (local/manual) | ✅ Implemented |
| **Azure Container Apps** | `"azure-container-apps"` | Docker image (FastAPI) | Azure SDK | Build+push ✅, update pending |
| **AWS ECS** | `"aws-ecs"` | Docker image (FastAPI) | AWS SDK | Build+push ✅, update pending |
| **GCP Cloud Run** | `"gcp-run"` | Docker image (FastAPI) | GCP SDK | Build+push ✅, update pending |
| **AWS SageMaker** | `"sagemaker"` | - | - | Planned |
| **Azure ML** | `"azure-ml"` | - | - | Planned |

The team picks the target that matches their infrastructure. `haute init --target <t>` generates the right config, credentials template, and CI/CD workflows.

### 4.2 Databricks Target

For teams already on Databricks. Wraps the pipeline in an `mlflow.pyfunc.PythonModel` shim (Databricks Model Serving requires the MLflow protocol). Pandas bridge at the boundary only - all internal computation remains Polars.

### 4.3 Container Target (Generic)

For local testing, Kubernetes, or environments where IT manages the service externally. Produces a self-contained Docker image with a FastAPI app wrapping `score_graph()` - `POST /quote`, `GET /health`, no MLflow, no pandas. Build and push only - no service update.

### 4.4 Platform Container Targets

Azure Container Apps, AWS ECS, and GCP Cloud Run are first-class targets. They share the container build+push step and then call the platform SDK to update the running service:

- **Azure Container Apps** - creates a new revision via the Azure SDK
- **AWS ECS** - calls `UpdateService` to deploy the new task definition
- **GCP Cloud Run** - deploys the new image via the Cloud Run SDK

The service update step is not yet implemented - Haute builds and pushes the image, then raises `NotImplementedError` with a message telling you the image tag so you can update manually until the SDK integration lands.

### 4.5 SageMaker / Azure ML Targets

Planned. Different packaging approach from container targets - these ML platforms have their own model packaging format and endpoint management.

### 4.6 Rigour Requirements (All Targets)

Every target must satisfy the same safety bar - these are **Haute's responsibility**, not the platform's:

| Requirement | Provided by |
|---|---|
| **Staging environment** | Haute CI pipeline |
| **Impact analysis** (new vs old premiums) | `haute impact` |
| **Smoke test** (known quotes against staging) | `haute smoke` |
| **Rollback** (revert within minutes) | Container tags / model versions |
| **Versioned deployments** (audit trail) | Git tags + image/model tags |
| **Reproducibility** | Pinned deps + deterministic build |
| **Health check** | `/health` endpoint |
| **No secrets in artefact** | Env vars at deploy time |

### 3.6 Install Extras

```bash
uv add haute                       # container target (no extras needed)
uv add "haute[databricks]"         # mlflow, databricks-sdk
uv add "haute[aws]"                # boto3, sagemaker (planned)
uv add "haute[azure]"              # azure-ai-ml, azure-identity (planned)
```

---

## 4. Configuration

### 4.1 `haute.toml`

All deploy settings live in a single TOML file. Created by `haute init`, committed to git.

```toml
[project]
name = "motor-pricing"
pipeline = "main.py"

[deploy]
target = "databricks"
model_name = "motor-pricing"
endpoint_name = "motor-pricing"

[deploy.databricks]
experiment_name = "/Shared/haute/motor-pricing"
catalog = "main"
schema = "pricing"
serving_workload_size = "Small"
serving_scale_to_zero = true

[test_quotes]
dir = "tests/quotes"

[safety]
impact_dataset = "data/portfolio_sample.parquet"

[safety.approval]
min_approvers = 2

[ci]
provider = "github"

[ci.staging]
endpoint_suffix = "-staging"
```

### 4.2 Target-Specific Sections (reference)

Only the one matching `--target` is generated - never in the same file:

```toml
# ── Container ────────────────────────────────────────────────
[deploy.container]
registry = ""                         # e.g. "ghcr.io/myorg"
port = 8080
base_image = "python:3.11-slim"

# ── Databricks ───────────────────────────────────────────────
[deploy.databricks]
experiment_name = "/Shared/haute/motor-pricing"
catalog = "main"
schema = "pricing"
serving_workload_size = "Small"
serving_scale_to_zero = true

# ── AWS SageMaker ────────────────────────────────────────────
[deploy.sagemaker]
region = "eu-west-1"
instance_type = "ml.m5.large"
initial_instance_count = 1

# ── Azure ML ─────────────────────────────────────────────────
[deploy.azure-ml]
resource_group = ""
workspace_name = ""
instance_type = "Standard_DS3_v2"
instance_count = 1
```

### 4.3 What Lives Where

| Setting | Location | Committed? |
|---|---|---|
| Deploy target, model name, endpoint | `haute.toml [deploy]` | Yes |
| Target-specific infra config | `haute.toml [deploy.<target>]` | Yes |
| Safety config + impact dataset | `haute.toml [safety]` | Yes |
| CI provider + staging config | `haute.toml [ci]` | Yes |
| Credentials | `.env` (local) or CI secrets | **No** |
| Test quote JSON payloads | `tests/quotes/` | Yes |

### 4.4 Resolution Order

```
1. CLI flags          --model-name foo          (highest)
2. Environment vars   HAUTE_MODEL_NAME=foo
3. haute.toml         model_name = "foo"
4. Auto-detection     pipeline file stem        (lowest)
```

### 4.5 Credentials by Target

| Target | Env vars |
|---|---|
| `container` | `DOCKER_USERNAME`, `DOCKER_PASSWORD` (optional) |
| `databricks` | `DATABRICKS_HOST`, `DATABRICKS_TOKEN` |
| `sagemaker` | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION`, `SAGEMAKER_ROLE_ARN` |
| `azure-ml` | `AZURE_SUBSCRIPTION_ID`, `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET` |

---

## 5. Technical Architecture

### 5.1 Module Structure

```
src/haute/deploy/
  __init__.py             # Public API: deploy(), DeployConfig, dispatch by target
  _config.py              # DeployConfig, ResolvedDeploy, haute.toml loading
  _pruner.py              # Graph pruning to output ancestors
  _bundler.py             # Artifact discovery and collection
  _scorer.py              # score_graph() - runtime scoring engine (shared by all targets)
  _schema.py              # Input/output schema inference
  _validators.py          # Pre-deploy validation (dry-run, artifact checks)
  _impact.py              # Impact analysis: staging vs production comparison

  # ── Target-specific (only the final "package + push" step differs) ──
  _mlflow.py              # Databricks: deploy_to_mlflow(), HauteModel shim
  _model_code.py          # Databricks: models-from-code script for MLflow
  _container.py           # Container: generate Dockerfile + FastAPI app (planned)
```

### 5.2 Target-Agnostic Pipeline

```
                      target-agnostic
                ┌─────────────────────────┐
haute.toml ──→ DeployConfig ──→ resolve_config()
                                    │
                  _pruner.py       prune graph
                  _bundler.py      collect artifacts
                  _schema.py       infer schemas
                  _validators.py   run test quotes
                  _scorer.py       dry-run scoring
                  _impact.py       staging vs prod
                                    │
                                    ▼
                            ResolvedDeploy
                └─────────────────────────┘
                                    │
                      target-specific
                ┌─────────────────────────┐
                │  "databricks" → _mlflow  │
                │  "container"  → _container│
                └─────────────────────────┘
```

`ResolvedDeploy` is the clean handoff point. A new target only consumes it and does its own packaging.

### 5.3 Key Data Structures

```python
@dataclass
class DeployConfig:
    """User-provided config (from haute.toml + CLI)."""
    pipeline_file: Path
    model_name: str
    endpoint_name: str | None = None
    output_fields: list[str] | None = None
    test_quotes_dir: Path | None = None
    databricks: DatabricksConfig = field(default_factory=DatabricksConfig)

@dataclass
class ResolvedDeploy:
    """Computed state after resolving config against a parsed pipeline."""
    config: DeployConfig
    pruned_graph: dict
    input_node_ids: list[str]
    output_node_id: str
    artifacts: dict[str, Path]
    input_schema: dict[str, str]
    output_schema: dict[str, str]
```

### 5.4 Dispatch

```python
def deploy(config: DeployConfig) -> DeployResult:
    resolved = resolve_config(config)
    if config.target == "databricks":
        return deploy_to_mlflow(resolved)
    if config.target == "container":
        return deploy_to_container(resolved)
    raise NotImplementedError(f"Target '{config.target}' is not yet implemented")
```

No `Protocol` or base class until three targets exist.

### 5.5 Data Flow - Container Target

```
POST /quote  →  JSON → pl.DataFrame → score_graph() → { rows, row_count, returned_rows, truncated, limit } → JSON
```

No pandas. JSON in, Polars throughout, JSON out.

### 5.6 Data Flow - Databricks Target

```
POST /invocations  →  pd.DataFrame → pl.from_pandas() → score_graph() → .to_pandas()
```

Pandas bridge at the boundary only (MLflow contract). Documented exception to "Polars-native" rule.

### 5.7 Pre-Deploy Validation

| Check | Description |
|---|---|
| Pipeline parseable | File parses without syntax errors |
| Output node exists | Declared output node is in the graph |
| Input node exists | Input node is in the pruned graph |
| All artifacts exist | Every model/data file is on disk |
| Dry-run passes | Score 1 sample row, confirm no runtime errors |
| Output has rows | Dry-run produces at least 1 output row |

### 5.8 Deployment Manifest

Every deploy produces `deploy_manifest.json` - the single source of truth for a deployed pipeline:

```json
{
    "haute_version": "0.1.0",
    "pipeline_name": "my_pipeline",
    "created_at": "2026-02-15T16:06:00Z",
    "created_by": "ralph",
    "input_nodes": ["policies"],
    "output_node": "output",
    "input_schema": {"Area": "String", "VehPower": "Int64", "...": "..."},
    "output_schema": {"technical_price": "Float64", "premium": "Float64"},
    "artifacts": {"freq_model": "models/freq.cbm", "sev_model": "models/sev.cbm"},
    "nodes_deployed": 5,
    "nodes_skipped": 7
}
```

---

## 6. CI/CD

### 6.1 Release Flow

```
feature branch ──→ PR ──→ merge to main ──→ deploy
       │              │              │
    local dev      CI gate      CD pipeline
       │              │              │
  haute lint       ruff, mypy      validate
  haute run        pytest              │
  --dry-run        haute lint          ├─→ deploy staging (auto)
                   --dry-run           │     smoke test
                                       │     impact analysis
                                       ├─→ [approval gate]
                                       └─→ deploy production
```

### 6.2 CI Provider Support

| Provider | Config key | Generated file(s) | Status |
|---|---|---|---|
| **GitHub Actions** | `"github"` | `ci.yml`, `deploy-staging.yml`, `deploy-production.yml` | ✅ |
| **GitLab CI** | `"gitlab"` | `.gitlab-ci.yml` | ✅ |
| **Azure DevOps** | `"azure-devops"` | `azure-pipelines.yml` | ✅ |
| **None** | `"none"` | No CI files | ✅ |

All providers run the same logical steps - only the YAML syntax differs.

### 6.3 No Shortcuts

Every `haute init` project gets the full pipeline regardless of team size:

```
merge to main → validate → deploy staging → smoke test → impact analysis → [approval gate] → deploy production
```

Solo developers set `min_approvers = 0` in `haute.toml` - the pipeline still runs identically, just without requiring sign-off.

---

## 7. Safety Gates

### 7.1 Test Quotes

Every deploy scores `tests/quotes/*.json` through the pruned pipeline. If any file fails, deployment is blocked. Catches schema mismatches, runtime errors, model loading failures, and edge case crashes.

**Planned:** Golden file pattern - expected outputs with tolerance:

```json
[{"input": {"VehPower": 7, "Area": "C"}, "expected": {"premium": 548.57}, "tolerance_pct": 0.01}]
```

### 7.2 Impact Report

`haute impact` scores a portfolio sample through both staging (new) and production (current), then compares:

```
Output: technical_price
  Staging mean:     548.57     Production mean:   536.12
  Mean change:       +2.3%    Rows changed:      84.2%
  Max increase:     +18.7%    Max decrease:       -4.2%

  ⚠ 147 quotes changed by more than ±25.0%
  ✓ Average change (+2.3%) within ±10.0% threshold
```

Written to `impact_report.md` (all providers) + `$GITHUB_STEP_SUMMARY` (GitHub) + pipeline artifact (GitLab).

### 7.3 Approval Gates

| Provider | Mechanism |
|---|---|
| **GitHub** | `workflow_dispatch` manual trigger (Free) or environment protection rules (Team/Enterprise) |
| **Azure DevOps** | Environment approval checks |
| **GitLab** | `when: manual` on production job |

### 7.4 Rollback

```bash
haute rollback motor-pricing           # revert to previous version
haute rollback motor-pricing --to 3    # revert to specific version
```

Databricks: updates serving endpoint to previous model version. Container: re-tags previous image and redeploys.

### 7.5 Audit Trail

Every deployment records: pipeline name, version, git SHA, deployer, timestamp, impact report hash, test quote pass/fail, PR approvers, previous version, target, endpoint.

---

## 8. Design Decisions

**D1: `haute init` generates only relevant files** - only the chosen target's config, credentials, and CI workflows. No commented-out blocks.

**D2: Graph JSON is the deployment unit** - not the `.py` file. The pruned graph is self-contained, inspectable, and requires no import resolution.

**D3: Same scoring engine for dev and prod** - `_build_node_fn` with a thin wrapper that intercepts source nodes and redirects artifact paths. No separate execution engine.

**D4: Manifest-driven deployment** - `deploy_manifest.json` is the single source of truth. Inspectable, reproducible, debuggable.

**D5: Same target for staging and production** - no mixing container staging with Databricks production. Only differences: endpoint suffix, infra overrides via `HAUTE_` env vars, approval gate.

**D6: `haute.toml` says *what*, workflow file says *how`** - `haute init` generates the workflow once, teams own it from there. No re-generation.

**D7: No abstraction until three targets** - no `Protocol`, no base class. When a third target arrives, the interface is obvious from the concrete implementations.

**D8: pandas bridge is Databricks-only** - container target uses JSON → Polars → JSON directly. The MLflow bridge is a documented exception.

---

## 9. Implementation Status

| Phase | What | Status |
|---|---|---|
| **P1** | `haute.toml` schema: `[safety]`, `[ci]` sections | ✅ Done |
| **P2** | `haute init` generates CI/CD workflows (GitHub + GitLab + Azure DevOps) | ✅ Done |
| **P3** | `haute impact` + endpoint comparison + Step Summary | ✅ Done |
| **P4** | Golden file test quotes (expected outputs with tolerance) | Pending |
| **P5** | Container target (`_container.py`) - FastAPI + Dockerfile | **Next** |
| **P5a** | Target dispatch + `NotImplementedError` for unimplemented targets | **Next** |
| **P6** | SageMaker target - thin wrapper: push to ECR + create endpoint | Planned |
| **P7** | Azure ML target - thin wrapper: push to ACR + create endpoint | Planned |
| **P8** | `haute rollback` command | Planned |

### Module-level status (Databricks target)

All implemented and tested (323 tests as of v0.1.24):

`_pruner.py`, `_config.py`, `_bundler.py`, `_schema.py`, `_scorer.py`, `_model_code.py`, `_validators.py`, `_mlflow.py`, `_impact.py`, CLI commands (`deploy`, `smoke`, `impact`, `status`), CI/CD scaffolds.
