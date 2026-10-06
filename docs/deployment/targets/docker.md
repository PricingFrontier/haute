# Docker

This guide covers packaging a Haute pipeline as a **Docker container** - a self-contained image that can run anywhere. The `container` target builds the image and pushes it when a registry is configured; it deliberately does **not** provision a host, create an endpoint, or start the container. Your IT team (or a separate platform workflow) runs it from there.

!!! info "What is Docker?"
    Docker is a tool that packages your application and everything it needs (Python, libraries, model files) into a single **container** - like a shipping container for software. Anyone with Docker installed can run it, regardless of what's on their machine. You don't need to understand Docker to use this target - Haute and CI handle everything.

!!! tip "You don't need Docker on your laptop"
    Docker runs on the **CI runner**, not your machine. You never need to install Docker, build images, or run containers yourself. You just merge to main and CI does the rest. Your IT team takes the built image and deploys it to their infrastructure.

    If your organisation uses Databricks, the [Databricks target](databricks.md) is simpler and doesn't involve containers at all.

!!! example "When should I choose this target?"
    Choose Docker if your company **doesn't use Databricks** and your IT team has asked you for a container image, or if they've said they'll handle the hosting and just need a package from you. This is also the right choice if your IT team uses Kubernetes, Docker Compose, or any other container platform you haven't heard of - you don't need to know what those are.

!!! note "This target involves your IT team"
    As an analyst, your role is to **configure `haute.toml`** and **merge to main**. CI builds and pushes the Docker image automatically. Your IT team handles everything else - the registry, the hosting, the infrastructure. The sections below are split: **Steps 1-5 are for you**, and the "For your IT team" section at the bottom is reference material for IT.

---

## How it works

1. **You edit your pipeline** locally and preview with `haute serve`
2. **You merge to main** - CI automatically builds a Docker image containing your pipeline and pushes it to a container registry when `registry` is configured
3. **Your IT team** (or an automated platform) runs the image as a service, exposing the API

