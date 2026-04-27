# Haute - Deployment Strategy

**Status:** Partially implemented (P1–P3 + P9 done, P4–P8 pending). Container-first target architecture designed, not yet implemented.  
**Scope:** Config design, CI/CD, deploy targets, safety gates  

---

## 1. Problem

A pricing team builds a pipeline in Haute. They need to:

1. **Deploy it** to a live scoring API (Databricks, AWS, Azure, Docker, etc.)
2. **Release safely** - every change is tested, reviewed, impact-analysed, and auditable
3. **Configure once** - deployment target, CI provider, and safety thresholds defined in version-controlled config

The insurance industry adds a hard constraint: **a single wrong factor can misprice millions of pounds of premium before anyone notices**. The release process must catch bugs, unintended impact, and compliance violations - without slowing teams to a crawl.

Haute's competitive edge: all of this works out of the box from `haute init`. No DevOps team required.

---

## 2. Design Principles

1. **`haute.toml` is the single source of truth** - everything about *what* gets deployed, *where*, and *with what safety checks* lives in one committed file
2. **Secrets never go in config** - credentials live in `.env` (local) or CI secrets (remote), never in `haute.toml`
3. **Deploy targets and CI providers are independent choices** - you can deploy to Databricks from GitHub Actions, or to Docker from Azure DevOps, or any combination
4. **Convention over configuration** - sensible defaults for everything; most teams only need to fill in target-specific credentials
5. **Merge to main = deploy** - production deployments are triggered by merging to the main branch, never by running a manual command in production

---

## 3. What `haute init` Generates

```
my-pricing-project/
├── main.py                      # Starter pipeline
├── haute.toml                   # Project + deploy + safety config
├── .env.example                 # Credentials template (committed)
├── .env                         # Actual secrets (gitignored)
├── data/                        # Local data files
├── tests/                       # Tests + JSON test payloads
│   ├── quotes/
│   │   └── example.json
├── .github/                     # CI/CD workflows (if provider=github)
│   └── workflows/
│       ├── ci.yml               # PR checks: lint, test, validate
│       ├── deploy-staging.yml   # Merge-to-main: validate → staging → smoke → impact
│       └── deploy-production.yml  # Manual trigger: deploy to production
├── .gitignore
└── pyproject.toml               # Dependencies (haute added automatically)
```

For GitLab users (`--ci gitlab`), `.github/workflows/` is replaced with `.gitlab-ci.yml`.  
For Azure DevOps users (`--ci azure-devops`), `azure-pipelines.yml`.

---

## 4. Configuration Design - `haute.toml`

### 4.1 Target-Specific Generation

`haute init --target <target> --ci <provider>` generates a `haute.toml` containing **only** the sections relevant to the chosen target. No commented-out blocks, no unused target sections. Clean, minimal, obvious.

#### Example: `haute init --target databricks --ci github`

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
serving_workload_size = "Small"       # Small | Medium | Large
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

#### Example: `haute init --target docker --ci azure-devops`

```toml
[project]
name = "motor-pricing"
pipeline = "main.py"

[deploy]
target = "docker"
model_name = "motor-pricing"
endpoint_name = "motor-pricing"

[deploy.docker]
registry = ""                         # e.g. "ghcr.io/myorg", "123456.dkr.ecr.eu-west-1.amazonaws.com"
port = 8080
base_image = "python:3.11-slim"

[test_quotes]
dir = "tests/quotes"

[safety]
impact_dataset = "data/portfolio_sample.parquet"

[safety.approval]
min_approvers = 2

[ci]
provider = "azure-devops"

[ci.staging]
endpoint_suffix = "-staging"
```

#### Example: `haute init --target sagemaker --ci gitlab`

```toml
[project]
name = "motor-pricing"
pipeline = "main.py"

[deploy]
target = "sagemaker"
model_name = "motor-pricing"
endpoint_name = "motor-pricing"

[deploy.sagemaker]
region = "eu-west-1"
instance_type = "ml.m5.large"
initial_instance_count = 1

[test_quotes]
dir = "tests/quotes"

[safety]
impact_dataset = "data/portfolio_sample.parquet"

[safety.approval]
min_approvers = 2

[ci]
provider = "gitlab"

[ci.staging]
endpoint_suffix = "-staging"
```

