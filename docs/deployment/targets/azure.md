# Azure Container Apps

This guide covers deploying a Haute pipeline to **Azure Container Apps** - Microsoft's managed container platform. When you merge to main, CI builds a Docker image from your pipeline, pushes it to a registry, and (once the SDK integration lands) creates a new revision of your app.

!!! info "What is Azure Container Apps?"
    Azure Container Apps is Microsoft's serverless container service. You give it a Docker image and it runs it for you - handling scaling, load balancing, and HTTPS certificates automatically. It can even scale to zero when there's no traffic, so you only pay when the API is being used.

!!! warning "Platform service update is not yet implemented"
    Haute currently builds and pushes the Docker image for Azure Container Apps, but the automatic service update step (creating a new revision) is still in development. After CI pushes the image, your IT team will need to update the app manually until the SDK integration lands.

!!! note "This target requires IT support"
    Azure Container Apps involves cloud infrastructure setup (registries, environments, service principals) that is done by an IT or platform team. The "Infrastructure setup" section below is written **for your IT team**. As an analyst, your role is to configure `haute.toml` and merge to main - CI and IT handle the rest.

    If your organisation uses Databricks, the [Databricks target](databricks.md) is simpler and doesn't involve containers.

---

## Prerequisites

- **Python 3.11+** and **Haute** installed on your machine
- An **Azure subscription** (your IT team manages this)
- A Haute project initialised with the Azure Container Apps target - open your VS Code terminal and run:

```powershell
haute init --target azure-container-apps --ci github
```

!!! tip "Your team may have already done this"
    If you cloned an existing project that already has a `haute.toml` file, skip this step - it's already initialised.

!!! note "Before you begin"
    Your IT team needs to set up the Azure infrastructure first (container registry, container app, service principal). If that hasn't been done yet, send them the [Infrastructure setup for IT](#infrastructure-setup-one-time-done-by-it) section at the bottom of this page. Once they've done it, they'll give you the values you need for the steps below.

---

## Step 1: Configure `haute.toml`

```toml
[project]
name = "motor-pricing"
pipeline = "rating/main.py"

[deploy]
target = "azure-container-apps"
model_name = "motor-pricing"

[deploy.container]
registry = "pricingregistry.azurecr.io"
port = 8080
base_image = "python:3.11-slim"

[deploy.azure-container-apps]
resource_group = "rg-pricing"
container_app_name = "motor-pricing"
environment_name = "pricing-env"

[test_quotes]
dir = "tests/quotes"
```

### What each setting means

| Setting | What it does | Example |
|---|---|---|
| `target` | Tells Haute to deploy to Azure Container Apps | `"azure-container-apps"` |
| `registry` | Your ACR login server | `"pricingregistry.azurecr.io"` |
| `port` | The port your API listens on | `8080` |
| `resource_group` | Azure resource group containing your container app | `"rg-pricing"` |
| `container_app_name` | Name of the container app | `"motor-pricing"` |
| `environment_name` | Name of the Container Apps environment | `"pricing-env"` |

---

## Step 2: Add credentials to CI

CI needs Azure credentials to push images to ACR and update the container app. Add these as encrypted secrets in your CI provider (your IT team will have the values from the infrastructure setup):

| Secret name | Value |
|---|---|
| `DOCKER_USERNAME` | Your ACR name (e.g. `pricingregistry`) |
| `DOCKER_PASSWORD` | Your ACR admin password or service principal secret |
| `AZURE_SUBSCRIPTION_ID` | Your Azure subscription ID |
| `AZURE_TENANT_ID` | Your Azure tenant ID |
| `AZURE_CLIENT_ID` | Your service principal app ID |
| `AZURE_CLIENT_SECRET` | Your service principal password |

