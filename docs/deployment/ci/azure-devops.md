# Azure DevOps Pipelines

This guide explains how to set up **automatic deployments** using Azure DevOps Pipelines - so that every time you merge a change to your main branch, your pricing pipeline is tested, deployed to staging, validated, and (with your approval) promoted to production.

!!! tip "New to Git, pull requests, or CI/CD?"
    This page assumes you understand basic Git concepts like branches, commits, and pull requests. If these are unfamiliar, read [Before You Start](../before-you-start.md) first - it explains everything in plain English.

!!! tip "Not sure if this is the right page?"
    If you access your project on **dev.azure.com**, this is the right page. If it's on **github.com**, go to [GitHub Actions](github-actions.md). If it's on **gitlab.com** (or a company GitLab server), go to [GitLab CI/CD](gitlab.md). If you're not sure, ask your IT team or tech lead: *"Where is our code repository hosted?"* They'll tell you GitHub, GitLab, or Azure DevOps.

!!! info "What is Azure DevOps?"
    Azure DevOps is Microsoft's platform for managing code, builds, and releases. **Pipelines** is the [CI/CD](../before-you-start.md#what-is-cicd) feature - it runs scripts automatically when you push code or open a pull request. If your organisation uses Azure DevOps for source control (instead of GitHub or GitLab), this is your CI/CD provider.

!!! note "Who does what on this page?"
    This page has **two parts**. As a pricing analyst, you only need to do **Step 4** (test it). **Steps 1-3** (creating the variable group, environment, and pipeline) are usually done by your IT team or tech lead - they have the credentials and know the Azure DevOps UI. If you're setting this up yourself, the steps walk you through it, but it's perfectly fine to hand it to IT and say: *"Can you set up CI/CD for our Haute project? Here's the `.env.example` file listing the credentials needed."*

---

## What Haute generates

When you run `haute init --ci azure-devops`, Haute creates an `azure-pipelines.yml` file in the root of your repository. This file defines a pipeline with five **stages** that run in order:

| Stage | When it runs | What it does |
|---|---|---|
| `Validate` | On PRs and pushes to main | Lints, type-checks, tests, validates pipeline, dry-run deploy |
| `DeployStaging` | On push to main only | Deploys to a staging endpoint |
| `SmokeTest` | After staging deploy | Scores test quotes against the live staging endpoint |
| `ImpactAnalysis` | After smoke test | Compares new staging premiums vs current production |
| `DeployProduction` | **Manual approval** | Deploys to production after a reviewer approves |

This gives you the same safe release pipeline as GitHub Actions and GitLab:

```
Pull request → Validation → Merge → Staging → Smoke test → Impact analysis → [Approval] → Production
```

---

## How it works, step by step

### 1. You make a change and open a pull request