### 4.2 All Target-Specific Sections (reference)

These are never in the same file - only the one matching `--target` is generated.

```toml
# ── Databricks ────────────────────────────────────────────────
[deploy.databricks]
experiment_name = "/Shared/haute/motor-pricing"
catalog = "main"
schema = "pricing"
serving_workload_size = "Small"       # Small | Medium | Large
serving_scale_to_zero = true

# ── Docker ────────────────────────────────────────────────────
[deploy.docker]
registry = ""                         # e.g. "ghcr.io/myorg"
port = 8080
base_image = "python:3.11-slim"

# ── AWS SageMaker ─────────────────────────────────────────────
[deploy.sagemaker]
region = "eu-west-1"
instance_type = "ml.m5.large"
initial_instance_count = 1

# ── Azure ML ──────────────────────────────────────────────────
[deploy.azure-ml]
resource_group = ""
workspace_name = ""
instance_type = "Standard_DS3_v2"
instance_count = 1
```

### 4.3 What Goes Where - The Separation

| Setting | Where | Committed to git? | Example |
|---|---|---|---|
| Deploy target, model name, endpoint | `haute.toml [deploy]` | ✅ Yes | `target = "databricks"` |
| Target-specific infra config | `haute.toml [deploy.<target>]` | ✅ Yes | `catalog = "main"`, `instance_type = "ml.m5.large"` |
| Safety config | `haute.toml [safety]` | ✅ Yes | `impact_dataset = "data/portfolio.parquet"` |
| CI provider and pipeline shape | `haute.toml [ci]` | ✅ Yes | `provider = "github"` |
| CI workflow files | `.github/workflows/` etc. | ✅ Yes | `ci.yml`, `deploy-staging.yml`, `deploy-production.yml` |
| Workspace credentials | `.env` (local) | ❌ No | `DATABRICKS_TOKEN=dapi...` |
| Workspace credentials (CI) | CI secrets | ❌ No | GitHub repo secrets |
| Test quote payloads | `tests/quotes/*.json` | ✅ Yes | `single_policy.json` |
| Impact comparison dataset | `data/` | ✅ Yes | `portfolio_sample.parquet` |

### 4.4 Why One File, Not Many

We considered `deployment/databricks.yaml`, `deployment/docker.yaml` etc. but rejected it:

- **More files = more confusion** for pricing teams who aren't DevOps engineers
- All targets share the same top-level fields (`model_name`, `endpoint_name`)
- Only one target is active at a time (`[deploy].target`)
- `haute init` generates only the relevant target section - no dead config
- If a team needs to switch target, they edit the `[deploy]` section and add the new `[deploy.<target>]` section - or re-run `haute init` in a clean project

### 4.5 Credentials by Target

Each deploy target reads credentials from environment variables. `haute init` generates a `.env.example` containing **only** the credentials for the chosen target - not a master list of every possible provider.

#### `--target databricks` generates:
```bash
# Haute - Databricks credentials
# Copy to .env and fill in your values. .env is gitignored.
DATABRICKS_HOST=https://adb-1234567890123456.12.azuredatabricks.net
DATABRICKS_TOKEN=your_token_here
```

#### `--target sagemaker` generates:
```bash
# Haute - AWS SageMaker credentials
# Copy to .env and fill in your values. .env is gitignored.
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_DEFAULT_REGION=eu-west-1
SAGEMAKER_ROLE_ARN=arn:aws:iam::123456789012:role/SageMakerRole
```

#### `--target azure-ml` generates:
```bash
# Haute - Azure ML credentials
# Copy to .env and fill in your values. .env is gitignored.
AZURE_SUBSCRIPTION_ID=
AZURE_TENANT_ID=
AZURE_CLIENT_ID=
AZURE_CLIENT_SECRET=
```