How to add them depends on your CI provider - see [GitHub Actions](../ci/github-actions.md#step-1-add-your-credentials-as-github-secrets), [GitLab](../ci/gitlab.md#step-1-add-your-credentials-as-cicd-variables), or [Azure DevOps](../ci/azure-devops.md#step-1-create-a-variable-group-for-credentials).

---

## Step 3: Deploy by merging to main

You don't run any deploy command. When you merge to main, CI automatically:

1. Validates your pipeline and scores test quotes
2. Generates a FastAPI app and Dockerfile
3. Builds the Docker image
4. Pushes the image to your ACR
5. *(Coming soon)* Creates a new revision of the container app with the new image

!!! success "What does success look like?"
    After a successful merge to main, you should see:

    1. **In your CI provider** - all pipeline steps show green ✓ (validation, build, push)
    2. **In the CI logs** - a message like `Pushed motor-pricing:a1b2c3d to pricingregistry.azurecr.io`
    3. **In the Azure Portal** - your Container App shows a new revision running with the latest image

    If CI is green and the container app is healthy, your pipeline is live.

Until the automatic service update lands, CI will output the image tag. Your IT team can then update the container app manually in the Azure Portal:

1. Go to your **Container App** → **Revisions**
2. Click **Create new revision**
3. Update the image to the tag CI printed (e.g. `pricingregistry.azurecr.io/motor-pricing:a1b2c3d`)
4. Click **Create**

Or using the Azure CLI:

```bash
az containerapp update \
  --name motor-pricing \
  --resource-group rg-pricing \
  --image pricingregistry.azurecr.io/motor-pricing:a1b2c3d
```

---

## Step 4: Test the API

Find your app URL in the Azure Portal: **Container App** → **Overview** → **Application Url**.

It will look like: `https://motor-pricing.nicemeadow-abc12345.uksouth.azurecontainerapps.io`

```python
import requests

response = requests.post(
    "https://motor-pricing.nicemeadow-abc12345.uksouth.azurecontainerapps.io/quote",
    json=[{"IDpol": 99001, "VehPower": 7, "DrivAge": 42, "Area": "C", "VehBrand": "B12"}],
)
print(response.json())
```

---

## Troubleshooting

### "Access denied" when pushing to ACR

Check that your Docker credentials are correct. If using admin credentials, make sure admin access is enabled on the ACR. If using a service principal, check it has the `AcrPush` role on the registry.

### Container app not starting

Check the logs in the Azure Portal: **Container App** → **Log stream**. Common causes are missing dependencies or model files inside the image.

### Health check failing

Azure Container Apps probes your health endpoint automatically. Make sure:

- Ingress target port matches your `haute.toml` `port` setting (default 8080)
- The `/health` endpoint returns a 200 status code

### App URL not working

Check that ingress is enabled on the container app and that it's accepting external traffic (not just internal).

---

## Infrastructure setup (one-time, done by IT)

As an analyst, **you can skip this section entirely** - send this page to whoever manages your Azure subscription and they'll set things up for you.

??? note "1. Create an Azure Container Registry (click to expand)"

    ACR is where Docker images are stored. Create one in the Azure Portal:

    1. Go to the [Azure Portal](https://portal.azure.com)
    2. Search for **Container registries** → click **Create**
    3. Fill in:
        - **Registry name:** e.g. `pricingregistry` (must be globally unique)
        - **Resource group:** create one or use existing (e.g. `rg-pricing`)
        - **Location:** e.g. `UK South`
        - **SKU:** `Basic` is fine for dev/test
    4. Click **Review + Create** → **Create**

    Note the **Login server** - it looks like: `pricingregistry.azurecr.io`

??? note "2. Create a Container Apps Environment (click to expand)"

    The environment is the shared network space where container apps run:

    1. In the Azure Portal, search for **Container Apps Environments** → click **Create**
    2. Fill in:
        - **Environment name:** e.g. `pricing-env`
        - **Resource group:** same as above
        - **Location:** same as above
    3. Click **Review + Create** → **Create**

??? note "3. Create a Container App (click to expand)"

    1. Search for **Container Apps** → click **Create**
    2. Fill in:
        - **Container app name:** e.g. `motor-pricing`
        - **Resource group:** same as above
        - **Container Apps Environment:** select the one you just created
    3. Under **Container**:
        - **Image source:** Azure Container Registry
        - **Registry:** select your ACR
        - **Image:** you can use a placeholder for now - Haute will update it on deploy
    4. Under **Ingress**:
        - **Ingress:** Enabled
        - **Ingress traffic:** Accepting traffic from anywhere (or limited, depending on your security needs)
        - **Target port:** `8080` (match the `port` in `haute.toml`)
    5. Click **Review + Create** → **Create**

??? note "4. Create a Service Principal for deployments (click to expand)"

    A service principal is like a special account that CI uses to push images and update the app:

    ```bash
    az ad sp create-for-rbac \
      --name "haute-deploy" \
      --role contributor \
      --scopes /subscriptions/<your-subscription-id>/resourceGroups/rg-pricing
    ```

    This outputs:

    ```json
    {
      "appId": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
      "displayName": "haute-deploy",
      "password": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
      "tenant": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
    }
    ```

    Give these values to whoever is setting up CI secrets.
