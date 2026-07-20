# Deployment

Deployment packages a pricing pipeline for a serving target. For **Databricks**, Haute also registers the model and creates or updates a Model Serving endpoint. For the **container** target, Haute builds an image (and pushes it when a registry is configured); your platform team runs that image. The Azure Container Apps, AWS ECS, and GCP Cloud Run adapters currently stop after the image push and require a manual service update.

!!! warning "New to Haute? Start here."
    If you haven't installed Haute yet, start with **[Getting Started](../getting-started/index.md)** - it covers installing everything and running your first `haute serve`. If you don't know what a pull request, CI/CD, or staging means, read **[Before You Start](before-you-start.md)** next - it explains every deployment concept in plain English.

!!! tip "Haven't built your pipeline yet?"
    These docs assume you already have a working pricing pipeline (`rating/main.py`). If you haven't created one yet, start with the **Building Pipelines** guide first, then come back here when you're ready to deploy.

`haute init` generates CI examples, but they are ordinary workflow files that your team owns. They run Haute commands in sequence; they do **not** themselves provision infrastructure, create a staging environment, or enforce an approval policy. Review the generated workflow and configure your CI provider's own branch protections, environments, and approvals before relying on it for a release process.

---

## Your workflow

As a pricing analyst, your day-to-day workflow is:

1. **Edit your pipeline** - change your Python file, update a model, adjust a transform
2. **Preview it** - run `haute serve` to open the visual editor and check everything looks right
3. **Push and open a pull request** - CI (an automated checker - see [Before You Start](before-you-start.md#what-is-cicd)) automatically validates your pipeline
4. **Merge to main** - the generated workflow can run a deploy, smoke test, and impact analysis in that order
5. **Review the result** - check logs and any impact report; choose a provider-level approval process appropriate for your team
6. **Promote deliberately** - run the production workflow or deployment process your team has configured

You never need to run `haute deploy`, install Docker, or manage cloud credentials on your machine. The CI runner handles all of that.

### What happens behind the scenes?

When a CI runner invokes `haute deploy`, Haute:

1. **Parses your pipeline** - reads your Python file and builds a graph of all the steps
2. **Prunes to the scoring path** - removes training steps, data exports, and anything not needed for live scoring
3. **Collects artifacts** - finds all the model files (e.g. `.cbm`, `.pkl`) your pipeline references and bundles them
4. **Validates** - runs your test quotes through the pruned pipeline to make sure it works
5. **Packages and uploads** - wraps everything into the format the selected target expects and uploads it where supported
6. **Dispatches by target** - Databricks creates or updates Model Serving; `container` returns the image for a separate hosting step; the Azure, ECS, and GCP adapters fail after building/pushing because their service-update integrations are not implemented

---

## Choosing a target

A **target** is where your pipeline will run in production. Haute supports several:

| Target | Best for | What you need |
|---|---|---|
| [**Databricks**](targets/databricks.md) | Teams already using Databricks | A Databricks workspace - the simplest option, no containers involved |
| [**Docker**](targets/docker.md) | Companies without Databricks | IT takes the package and deploys it on their infrastructure |
| [**AWS ECS**](targets/aws.md) | Teams on AWS (with IT support) | An AWS account and a manual ECS service-update handoff; the built image is pushed before Haute exits with an unimplemented-adapter failure |
| [**Azure Container Apps**](targets/azure.md) | Teams on Azure (with IT support) | An Azure subscription and a manual Container Apps revision handoff; the built image is pushed before Haute exits with an unimplemented-adapter failure |
| GCP Cloud Run | Teams on GCP (with IT support) | Config target is recognised, but its service update is not implemented; use the image tag in the failure message for a manual update |
| SageMaker / Azure ML | Planned targets | Recognised by scaffolding/configuration but rejected before deployment with `NotImplementedError` |

You pick your target once when you set up the project. The command is:

```powershell
haute init --target databricks
```

This generates all the deployment files you need. You don't write them by hand - `haute init` creates them for you. Here's what your project folder looks like before and after:

```
Before haute init:          After haute init:
my-project/                 my-project/
  main.py                     pyproject.toml       ← haute added as a dependency
  pyproject.toml              haute.toml           ← project, deploy & CI config
                              .env.example         ← credential template
                              .gitignore           ← keeps .env safe
                              rating/main.py       ← starter pipeline
                              rating/utility/      ← project-level utility functions
                              data/                ← put your data files here
                              prompts/             ← reusable AI prompts
                              tests/quotes/        ← test data for validation
                              .githooks/           ← auto-format on commit
                              .github/workflows/   ← CI/CD pipeline files
```

Two existing files are touched: a root `main.py` left over from `uv init` is **removed** (your pipeline lives at `rating/main.py` instead), and `pyproject.toml` is updated to list `haute` as a dependency. Everything else is additive - if a file like `.gitignore` already exists, Haute appends to it rather than replacing it. If the project is already initialised (a `haute.toml` exists), `haute init` refuses to run unless you pass `--force`.

!!! tip "Not sure which target to pick?"
    If your organisation uses Databricks, start with the **Databricks** target - it's the most mature and requires the least infrastructure setup. If you don't have Databricks, use **Docker** to start and move to a cloud target later.

---

## The configuration file

The most important generated file is `haute.toml` - a plain text file that says what gets deployed and where. You don't need to write it from scratch; `haute init` creates it pre-filled for your chosen target. Here's what a typical one looks like:

```toml
[project]
name = "motor-pricing"
pipeline = "rating/main.py"

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

Each section is explained in detail on the target-specific pages. The key idea is: **`haute.toml` says *what* gets deployed and *where***. It never contains passwords or secrets.

---

## Credentials

Every target needs credentials to authenticate - for example, a Databricks access token or a Docker registry password. These are **never** stored in `haute.toml` or committed to your repository.

Credentials live in **two places**:

- **On your laptop** - in a `.env` file, so you can call the live endpoint locally (e.g. to run your own impact comparisons before pushing). Copy `.env.example` to `.env` and fill in the values. This file is gitignored and never shared.
- **In your CI provider** - as encrypted secrets (GitHub Secrets, GitLab CI/CD Variables, or Azure DevOps Variable Groups), so the automated deploy pipeline can use them. Your IT team or tech lead usually sets these up once.

Both use the same credential values. The `.env.example` file in your project lists exactly what's needed - give it to whoever sets up the CI secrets.

The target-specific pages and the [CI/CD setup guides](ci/github-actions.md) explain exactly which secrets to add and how.

---

## Test quotes

Before every deployment, Haute scores your **test quotes** - example JSON payloads that represent real requests your API will receive. If any of them fail, the deployment is blocked.

Test quotes live in `tests/quotes/` as JSON files:

```json
[
  {
    "IDpol": 99001,
    "VehPower": 7,
    "DrivAge": 42,
    "Area": "C",
    "VehBrand": "B12"
  }
]
```

This catches problems early: schema mismatches, missing model files, runtime errors. Think of it as a sanity check that runs automatically before every deploy.

---

## Validation and release controls

Haute validates a deployment graph and scores configured test quotes before a non-dry-run target dispatch. It also provides `haute smoke` and `haute impact` commands for an already-running endpoint. These are useful release building blocks, not a managed safety system:

| Safety check | What it does |
|---|---|
| **Dry-run validation** | Parses the pipeline, checks all model files exist, scores test quotes |
| **Staging deployment** | `--endpoint-suffix` chooses a different name; the target and your infrastructure must provide that endpoint |
| **Smoke testing** | `haute smoke` scores configured test quotes against an existing Databricks or HTTP endpoint |
| **Impact analysis** | `haute impact` compares existing staging and production endpoints using a configured portfolio sample |
| **Approval gate** | Configure this in GitHub, GitLab, Azure DevOps, or your own release process; `min_approvers` is configuration metadata, not an enforced gate |
| **Rollback** | Use your target platform's model/image revision and rollback procedure |

The CI files generated by `haute init` sequence the available commands. Whether they run, whether production requires approval, and how a failed stage is handled are CI-provider and repository-policy decisions. See the CI setup guides ([GitHub Actions](ci/github-actions.md), [GitLab](ci/gitlab.md), [Azure DevOps](ci/azure-devops.md)) for the generated-job behaviour and required platform configuration.

---

## Which page should I read?

If you're a pricing analyst doing this for the first time, **start with Databricks** - it's the simplest target and doesn't require Docker or cloud infrastructure knowledge. The Docker, AWS, and Azure pages are designed for teams with IT support or technical colleagues who can help with the infrastructure setup.

| I want to... | Read this |
|---|---|
| Deploy my pipeline with the least setup possible | [Databricks](targets/databricks.md) |
| Build a portable package my IT team can deploy anywhere | [Docker](targets/docker.md) |
| Deploy to our existing AWS infrastructure | [AWS ECS](targets/aws.md) (with IT support) |
| Deploy to our existing Azure infrastructure | [Azure Container Apps](targets/azure.md) (with IT support) |
| Set up automatic testing and deployment | See "Where is your code hosted?" below |
| Understand the terminal, Git, and other new concepts | [Before You Start](before-you-start.md) |

### Where is your code hosted?

To set up CI/CD (the automatic testing and deployment), you need to know where your team's repository lives. **Ask your IT team or tech lead if you're not sure.** Then pick the matching guide:

| If your project is on... | Set up CI/CD with... |
|---|---|
| **github.com** | [GitHub Actions](ci/github-actions.md) |
| **gitlab.com** (or a company GitLab server) | [GitLab CI/CD](ci/gitlab.md) |
| **dev.azure.com** | [Azure DevOps Pipelines](ci/azure-devops.md) |

---

## Next steps

1. **New to the command line and Git?** Start with [Before You Start](before-you-start.md)

2. **Pick your target** and follow the setup guide:
    - [Databricks](targets/databricks.md) - most common, recommended starting point
    - [Docker](targets/docker.md) - for local testing or IT-managed infrastructure
    - [AWS ECS](targets/aws.md) - for AWS-based teams (IT-assisted)
    - [Azure Container Apps](targets/azure.md) - for Azure-based teams (IT-assisted)

3. **Set up CI/CD** - pick the guide that matches where your code is hosted:
    - [GitHub Actions](ci/github-actions.md) - if your project is on **github.com**
    - [GitLab CI/CD](ci/gitlab.md) - if your project is on **gitlab.com**
    - [Azure DevOps](ci/azure-devops.md) - if your project is on **dev.azure.com**