#### `--target docker` generates:
```bash
# Haute - Docker registry credentials (optional)
# Only needed if pushing to a private registry.
# Copy to .env and fill in your values. .env is gitignored.
DOCKER_USERNAME=
DOCKER_PASSWORD=
```

---

## 5. Deploy Targets

### 5.1 Key Insight: This is a Pricing API

Haute deploys a **pricing API** - quote data in, premium out. The pipeline graph
contains models (CatBoost, GLMs) as internal nodes, but the deployed artefact is
the entire pipeline, not a single `model.predict()` call.

This means ML-specific platforms (SageMaker, Azure ML) are one option for teams
already using them, not the natural default. The natural default is a **FastAPI
app in a Docker container** - the same way any other API gets deployed.

### 5.2 Target Architecture

All targets consume the same `ResolvedDeploy` dataclass (already implemented). The target-specific layer is thin - it only handles "package this pipeline and give me a URL".

```
haute.toml ──→ DeployConfig ──→ resolve_config()
                                    │
                  target-agnostic   │  _pruner.py     prune graph
                                    │  _bundler.py    collect artifacts
                                    │  _schema.py     infer schemas
                                    │  _validators.py  run test quotes
                                    │  _scorer.py     dry-run scoring
                                    │  _impact.py     staging vs prod
                                    │
                                    ▼
                              ResolvedDeploy
                                    │
                  target-specific   │  dispatch on [deploy].target
                                    │
                    ┌────────────────┼────────────────┐
                    ▼                ▼                ▼
              _mlflow.py       _container.py    (future: _sagemaker, _azure_ml)
              (databricks)     (FastAPI+Docker)  (thin wrappers around container)
```

### 5.3 Target Summary

| Category | Target | `[deploy].target` | Install extra | What it produces | Status |
|---|---|---|---|---|---|
| **Container** | Container (FastAPI) | `"container"` | (none - core) | Docker image with FastAPI app, `/quote` + `/health` | **Next** |
| **ML platform** | Databricks | `"databricks"` | `haute[databricks]` | MLflow model → Databricks Model Serving endpoint | ✅ Implemented |
| **ML platform** | AWS SageMaker | `"sagemaker"` | `haute[aws]` | Container → ECR → SageMaker endpoint | Planned |
| **ML platform** | Azure ML | `"azure-ml"` | `haute[azure]` | Container → ACR → Azure ML endpoint | Planned |

Credential env vars per target:

| Target | Env vars |
|---|---|
| `container` | `DOCKER_USERNAME`, `DOCKER_PASSWORD` (optional, for registry push) |
| `databricks` | `DATABRICKS_HOST`, `DATABRICKS_TOKEN` |
| `sagemaker` | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION`, `SAGEMAKER_ROLE_ARN` |
| `azure-ml` | `AZURE_SUBSCRIPTION_ID`, `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET` |

### 5.4 Install Extras

```bash
# Container (no extra needed - uses FastAPI + Polars, both already core)
uv add haute

# Databricks
uv add "haute[databricks]"     # mlflow, databricks-sdk

# AWS SageMaker (planned)
uv add "haute[aws]"            # boto3, sagemaker

# Azure ML (planned)
uv add "haute[azure]"          # azure-ai-ml, azure-identity

