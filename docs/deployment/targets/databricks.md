# Databricks

This guide walks you through setting up a Haute pipeline to deploy to **Databricks Model Serving** - step by step, from scratch. The Databricks adapter is the end-to-end deployment path: it registers the model and creates or updates its serving endpoint. A CI run performs that action only when your team enables the generated workflow and supplies the required credentials.

!!! info "What is Databricks Model Serving?"
    Databricks is a cloud platform for data and AI. **Model Serving** is a feature that takes a model (in this case, your pricing pipeline) and hosts it as a live web address (called an [API](../before-you-start.md#what-is-an-api)) that accepts quote data and returns results. You don't need to manage any servers.

!!! tip "New to the command line?"
    This guide involves typing a few setup commands in a terminal. If you've never done that before, read [Before You Start](../before-you-start.md) first - it takes five minutes and will make everything below much clearer.

### What this guide covers

This guide has **9 steps**. You don't need to do them all in one sitting.

- **Steps 1-3** are **one-time credential setup** - get your workspace URL, create a token, and give them to CI. This takes 10 minutes.
- **Steps 4-6** are **Databricks infrastructure** - set up Unity Catalog, MLflow, and Model Serving. You or your data engineer do these once. If your workspace is already set up, you may be able to skip some of these.
- **Steps 7-9** are **your ongoing workflow** - review your config, deploy by merging to main, and call the API.

---

## How it works

The generated CI workflow is designed to run `haute deploy`; you may use it after reviewing and adapting it. A typical workflow is:

1. **Edit your pipeline** locally and preview with `haute serve`
2. **Push your changes** and open a pull request - CI validates automatically
3. **Merge to main** - the generated workflow runs a staging deploy, then smoke and impact commands if the deploy succeeds
4. **Review the result** and use a CI-provider approval/process to decide whether to run the separate production workflow

Set up the configuration (`haute.toml`), Databricks infrastructure (steps below), CI secrets, and your CI provider's branch/environment protections. Haute does not enforce approval counts or create those protections.

---

## Prerequisites

Before you start, you need:

- A **Databricks workspace** (your organisation probably already has one)
- **Python 3.11+** installed on your machine
- **Haute** installed with Databricks extras (`uv add "haute[databricks]"` - see [Installing Haute](../../getting-started/installing-haute.md#installing-extras))

If you haven't initialised your project yet, open your VS Code terminal and run:

```powershell
haute init --target databricks --ci github
```

!!! tip "Your team may have already done this"
    If you cloned an existing project that already has a `haute.toml` file, skip this step - it's already initialised. This command is only needed once per project, and whoever set up the repository probably ran it already.

---

## Step 1: Get your Databricks workspace URL

Your workspace URL is the web address you use to log into Databricks. It looks like one of these:

| Cloud | URL format |
|---|---|
| **Azure** (most common for UK insurance) | `https://adb-1234567890123456.12.azuredatabricks.net` |
| **AWS** | `https://dbc-abc12345-1234.cloud.databricks.com` |
| **GCP** | `https://1234567890123456.gcp.databricks.com` |

If you don't know your workspace URL, open Databricks in your browser and copy the URL from the address bar (just the part before any `/` path).

!!! tip "Don't have a Databricks workspace?"
    Ask your IT team - most insurance companies with a data platform already have one. If you need to create one, the cheapest option is Azure Databricks on the **Premium** tier (required for Model Serving).

---

## Step 2: Create a Personal Access Token

A **Personal Access Token** (PAT) is like a password that Haute uses to talk to Databricks on your behalf. To create one:

1. Open your Databricks workspace in a browser
2. Click your **user icon** in the top-right corner → **Settings**
3. Go to **Developer** → **Access tokens**
4. Click **Manage** → **Generate new token**
5. Fill in:
    - **Comment:** `haute-deploy` (so you remember what it's for)
    - **Lifetime:** 90 days (or whatever your organisation allows)
6. Click **Generate**
7. **Copy the token immediately** - you won't be able to see it again

The token looks like: `dapi_your_token_here`

---

## Step 3: Add credentials to CI

Since deployment runs in CI (not on your laptop), your credentials need to be stored as **encrypted secrets** in your CI provider. This is a one-time setup.

The two values you need are:

| Secret name | Value |
|---|---|
| `DATABRICKS_RATING_HOST` | Your workspace URL from Step 1 |
| `DATABRICKS_RATING_TOKEN` | Your personal access token from Step 2 |

!!! note "Why the `RATING` in the name?"
    The deploy pipeline uses these `DATABRICKS_RATING_*` names for the production serving credentials, keeping them separate from the general `DATABRICKS_HOST`/`DATABRICKS_TOKEN` pair the editor uses for data access and MLflow. The values can be the same workspace URL and token - only the names differ, and the deploy will fail with a clear error if the `RATING` names are missing.

How to add them depends on your CI provider:

- **GitHub Actions** - see [Adding GitHub Secrets](../ci/github-actions.md#step-1-add-your-credentials-as-github-secrets)
- **GitLab CI/CD** - see [Adding CI/CD Variables](../ci/gitlab.md#step-1-add-your-credentials-as-cicd-variables)
- **Azure DevOps** - see [Creating a Variable Group](../ci/azure-devops.md#step-1-create-a-variable-group-for-credentials)

!!! tip "Don't know which CI provider you're using?"
    It depends on where your code is hosted. If you access your project on **github.com**, you're using GitHub. If it's **gitlab.com** (or a company GitLab server), you're using GitLab. If it's **dev.azure.com**, you're using Azure DevOps. If you're not sure, ask your IT team or tech lead: *"Where is our code repository hosted?"* They'll tell you GitHub, GitLab, or Azure DevOps.

!!! note "You don't need credentials on your laptop"
    You never run `haute deploy` locally, so you don't need a `.env` file with passwords on your machine. The CI runner has the credentials; you just merge to main. The `.env.example` file in your project is a reference for whoever sets up the CI secrets.

---

## Step 4: Set up Unity Catalog

**Unity Catalog** is where Databricks registers your deployed model. Think of it as a filing system - you need a **catalog** (like a cabinet) and a **schema** (like a folder inside it).

### Check if Unity Catalog is enabled

1. In your Databricks workspace, click **Catalog** in the left sidebar
2. If you see a catalog browser with tables and schemas, it's enabled
3. If you don't see it, ask your workspace admin to enable it

### Create a schema for your models

Most workspaces already have a `main` catalog. You just need a schema inside it.

To run the SQL command below, open your Databricks workspace in a browser and do one of the following:

- **Option A: SQL Editor** - click **SQL Editor** in the left sidebar, paste the command, and click **Run**
- **Option B: Notebook** - click **New** → **Notebook**, change the language to **SQL** (dropdown at the top), paste the command, and click **Run**

```sql
CREATE SCHEMA IF NOT EXISTS main.pricing;
```

You can name the schema anything you like - `pricing` is a sensible default.

### Update `haute.toml`

Make sure your `haute.toml` matches:

```toml
[deploy.databricks]
catalog = "main"
schema = "pricing"
```

---

## Step 5: Set up an MLflow experiment

!!! info "What is MLflow?"
    MLflow is a logging system built into Databricks that keeps an audit trail of every version you deploy - think of it as a version history for your pricing models. Every time you deploy, Haute creates an **MLflow run** recording what was deployed, when, and by whom. These runs are grouped inside an **experiment** (which is just a named folder for runs).

Every time you deploy, Haute logs the deployment as an MLflow run inside an experiment. This gives you a full history of every version you've ever deployed.

### Option A: Let Haute create it automatically

If the experiment doesn't exist, Haute will create it on first deploy. Just set the path in `haute.toml`:

```toml
[deploy.databricks]
experiment_name = "/Shared/haute/motor-pricing"
```

### Option B: Create it manually

1. In Databricks, click **Experiments** in the left sidebar (under Machine Learning)
2. Click **Create Experiment**
3. Set the name to `/Shared/haute/motor-pricing` (or whatever matches your pipeline)
4. Click **Create**

!!! tip "Naming convention"
    We recommend `/Shared/haute/<your-model-name>` so all team members can access it:
    ```
    /Shared/haute/motor-pricing
    /Shared/haute/home-pricing
    /Shared/haute/commercial-property
    ```

---

## Step 6: Check that Model Serving is available

Databricks Model Serving is the feature that actually hosts your pipeline as an API. To check it's available:

1. Click **Serving** in the left sidebar
2. If you see the Serving page, you're good
3. If not, ask your workspace admin - Model Serving requires a **Premium** tier workspace

!!! note "Cost"
    Model Serving is billed per compute-second. With `serving_scale_to_zero = true` in your config, you only pay when the endpoint is actually receiving requests. For a `Small` workload with occasional traffic, expect **less than £5/month** for dev/test.

---

## Step 7: Review your full configuration

Here's what your complete `haute.toml` should look like:

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
```

### What each setting means

| Setting | What it does | Example |
|---|---|---|
| `target` | Tells Haute to deploy to Databricks | `"databricks"` |
| `model_name` | The name of your model in the Databricks Model Registry | `"motor-pricing"` |
| `endpoint_name` | The name of the serving endpoint (becomes part of the API URL) | `"motor-pricing"` |
| `experiment_name` | Where MLflow logs each deployment | `"/Shared/haute/motor-pricing"` |
| `catalog` | Unity Catalog name | `"main"` |
| `schema` | Unity Catalog schema | `"pricing"` |
| `serving_workload_size` | How much compute to allocate - `Small`, `Medium`, or `Large` | `"Small"` |
| `serving_scale_to_zero` | Whether the endpoint shuts down when idle (saves money) | `true` |

---

## Step 8: Deploy by merging to main

Once your configuration is set up and CI secrets are in place, you can deploy through your CI process. The starter workflow is a sequence of commands, not an enforced governance policy:

1. **Push your changes** to a branch and open a [pull request](../before-you-start.md#4-ask-for-a-review-pull-request)
2. **CI validates automatically** - lints your code, runs tests, and does a dry-run deploy to check your pipeline parses and test quotes pass
3. **Merge the PR** - the generated staging workflow requests deployment to `motor-pricing-staging`, then invokes smoke and impact commands if that command succeeds
4. **Review the impact report** - download it from CI if the impact job ran, and check the premium changes make sense
5. **Promote deliberately** - use the separate production workflow and any CI-provider approval rule your team configured

The first deploy creates the serving endpoint, but `haute deploy` returns after Databricks accepts the create or update request. Provisioning can take **5–10 minutes**. The generated workflow does not wait for readiness before it invokes `haute smoke`, so add a Databricks readiness wait/retry to that workflow (or rerun smoke and impact after the endpoint is **Ready**) before treating the sequence as a release gate.

!!! success "What does success look like?"
    After Databricks reports the endpoint **Ready** and the corresponding smoke and impact commands have run, you should see:

    1. **In your CI provider** - green validation and deploy jobs; smoke and impact can be green only after endpoint readiness and valid endpoint configuration
    2. **In Databricks** - click **Serving** in the left sidebar and you'll see your endpoint (e.g. `motor-pricing`) with status **Ready** and a green indicator
    3. **In MLflow** - click **Experiments** in the left sidebar, navigate to your experiment (e.g. `/Shared/haute/motor-pricing`), and you'll see a new run logged with the deployment details

    If you see all three, your pipeline is live and serving premiums.

If CI reports errors during validation, the most common causes are:

- **Missing model files** - check that the files referenced in your pipeline (e.g. `models/freq.cbm`) exist
- **Test quote schema mismatch** - your test quote JSON fields don't match what your pipeline expects
- **Pipeline syntax error** - there's a bug in your Python file

See your CI provider's setup guide for full details: [GitHub Actions](../ci/github-actions.md), [GitLab](../ci/gitlab.md), or [Azure DevOps](../ci/azure-devops.md).

---

## Step 9: Call the API

Once the endpoint is live, you can call it from any system that can make HTTP requests.

### Using Python (recommended)

If you're more comfortable with Python than the command line, this is the easiest way to test:


```python
import requests

# Replace these with your actual values
url = "https://adb-xxx.12.azuredatabricks.net/serving-endpoints/motor-pricing/invocations"
token = "dapi_your_token_here"  # Your Databricks personal access token from Step 2

headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json",
}
payload = {
    "dataframe_records": [
        {"IDpol": 99001, "VehPower": 7, "DrivAge": 42, "Area": "C", "VehBrand": "B12"}
    ]
}

response = requests.post(url, json=payload, headers=headers)
print(response.json())
```

### Using the Databricks SDK

```python
from databricks.sdk import WorkspaceClient

w = WorkspaceClient()
response = w.serving_endpoints.query(
    name="motor-pricing",
    dataframe_records=[
        {"IDpol": 99001, "VehPower": 7, "DrivAge": 42, "Area": "C", "VehBrand": "B12"}
    ],
)
print(response.predictions)
```

### Using curl (advanced)

[`curl`](../before-you-start.md#what-is-curl) is a command-line tool for sending web requests. You don't need to use it - the Python examples above are easier and work the same on Windows - but it's included for reference:

```powershell
curl -X POST `
  "https://<your-workspace-url>/serving-endpoints/motor-pricing/invocations" `
  -H "Authorization: Bearer <your-token-here>" `
  -H "Content-Type: application/json" `
  -d '{\"dataframe_records\": [{\"IDpol\": 99001, \"VehPower\": 7, \"DrivAge\": 42, \"Area\": \"C\", \"VehBrand\": \"B12\"}]}'
```

!!! tip "Prefer the Python example above"
    The Python examples are simpler and avoid Windows command-line quoting issues. Use those unless you have a specific reason to use curl.

---

## Troubleshooting

### "PERMISSION_DENIED" on deploy

Your token needs these permissions:

- **Can Manage** on the MLflow experiment
- **USE CATALOG** and **USE SCHEMA** on Unity Catalog
- **Can Manage** on serving endpoints (or ask an admin to create the endpoint first)

Ask your Databricks admin to grant these if you see permission errors. This is usually someone in your IT or data engineering team - the person who set up the Databricks workspace.

### "Endpoint not found" when calling the API

After deploying, the serving endpoint can take 5–10 minutes to provision. Check its status:

```powershell
haute status
```

Or in the Databricks UI: click **Serving** in the left sidebar and look for your endpoint.

### Token expired

Tokens have a lifetime (default 90 days). If your deploy suddenly fails with an authentication error, generate a new token (Step 2) and update the `DATABRICKS_RATING_TOKEN` secret in your CI provider.

### Slow first request (cold start)

With `serving_scale_to_zero = true`, the endpoint shuts down when it's idle. The first request after idle can take 30–60 seconds. For production endpoints that need consistent response times, set `serving_scale_to_zero = false` in `haute.toml`.

### Missing dependency error on the endpoint

The deployed model needs the `haute` package. If you see `No module named 'haute'` in the endpoint logs, make sure `haute` is published and accessible from your Databricks workspace.

---

## Checklist

Before your first deploy, confirm:

- [ ] You have your Databricks workspace URL
- [ ] You have a Personal Access Token
- [ ] Both are added as CI secrets (`DATABRICKS_RATING_HOST`, `DATABRICKS_RATING_TOKEN`)
- [ ] Unity Catalog is enabled with a catalog and schema
- [ ] `haute.toml` has the correct `experiment_name`, `catalog`, and `schema`
- [ ] Model Serving is available in your workspace
- [ ] You have at least one test quote JSON file in `tests/quotes/`
- [ ] CI/CD workflows are committed to your repository
- [ ] CI validation passes on a pull request