The generated CI workflow can run the build and push once you add a registry login step ([Step 1](#step-1-add-registry-credentials-to-ci)); IT handles the infrastructure and must provide the endpoints before the smoke and impact steps can use them ([Step 3](#step-3-give-smoke-and-impact-their-endpoints)).

---

## Prerequisites

Before you start, you need:

- **Python 3.11+** installed on your machine
- **Haute** installed (`uv add haute` - see [Installing Haute](../../getting-started/installing-haute.md#installing-extras))
- A **container registry** - this is an online storage service for Docker images (like a shared drive for containers). Ask your IT team which registry your company uses - they'll give you the registry URL and credentials

If you haven't initialised your project yet, open your VS Code terminal and run:

```powershell
haute init --target container --ci github
```

!!! tip "Your team may have already done this"
    If you cloned an existing project that already has a `haute.toml` file, skip this step - it's already initialised.

---

## Step 1: Add registry credentials to CI

!!! tip "This step is usually done by IT"
    Your IT team will know the registry URL and credentials. Ask them to add these as CI secrets, or give them the `.env.example` file from your project.

CI needs credentials to push the Docker image to your registry. Add these as encrypted secrets in your CI provider:

| Secret name | Value |
|---|---|
| `DOCKER_USERNAME` | Your registry username |
| `DOCKER_PASSWORD` | Your registry password or access token |

How to add them depends on your CI provider - see [GitHub Actions](../ci/github-actions.md#step-1-add-your-credentials-as-github-secrets), [GitLab](../ci/gitlab.md#step-1-add-your-credentials-as-cicd-variables), or [Azure DevOps](../ci/azure-devops.md#step-1-create-a-variable-group-for-credentials).

### Log in to the registry before the deploy

`haute deploy` pushes with a plain `docker push`, and the generated workflows pass these secrets to the deploy steps but do not log in with them. Until they do, add a login line before `haute deploy` in both the staging and the production deploy step of your workflow file, for example on GitHub Actions:

```yaml
        run: |
          echo "$DOCKER_PASSWORD" | docker login ghcr.io --username "$DOCKER_USERNAME" --password-stdin
          uv run haute deploy --endpoint-suffix "-staging"
```

Use your registry's host in place of `ghcr.io` (`docker.io` for Docker Hub, `myregistry.azurecr.io` for Azure Container Registry). Amazon ECR issues short-lived passwords instead; see [AWS ECS](aws.md#step-2-add-credentials-to-ci).

---

## Step 2: Configure `haute.toml`

Here's what the container section of your `haute.toml` looks like:

```toml
[project]
name = "motor-pricing"
pipeline = "rating/main.py"

[deploy]
target = "container"
model_name = "motor-pricing"

[deploy.container]
registry = ""
port = 8080
base_image = "python:3.11.9-slim"

[test_quotes]
dir = "tests/quotes"
```

### What each setting means

| Setting | What it does | Example |
|---|---|---|
| `target` | Tells Haute to build a Docker container | `"container"` |
| `model_name` | Used as the Docker image name | `"motor-pricing"` |
| `registry` | Where to push the image. Leave empty for local-only. | `"ghcr.io/myorg"` or `""` |
| `port` | The [port](../before-you-start.md#quick-glossary) the API server listens on inside the container (like an extension number on a phone system) | `8080` |
| `base_image` | The base Docker image to build from. It must name an exact patch version (`python:3.11.9-slim`) or a digest (`python@sha256:…`); a floating tag such as `python:3.11-slim` is refused before the build | `"python:3.11.9-slim"` |

The `registry` value is the address your IT team gave you for where Docker images are stored. If you don't know it, ask them: *"What's our container registry URL?"* They'll give you something like `ghcr.io/yourorg` or `myregistry.azurecr.io`. Put that value in `haute.toml`.

---

## Step 3: Give smoke and impact their endpoints

The smoke and impact steps call the running staging and production services, so they need their addresses. Once IT runs them, add them to `haute.toml`:

```toml
[ci.staging]
endpoint_url = "https://motor-pricing-staging.example.com"

[ci.production]
endpoint_url = "https://motor-pricing.example.com"
```

Without a production address, the impact step reports a first deployment instead of a comparison. The generated smoke and impact steps also pass `--endpoint-suffix "-staging"`, which `haute smoke` and `haute impact` accept only for Databricks, so they fail for this target until you remove that option from those two steps in your workflow file.

---

## Step 4: Deploy by merging to main

You don't run any deploy command. When you merge to main, CI automatically:

1. Validates your pipeline and scores test quotes
2. Generates an API app that wraps your pipeline (with two web addresses: `/quote` for scoring and `/health` for status checks)
3. Generates a Dockerfile (a recipe that tells Docker how to build the container)
4. Builds the Docker image
5. Pushes the image to your registry when `registry` is configured (otherwise the image remains only on that CI runner)

Once the image is in the registry, your IT team (or a separate platform workflow) can pull and run it. A successful `haute deploy` for this target means image packaging completed; it is not evidence that a live endpoint exists.

!!! success "What does success look like?"
    After the `haute deploy` job succeeds, you should see:

    1. **In the CI logs** - `✓ Deployed: motor-pricing v1`, followed by `Image:` and the image tag, such as `ghcr.io/yourorg/motor-pricing:a1b2c3d`
    2. **From your IT team** - confirmation that the image is running and the endpoint URL to test
    3. **In the CI workflow** - smoke and impact jobs can pass only after your hosting process runs the staging service and you have set its address ([Step 3](#step-3-give-smoke-and-impact-their-endpoints)); the impact report compares against production once its address is set too

    The deploy job's success confirms only validation and image packaging/push. It does not make the later smoke and impact jobs green by itself, and the pipeline is live only after your hosting process has started the image and its health checks pass.

---

## Step 5: Test the deployed API

Once your IT team has the container running, you can test it with Python:

```python
import requests

# Replace with the actual URL your IT team gives you
url = "http://<your-endpoint>:8080"

# Score a test quote
response = requests.post(
    f"{url}/quote",
    json=[{"IDpol": 99001, "VehPower": 7, "DrivAge": 42, "Area": "C", "VehBrand": "B12"}],
)
print(response.json())

# Check the service is alive
health = requests.get(f"{url}/health")
print(health.json())  # {"status": "ok", "model": "motor-pricing", ...}
```

---

## For your IT team

As an analyst, **you can stop reading here** - your job is done after Step 5 above. The sections below are reference material for whoever manages the container registry and hosting infrastructure.

??? note "What Haute generates (click to expand)"

    When Haute deploys, it creates a `.haute_build/` directory containing:

    | File | Purpose |
    |---|---|
    | `app.py` | API application (built with FastAPI) that wraps the scoring pipeline |
    | `Dockerfile` | Instructions for building the Docker image |
    | `deploy_manifest.json` | Metadata about what was deployed (version, schemas, artifacts) |
    | `artifacts/` | Copies of model files |
    | `utility/` | Your project's utility package, when the pipeline imports it |

    These are generated fresh on every deploy.

??? note "API contract (click to expand)"

    The generated container exposes two endpoints:

    | Endpoint | Method | Purpose |
    |---|---|---|
    | `/quote` | `POST` | Send quote data (JSON object or array), receive premium results in a stable envelope |
    | `/health` | `GET` | Returns a JSON object with `"status": "ok"`, the model name, the Haute version and the input and output schemas - used by infrastructure to check the service is alive |

    The `/quote` endpoint accepts a JSON object or array of quote objects and returns `{ "rows": [...], "row_count": n, "returned_rows": n, "truncated": false, "limit": 1000, "execution_metrics": {...} }`. For larger results, `rows` is capped at `limit`, `row_count` reports the full result size, and `truncated` is `true`. Send `Accept: application/x-ndjson` to receive every row as newline-delimited JSON instead. A request body over the size limit gets HTTP 413. No MLflow, no pandas - just JSON in, JSON out.

??? note "Registry options (click to expand)"

    | Registry | `registry` value in `haute.toml` | Credentials needed |
    |---|---|---|
    | **Docker Hub** | `docker.io/yourname` | Docker Hub username + access token |
    | **GitHub Container Registry** | `ghcr.io/yourorg` | GitHub username + personal access token |
    | **AWS ECR** | `123456789012.dkr.ecr.eu-west-1.amazonaws.com` | AWS credentials |
    | **Azure Container Registry** | `myregistry.azurecr.io` | Azure service principal |
    | **Local only** | `""` (empty) | None needed |

??? note "Handing off the image (click to expand)"

    The Docker image is pushed to the registry configured in `haute.toml`. On the machine that built it, you can also export it as a `.tar` file, using its full tag (which includes the registry when one is configured):

    ```bash
    docker save ghcr.io/yourorg/motor-pricing:a1b2c3d > motor-pricing.tar
    ```

    The image can run on any platform that supports Docker containers - Kubernetes, Docker Compose, AWS ECS, Azure Container Apps, or others. It requires:

    - `POST /quote` on the configured port (default 8080)
    - `GET /health` on the same port
    - No environment variables required at runtime, unless an Apply Optimisation node loads its result from an MLflow run or registered model; that container needs MLflow credentials

---

## Troubleshooting

### CI fails with "authentication required" on push

The deploy step has not logged in to the registry, or the credentials are wrong. Check that the deploy steps run the login line from [Step 1](#log-in-to-the-registry-before-the-deploy), and that `DOCKER_USERNAME` and `DOCKER_PASSWORD` are set correctly in your CI provider.

### CI fails during image build

Check the CI logs for the build step. Common causes:

- Missing Python dependencies - the image installs Haute's scoring runtime and the packages your model files need, not your `pyproject.toml`, so a package your pipeline code imports beyond those is not in the image
- Model file not found - ensure all model files referenced in your pipeline are committed to the repository

### Container crashes on startup (reported by IT)

Ask your IT team for the container logs. Common causes:

- Missing dependencies inside the image
- Model files not found - ensure they're committed and referenced correctly in your pipeline

### API returns errors

If the container is running but `/quote` returns errors, the pipeline is likely failing at runtime. Check the container logs for Python tracebacks. The most common cause is a schema mismatch between the request JSON and what your pipeline expects.