# Multiple targets
uv add "haute[databricks,aws]"
```

### 5.5 Container Target - The Universal Default

The container target is the **recommended path** for most teams. It produces a
self-contained Docker image that runs anywhere - ECS, Container Apps, Cloud Run,
Kubernetes, a VM, or a laptop.

```bash
# Set target = "container" in haute.toml, then:
haute deploy
# → Builds: Dockerfile, FastAPI app, docker-compose.yml
# → Optionally pushes to a registry
```

The generated container:
- **FastAPI app** wrapping `score_graph()` with `POST /quote` for single-quote objects and batch arrays
- **Health check** at `GET /health` (with version info)
- **Baked-in** pipeline graph, model files, and static data
- **No pandas** - JSON → Polars → JSON, no MLflow shim
- **No runtime dependency** on Databricks, AWS, or Azure
- **Configurable** via environment variables

`/quote/batch` is no longer a separate endpoint. Clients that previously called
`POST /quote/batch` must send the same batch payload to `POST /quote`; the
stable response envelope is returned per scored row.

Why container-first:
- It's just an API - teams already know how APIs work
- No ML platform lock-in or MLOps-specific knowledge required
- Standard API patterns: container tags for versioning, blue/green deploys, health checks
- Cheaper than ML-specific compute pricing
- SageMaker and Azure ML targets are thin wrappers around this (push image + create endpoint)

### 5.6 Rigour Requirements (All Targets)

Every target must satisfy the same safety bar. These are **Haute's responsibility**,
not the hosting platform's:

| Requirement | Why (pricing/insurance) | Provided by |
|---|---|---|
| **Staging environment** | Can't push untested pricing to production | Haute CI pipeline |
| **Impact analysis** | Must compare new vs old premiums before go-live | `haute impact` |
| **Smoke test** | Score known quotes against staging, assert sane outputs | `haute smoke` |
| **Rollback** | Revert to previous version within minutes | Container tags / model versions |
| **Versioned deployments** | Audit trail - "which pipeline version priced policy X?" | Git tags + image/model tags |
| **Reproducibility** | Given a version tag, rebuild the exact same artefact | Pinned deps + deterministic build |
| **Health check** | Endpoint liveness/readiness for monitoring | `/health` endpoint |
| **No secrets in artefact** | Credentials injected at runtime, never baked in | Env vars at deploy time |

---

## 6. CI/CD Strategy

### 6.1 The Release Flow

```
feature branch ──→ PR ──→ merge to main ──→ deploy
       │              │              │
    local dev      CI gate      CD pipeline
       │              │              │
  haute lint       ruff check      validate (re-run all checks)
  haute test       mypy .              │
  haute run        pytest              ├─→ deploy staging (auto)
                   haute lint          │     smoke test
                   haute deploy        │     impact analysis
                     --dry-run         ├─→ [approval gate]
                                       │     (reviewer checks impact report)
                                       └─→ deploy production
```

### 6.2 CI Provider Support

| Provider | Config key | Generated file(s) | Status |
|---|---|---|---|
| **GitHub Actions** | `provider = "github"` | `.github/workflows/ci.yml`, `deploy-staging.yml`, `deploy-production.yml` | v1 |
| **Azure DevOps** | `provider = "azure-devops"` | `azure-pipelines.yml` | v1 |
| **GitLab CI** | `provider = "gitlab"` | `.gitlab-ci.yml` | v1 |
| **None** | `provider = "none"` | No CI files generated | v1 |

All providers run the same logical steps - only the YAML syntax differs.

### 6.3 GitHub Actions - CI Workflow (`ci.yml`)

Runs on every PR to `main`:

| Job | What it runs | Blocks merge on failure? |
|---|---|---|
| **lint** | `ruff check .` + `ruff format --check .` | Yes |
| **typecheck** | `mypy .` | Yes |
| **test** | `pytest -v` | Yes |
| **pipeline-validate** | `haute lint` + `haute deploy --dry-run` | Yes |

### 6.4 GitHub Actions - Staging Workflow (`deploy-staging.yml`)

Runs automatically on push to `main` (i.e. after PR merge):

| Job | What it does |
|---|---|
| **validate** | Re-runs lint, typecheck, test, pipeline validation on merge commit |
| **deploy-staging** | `haute deploy --endpoint-suffix "-staging"` |
| **smoke-test** | Score test quotes against the live staging endpoint |
| **impact-analysis** | `haute impact --endpoint-suffix "-staging"` - compares staging vs production predictions, writes `impact_report.md` + GitHub Step Summary |

### 6.5 GitHub Actions - Production Workflow (`deploy-production.yml`)

Triggered manually via the GitHub Actions UI (`workflow_dispatch`) after reviewing the impact report:

| Job | What it does |
|---|---|
| **deploy-production** | `haute deploy` - deploys the current main branch to the production endpoint |
| **tag** | Git tag with version: `deploy/v{model_version}` |

This split works on **GitHub Free** - no environment protection rules needed. The manual trigger is the approval gate. On GitHub Team/Enterprise, teams can optionally merge these into a single workflow with environment protection rules on `production`.

### 6.6 Azure DevOps - Pipeline (`azure-pipelines.yml`)

Same logical flow, Azure syntax:

```yaml
trigger:
  branches:
    include: [main]

