# Deployment

Deployment is how your pricing pipeline goes from a Python file on your laptop to a **live API** that other systems can call. When your policy admin system needs a premium for a new quote, it sends a request to this API, and the API runs your pipeline and returns the answer.

!!! warning "New to Haute? Start here."
    If you haven't installed Haute yet, start with **[Getting Started](../getting-started/index.md)** - it covers installing everything and running your first `haute serve`. If you don't know what a pull request, CI/CD, or staging means, read **[Before You Start](before-you-start.md)** next - it explains every deployment concept in plain English.

!!! tip "Haven't built your pipeline yet?"
    These docs assume you already have a working pricing pipeline (`rating/main.py`). If you haven't created one yet, start with the **Building Pipelines** guide first, then come back here when you're ready to deploy.

Haute handles the entire deployment process for you. You **merge your changes to main** (apply them to the main version of the project - see [Before You Start](before-you-start.md#5-accept-the-changes-merge)) and Haute's [CI/CD pipeline](before-you-start.md#what-is-cicd) (an automated process that tests and deploys your code) does the rest - packaging, uploading, testing, and promoting to production. No Docker knowledge, no cloud consoles, no DevOps tickets. You never need to run a deploy command yourself.

---

## Your workflow

As a pricing analyst, your day-to-day workflow is:

1. **Edit your pipeline** - change your Python file, update a model, adjust a transform
2. **Preview it** - run `haute serve` to open the visual editor and check everything looks right
3. **Push and open a pull request** - CI (an automated checker - see [Before You Start](before-you-start.md#what-is-cicd)) automatically validates your pipeline
4. **Merge to main** - CI automatically deploys to staging, runs smoke tests, and generates an impact report
5. **Review the impact report** - check the premium changes make sense
6. **Approve** - CI deploys to production

You never need to run `haute deploy`, install Docker, or manage cloud credentials on your machine. The CI runner handles all of that.

### What happens behind the scenes?

When CI runs the deploy, Haute automatically:

1. **Parses your pipeline** - reads your Python file and builds a graph of all the steps
2. **Prunes to the scoring path** - removes training steps, data exports, and anything not needed for live scoring
3. **Collects artifacts** - finds all the model files (e.g. `.cbm`, `.pkl`) your pipeline references and bundles them
4. **Validates** - runs your test quotes through the pruned pipeline to make sure it works
5. **Packages and uploads** - wraps everything into the format your target expects and pushes it
6. **Creates the endpoint** - sets up (or updates) the live API so it's ready to receive requests

---

## Choosing a target

A **target** is where your pipeline will run in production. Haute supports several:

| Target | Best for | What you need |
|---|---|---|
| [**Databricks**](targets/databricks.md) | Teams already using Databricks | A Databricks workspace - the simplest option, no containers involved |
| [**Docker**](targets/docker.md) | Companies without Databricks | IT takes the package and deploys it on their infrastructure |
| [**AWS ECS**](targets/aws.md) | Teams on AWS (with IT support) | An AWS account - IT sets up the infrastructure, you just merge |
| [**Azure Container Apps**](targets/azure.md) | Teams on Azure (with IT support) | An Azure subscription - IT sets up the infrastructure, you just merge |

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

## Safety gates

In insurance, a pricing mistake can misprice millions of pounds of premium before anyone notices. Haute builds safety into every deployment:

| Safety check | What it does |
|---|---|
| **Dry-run validation** | Parses the pipeline, checks all model files exist, scores test quotes |
| **Staging deployment** | Deploys to a separate staging endpoint first, never straight to production |
| **Smoke testing** | Scores test quotes against the live staging endpoint |
| **Impact analysis** | Compares new premiums vs current production premiums across a portfolio sample |
| **Approval gate** | Requires a team member to review the impact report and approve before production |
| **Rollback** | If something goes wrong, revert to the previous version in minutes |

These are all configured in `haute.toml` and enforced automatically by the [CI/CD pipeline](before-you-start.md#what-is-cicd) - an automated process that runs every time you propose a change. See the CI/CD setup guides ([GitHub Actions](ci/github-actions.md), [GitLab](ci/gitlab.md), [Azure DevOps](ci/azure-devops.md)) for details.

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