You edit your pipeline, update a model, or change a configuration. You push your changes to a [branch](../before-you-start.md#1-create-a-branch) and open a [pull request](../before-you-start.md#4-ask-for-a-review-pull-request) (PR) in Azure DevOps - which is a way of asking your teammate to review your changes before they go live.

### 2. Validation runs automatically

The `Validate` stage runs immediately. It has four parallel jobs:

- **Lint & Format** - checks your code style with `ruff`
- **Type Check** - runs `mypy` to catch type errors
- **Test** - runs `pytest`
- **Pipeline Validation** - runs `haute lint` and `haute deploy --dry-run`

If any of these fail, the PR gets a red ❌ and you can see what went wrong in the pipeline logs.

### 3. You merge to main

Once validation passes and your teammate has reviewed the PR, you [merge](../before-you-start.md#5-accept-the-changes-merge) it - which applies your changes to the main version of the project. This triggers the remaining stages.

### 4. Staging deployment (automatic)

The `DeployStaging` stage runs `haute deploy --endpoint-suffix "-staging"`, which creates a separate endpoint just for testing (e.g. `motor-pricing-staging`).

### 5. Smoke test (automatic)

The `SmokeTest` stage scores your test quotes against the live staging endpoint.

### 6. Impact analysis (automatic)

The `ImpactAnalysis` stage compares premiums from the new staging endpoint against the current production endpoint. The impact report is uploaded as a **pipeline artifact** you can download.

### 7. You review and approve

The `DeployProduction` stage targets an **Environment** called `production`. Azure DevOps environments support **approval checks** - so a designated reviewer must approve before the production deploy runs.

The impact report shows exactly what changed:

```
Output: technical_price
  Staging mean:     548.57     Production mean:   536.12
  Mean change:       +2.3%    Rows changed:      84.2%
  Max increase:     +18.7%    Max decrease:       -4.2%

  ⚠ 147 quotes changed by more than ±25.0%
  ✓ Average change (+2.3%) within ±10.0% threshold
```

### 8. Production deploy

Once approved, the pipeline deploys to the real endpoint and tags the release in Git.

---

## Setup

### Step 1: Create a Variable Group for credentials

!!! tip "This step is usually done by IT"
    If you're not comfortable navigating Azure DevOps, ask your IT team or tech lead to do Steps 1-3. Give them the `.env.example` file from your project - it lists exactly which credentials are needed.

Azure DevOps stores shared secrets in **Variable Groups**. Haute's generated pipeline expects a group called `haute-credentials`.

1. Go to your project page in Azure DevOps (e.g. `dev.azure.com/yourcompany/motor-pricing`)
2. In the left sidebar, click **Pipelines** → **Library** (the 📚 icon)
3. Click **+ Variable group**
4. Name it `haute-credentials`
5. Add each credential as a variable:

**For Databricks:**

| Name | Value | Secret? |
|---|---|---|
| `DATABRICKS_RATING_HOST` | Your workspace URL | ✓ Lock |
| `DATABRICKS_RATING_TOKEN` | Your personal access token | ✓ Lock |

**For Docker/Container targets:**

| Name | Value | Secret? |
|---|---|---|
| `DOCKER_USERNAME` | Your registry username | ✓ Lock |
| `DOCKER_PASSWORD` | Your registry password | ✓ Lock |

**For AWS ECS:**

| Name | Value | Secret? |
|---|---|---|
| `DOCKER_USERNAME` | `AWS` | ✓ Lock |
| `DOCKER_PASSWORD` | ECR auth token | ✓ Lock |
| `AWS_ACCESS_KEY_ID` | Your AWS access key | ✓ Lock |
| `AWS_SECRET_ACCESS_KEY` | Your AWS secret key | ✓ Lock |
| `AWS_DEFAULT_REGION` | e.g. `eu-west-1` | - |

**For Azure Container Apps:**

| Name | Value | Secret? |
|---|---|---|
| `DOCKER_USERNAME` | Your ACR name | ✓ Lock |
| `DOCKER_PASSWORD` | Your ACR password | ✓ Lock |
| `AZURE_SUBSCRIPTION_ID` | Your subscription ID | ✓ Lock |
| `AZURE_TENANT_ID` | Your tenant ID | ✓ Lock |
| `AZURE_CLIENT_ID` | Your service principal app ID | ✓ Lock |
| `AZURE_CLIENT_SECRET` | Your service principal secret | ✓ Lock |

Click **Save** when done.

!!! tip "Lock secrets"
    Click the 🔒 lock icon next to each secret value. This encrypts it and prevents it from appearing in pipeline logs.

### Step 2: Create the `production` Environment

The production deploy stage uses an Azure DevOps **Environment** with approval checks. This is what prevents accidental production deployments.

1. Go to **Pipelines** → **Environments**
2. Click **New environment**
3. Name it `production`
4. Resource: **None**
5. Click **Create**

Now add an approval check:

1. Click the `production` environment you just created
2. Click the **⋮** menu (top right) → **Approvals and checks**
3. Click **+ Add check** → **Approvals**
4. Add the people who should approve production deploys
5. Set **Minimum number of approvers** (e.g. 1)
6. Click **Create**

When the pipeline reaches the production stage, it will pause and send a notification to the approvers. They review the impact report, then approve or reject.

### Step 3: Create the Pipeline

1. Go to **Pipelines** → **Pipelines**
2. Click **New pipeline**
3. Select your repository source (Azure Repos Git, GitHub, etc.)
4. Select your repository
5. Choose **Existing Azure Pipelines YAML file**
6. Select `/azure-pipelines.yml` from the branch dropdown
7. Click **Run** (or **Save** if you want to run it later)

### Step 4: Test it

1. Create a branch, make a small change, and open a pull request
2. The validation stage should run automatically on the PR
3. If it passes, merge the PR
4. Watch the full pipeline: staging → smoke test → impact analysis
5. When it pauses at production, check the impact report artifact, then approve

---

## The generated pipeline in detail

!!! note "You don't need to read or understand the YAML below"
    The pipeline file was generated by `haute init` and works out of the box. The sections below are **reference only** - for troubleshooting or if your IT team wants to understand what’s happening.

??? info "`azure-pipelines.yml` structure"

    ```yaml
    trigger:
      branches:
        include: [main]
      paths:
        include:
          - "*.py"
          - haute.toml
          - data/
          - models/
          - tests/quotes/

    pr:
      branches:
        include: [main]

    stages:
      - stage: Validate        # 4 parallel jobs
      - stage: DeployStaging    # depends on Validate, main branch only
      - stage: SmokeTest        # depends on DeployStaging
      - stage: ImpactAnalysis   # depends on SmokeTest
      - stage: DeployProduction # depends on ImpactAnalysis, uses 'production' environment
    ```

??? info "Key features"

    - **Path filters** - the pipeline only triggers when relevant files change (Python files, config, data, models, test quotes), not on README changes
    - **PR validation** - the `Validate` stage runs on pull requests too, giving you feedback before merge
    - **Variable group** - credentials come from the `haute-credentials` variable group, not hardcoded
    - **Environment approvals** - the `production` environment enforces manual approval before production deploy
    - **Deployment job** - the production stage uses a `deployment` job type, which gives you deployment history and tracking in Azure DevOps
    - **Artifacts** - the impact report is published as a pipeline artifact you can download from the build summary
    - **Git tagging** - after production deploy, the pipeline tags the release as `deploy/v<version>`

??? info "Stage details"

    | Stage | Jobs | Timeout |
    |---|---|---|
    | Validate | Lint, Type Check, Test, Pipeline Validation (parallel) | 10 min each |
    | DeployStaging | Deploy to staging | 15 min |
    | SmokeTest | Score test quotes against staging | 10 min |
    | ImpactAnalysis | Compare staging vs production | 10 min |
    | DeployProduction | Deploy to production + tag | 15 min |

---

## Azure DevOps vs GitHub/GitLab - key differences

| Concept | GitHub | GitLab | Azure DevOps |
|---|---|---|---|
| Config file | `.github/workflows/*.yml` | `.gitlab-ci.yml` | `azure-pipelines.yml` |
| Credentials | Repository Secrets | CI/CD Variables | Variable Groups |
| Manual approval | Separate workflow + button | `when: manual` + ▶ button | Environment approval checks |
| Artifacts | Upload/download actions | `artifacts:` keyword | `publish:` keyword |
| PR checks | Workflow on `pull_request` | Job with `merge_request_event` rule | `pr:` trigger |

The biggest difference is how manual approval works:

- **GitHub:** you manually run a separate workflow
- **GitLab:** you click a play button on a manual job
- **Azure DevOps:** the pipeline pauses automatically and sends a notification to approvers - they approve directly from their email or the Azure DevOps UI

This makes Azure DevOps the most "enterprise-friendly" option for approval workflows.

---

## Working solo?

If you're the only person on the team:

1. Set `min_approvers = 0` in `haute.toml` under `[safety.approval]`
2. In the `production` environment, either remove the approval check or set yourself as the sole approver
3. You still get the full pipeline - validation → staging → smoke test → impact analysis
4. Approve your own production deploys after reviewing the impact report

---

## Troubleshooting

### Pipeline doesn't trigger

- **On PR:** Make sure the PR targets the `main` branch
- **On merge:** Check the trigger paths - if you only changed a markdown file, the pipeline won't trigger (by design)
- **Check Pipelines:** Go to **Pipelines** → **Pipelines** and look for recent runs

### "Variable not found" or "Authentication failed"

Your variable group is missing or misconfigured:

1. Go to **Pipelines** → **Library**
2. Check the variable group is named exactly `haute-credentials`
3. Check variable names are correct (case-sensitive)
4. Make sure secrets are saved (not just typed - click **Save**)

### Pipeline authorisation error on variable group

The first time a pipeline uses a variable group, Azure DevOps may ask you to authorise it:

1. Go to the failed pipeline run
2. Click **View** on the authorisation prompt
3. Click **Permit**

This only happens once per variable group per pipeline.

### "Environment not found"

Make sure you created the `production` environment in **Pipelines** → **Environments**. The name must match exactly.

### Impact report not appearing

The impact report is published as a pipeline artifact. To find it:

1. Go to the pipeline run → click the **Summary** tab
2. Under **Published artifacts**, look for `impact-report`
3. Click to download `impact_report.md`

### Approval notification not received

Check that:

1. The `production` environment has an approval check configured
2. Your email notifications are enabled in Azure DevOps
3. You're listed as an approver on the environment