pr:
  branches:
    include: [main]

stages:
  - stage: CI              # runs on PR
    jobs: [lint, typecheck, test, pipeline_validate]

  - stage: DeployStaging   # runs on merge to main
    condition: eq(variables['Build.SourceBranch'], 'refs/heads/main')
    jobs: [deploy_staging, smoke_test]

  - stage: DeployProd      # manual approval gate
    condition: succeeded()
    jobs: [deploy_production]
    # Uses Azure DevOps Environments with approval checks
```

Credentials use Azure DevOps **variable groups** or **service connections** instead of GitHub secrets - but the env var names are identical (`DATABRICKS_HOST`, etc.).

### 6.7 No Shortcuts - Every Team Gets the Full Pipeline

Insurance pricing carries risk regardless of team size. A solo actuary mispricing a book is just as dangerous as a large team doing it. Every `haute init` project gets the full pipeline:

```
merge to main → validate → deploy staging → smoke test → impact analysis → [approval gate] → deploy production
```

There is no "simplified flow". The staging environment exists to catch issues in a production-like setting before real money is at stake. The impact analysis compares pricing changes between the new model (staging) and the current model (production) so reviewers see exactly what's changing. The approval gate exists because deploying rates should always require a second pair of eyes.

Teams can adjust the `min_approvers` count in `haute.toml`, but the staging→impact→approval→production structure is non-negotiable. The workflow enforces ordering (production depends on impact-analysis), and the manual approval gate on the `production` environment gives reviewers control.

---

## 7. Safety Gates

### 7.1 Test Quotes - Golden File Testing

Already implemented. Every deploy scores `tests/quotes/*.json` through the pruned pipeline and blocks on failure.

**Enhancement needed:** Support for **expected outputs** - not just "it doesn't crash" but "this input produces this exact price". This is the golden file pattern:

```json
[
  {
    "input": {"VehPower": 7, "VehAge": 3, "DrivAge": 42, "Area": "C"},
    "expected": {"technical_price": 384.0, "premium": 548.57},
    "tolerance_pct": 0.01
  }
]
```

If the output drifts beyond `tolerance_pct`, the deploy is blocked with a diff showing exactly which quotes changed and by how much.

### 7.2 Impact Report - `haute impact`

The headline safety feature. Scores the impact dataset through both the staging endpoint (new model) and the production endpoint (current model), then compares predictions:

```
================================================================
  IMPACT REPORT
  Pipeline:    motor-pricing
  Staging:     motor-pricing-staging  →  Production: motor-pricing
  Dataset:     data/portfolio_sample.parquet (10,000 of 678,013 sampled)
================================================================

  Output: technical_price
  ────────────────────────────────────────────────────────────
  Staging mean:      548.57     Production mean:   536.12
  Rows changed:        8,421 / 10,000 (84.2%)
  Mean change:          +2.3%
  Median change:        +1.8%
  Premium impact:       +2.3%
  Max increase:        +18.7%
  Max decrease:         -4.2%

  Distribution:  P5=-2.1%  P25=+0.5%  P50=+1.8%  P75=+3.4%  P95=+7.2%

  ⚠ 147 quotes changed by more than ±25.0%
  ✓ Average change (+2.3%) within ±10.0% threshold

  Segment: Region
  Value                   Rows    Avg Change   Stg Mean   Prod Mean
  Ile-de-France           2,341       +4.1%     572.34       549.87
  Picardie                  423       -0.3%     501.23       502.78
```

The report is:
- **Always** written to `impact_report.md` (portable artifact for any CI platform)
- **GitHub Actions**: additionally written to `$GITHUB_STEP_SUMMARY` for inline rendering on the workflow run page
- **GitLab CI**: `impact_report.md` is collected as a pipeline artifact

Reviewers check the impact report before approving the production deployment. The report is informational - it does not block the pipeline, but the manual approval gate gives reviewers full control.

For first-time deployments (no production endpoint exists yet), the report notes this and skips comparison.

The segment breakdown auto-detects categorical input columns (2–50 unique values) and reports top segments by absolute average change.

### 7.3 Approval Gates

Configured in `haute.toml [safety.approval]` and enforced via CI provider:

| Provider | How approval is enforced |
|---|---|
| **GitHub** | Branch protection rules + environment protection rules |
| **Azure DevOps** | Environment approval checks |
| **GitLab** | Protected environments with required approvals |

`haute init` can generate the branch protection config (GitHub) or print setup instructions for other providers.

### 7.4 Rollback

```bash
haute rollback motor-pricing           # revert to previous model version
haute rollback motor-pricing --to 3    # revert to specific version
```

For Databricks: updates the serving endpoint to point to the previous model version (instant, no redeployment).  
For Docker: re-tags the previous image as `latest` and triggers a redeploy.  
For SageMaker/Azure ML: updates the endpoint to the previous model version.

On git: creates a revert commit on main, which triggers the normal deploy flow.

### 7.5 Audit Trail

Every deployment produces a record (stored in the deploy manifest + MLflow run metadata):

```json
{
  "pipeline": "motor-pricing",
  "version": 3,
  "git_sha": "a1b2c3d",
  "git_branch": "main",
  "deployed_by": "ci/github-actions",
  "timestamp": "2026-02-16T15:30:00Z",
  "impact_report_sha": "e4f5g6h",
  "test_quotes_passed": true,
  "pr_number": 47,
  "pr_approvers": ["jane", "alex"],
  "previous_version": 2,
  "target": "databricks",
  "endpoint": "motor-pricing"
}
```

---

## 8. Decisions Made

### D1: `haute init` is flag-driven, generates only relevant files

```bash
haute init                                    # defaults: databricks + github
haute init --target docker --ci azure-devops  # Docker + Azure DevOps
haute init --target sagemaker --ci gitlab     # AWS + GitLab
haute init --target docker --ci none          # Docker, no CI (manual deploy only)
```

- Only the chosen target's `[deploy.<target>]` section appears in `haute.toml`
- Only the chosen target's credentials appear in `.env.example`
- Only the chosen CI provider's workflow files are generated
- No commented-out blocks, no unused sections

### D2: Every team gets staging → approval gate → production

No simplified flow. Insurance pricing carries risk regardless of team size. The full pipeline is non-negotiable:

```
merge to main → validate → deploy staging → smoke test → impact analysis → [approval gate] → deploy production
```

### D3: `HAUTE_` env var overrides for environment-specific config

Staging and production may need different infra settings (e.g. workload size). Rather than duplicating config across files, use environment variables:

```
Resolution order (highest wins):
  1. CLI flags          --model-name foo
  2. Environment vars   HAUTE_MODEL_NAME=foo
  3. haute.toml         model_name = "foo"
  4. Auto-detection     pipeline file stem
```

CI workflow sets `HAUTE_SERVING_WORKLOAD_SIZE=Medium` in the production job's environment. The `haute.toml` value is the base (staging default), and env vars override for production.

### D4: `haute.toml` says *what*, workflow file says *how*

- `haute.toml [ci]` declares intent: "this project uses GitHub Actions"
- The generated workflow file contains the actual YAML steps
- `haute init` generates the workflow file **once** - teams own it from that point
- No re-generation on config changes (avoids the "managed file you can't customise" antipattern)

### D6: Same target for staging and production

Staging and production always use the same deploy target. If you deploy to Databricks, both staging and production are Databricks endpoints. No mixing Docker staging with Databricks production.

This keeps the deployment pipeline simple and ensures staging is a true replica of production. The only differences between staging and production are:

- **Endpoint name** - staging gets the `-staging` suffix (e.g. `motor-pricing-staging`)
- **Infra overrides** - production may use larger workload sizes via `HAUTE_` env vars
- **Approval gate** - production requires human approval, staging auto-deploys

```toml
[deploy]
target = "databricks"          # one target, used for both staging and production
model_name = "motor-pricing"
endpoint_name = "motor-pricing"

[ci.staging]
endpoint_suffix = "-staging"   # deploys to "motor-pricing-staging"
```

### D8: Solo mode is a config change, not a CLI flag

`haute init` always generates team defaults (`min_approvers = 2`). A solo developer edits one value in `haute.toml`:

```toml
[safety.approval]
min_approvers = 0
```

The full pipeline still runs identically - validate → staging → smoke test → impact analysis → production. The only difference is the team doesn't require multiple sign-offs. The impact report is still generated so the developer can review changes. When the team grows, bump the values back up. No workflow regeneration needed.

### D9: `pyproject.toml` extras

```
haute[databricks]   → mlflow, databricks-sdk      (existing, keep as-is)
haute[aws]          → boto3, sagemaker             (planned)
haute[azure]        → azure-ai-ml, azure-identity  (planned)
```

Container target needs no extra - it uses FastAPI (already core) + Polars (already core).
SageMaker and Azure ML targets no longer require MLflow - they use the container
target's FastAPI image with platform-specific push/endpoint creation.

---

## 9. No Open Questions

All design questions have been resolved. See §8 Decisions Made.

---

## 10. Implementation Order

| Phase | What | Depends on | Status |
|---|---|---|---|
| **P1** | Expand `haute.toml` schema with `[safety]` and `[ci]` sections | - | ✅ Done |
| **P2** | `haute init` generates CI/CD workflow files based on `[ci].provider` | P1 | ✅ Done (GitHub + GitLab + Azure DevOps) |
| **P3** | `haute impact` command + endpoint comparison + Step Summary | P1 | ✅ Done |
| **P4** | Golden file test quotes (expected outputs with tolerance) | Existing tests/quotes infra | Pending |
| **P5** | Container deploy target (`_container.py`) - FastAPI app + Dockerfile generation | Existing `ResolvedDeploy` | **Next** |
| **P5a** | Target dispatch in `deploy()` + `NotImplementedError` for unimplemented targets | P5 | **Next** |
| **P6** | AWS SageMaker deploy target (`_sagemaker.py`) - thin wrapper: push image to ECR + create endpoint | P5 | Planned |
| **P7** | Azure ML deploy target (`_azure_ml.py`) - thin wrapper: push image to ACR + create endpoint | P5 | Planned |
| **P8** | `haute rollback` command | P5 | Planned |
| **P9** | Azure DevOps CI template | P2 | ✅ Done |

---

## 11. Summary

- **This is a pricing API, not an ML model** - quote data in, premium out. Models are internal nodes, not the deployed artefact
- **Container-first** - the default target is a FastAPI app in a Docker container; ML platforms (Databricks, SageMaker, Azure ML) are alternatives for teams already on those platforms
- **One config file** (`haute.toml`) defines project, deploy target, safety thresholds, and CI shape
- **Secrets in env vars** - `.env` locally, CI secrets remotely, `HAUTE_` prefix for overrides
- **Deploy targets are pluggable** - all share `ResolvedDeploy`, only the final packaging step differs
- **Same rigour bar for every target** - staging, impact analysis, smoke test, rollback, versioning, health check, no secrets in artefact
- **Merge to main = deploy** - every change is tested, reviewed, and impact-analysed before production
- **`haute init --target <t> --ci <p>` scaffolds everything** - only the relevant config, credentials, and CI/CD workflows for the chosen target and provider
- **No shortcuts** - every team gets staging → impact analysis → approval gate → production, regardless of size
- **CI-only deploys** - `haute deploy` only runs in CI/CD environments; local deploys are always blocked (use `--dry-run` to validate locally)
