# AWS ECS

This guide covers deploying a Haute pipeline to **AWS Elastic Container Service (ECS)** - Amazon's managed container platform. When you merge to main, CI builds a Docker image from your pipeline, pushes it to a registry, and (once the SDK integration lands) updates your ECS service.

!!! info "What is AWS ECS?"
    ECS is Amazon's service for running Docker containers in the cloud. You don't manage individual servers - AWS handles that. You tell it which Docker image to run and how much compute to allocate, and it keeps your API available. Think of it as a managed hosting service for your pricing API.

!!! warning "Platform service update is not yet implemented"
    Haute currently builds and pushes the Docker image for AWS ECS, but the automatic service update step (telling ECS to use the new image) is still in development. After CI pushes the image, your IT team will need to update the ECS service manually until the SDK integration lands.

!!! note "This target requires IT support"
    AWS ECS involves cloud infrastructure setup (registries, clusters, IAM policies) that is done by an IT or platform team. The "Infrastructure setup" section below is written **for your IT team**. As an analyst, your role is to configure `haute.toml` and merge to main - CI and IT handle the rest.

    If your organisation uses Databricks, the [Databricks target](databricks.md) is simpler and doesn't involve containers.

---

## Prerequisites

- **Python 3.11+** and **Haute** installed on your machine
- An **AWS account** with ECS, ECR, and IAM access (your IT team manages this)
- A Haute project initialised with the AWS ECS target - open your VS Code terminal and run:

```powershell
haute init --target aws-ecs --ci github
```

!!! tip "Your team may have already done this"
    If you cloned an existing project that already has a `haute.toml` file, skip this step - it's already initialised.

!!! note "Before you begin"
    Your IT team needs to set up the AWS infrastructure first (ECR repository, ECS cluster, IAM credentials). If that hasn't been done yet, send them the [Infrastructure setup for IT](#infrastructure-setup-one-time-done-by-it) section at the bottom of this page. Once they've done it, they'll give you the values you need for the steps below.

---

## Step 1: Configure `haute.toml`

```toml
[project]
name = "motor-pricing"
pipeline = "rating/main.py"

[deploy]
target = "aws-ecs"
model_name = "motor-pricing"

[deploy.container]
registry = "123456789012.dkr.ecr.eu-west-1.amazonaws.com"
port = 8080
base_image = "python:3.11-slim"

[deploy.aws-ecs]
region = "eu-west-1"
cluster = "pricing-cluster"
service = "motor-pricing"

[test_quotes]
dir = "tests/quotes"
```

### What each setting means

| Setting | What it does | Example |
|---|---|---|
| `target` | Tells Haute to deploy to AWS ECS | `"aws-ecs"` |
| `registry` | Your ECR repository URI (without the image name) | `"123456789012.dkr.ecr.eu-west-1.amazonaws.com"` |
| `port` | The port your API listens on | `8080` |
| `region` | AWS region where your ECS cluster lives | `"eu-west-1"` |
| `cluster` | Name of your ECS cluster | `"pricing-cluster"` |
| `service` | Name of your ECS service | `"motor-pricing"` |

---

## Step 2: Add credentials to CI

CI needs AWS credentials to push images to ECR and update the ECS service. Add these as encrypted secrets in your CI provider (your IT team will have the values from the infrastructure setup):

| Secret name | Value |
|---|---|
| `DOCKER_USERNAME` | `AWS` |
| `DOCKER_PASSWORD` | ECR auth token (CI handles refresh automatically) |
| `AWS_ACCESS_KEY_ID` | Your AWS access key |
| `AWS_SECRET_ACCESS_KEY` | Your AWS secret key |
| `AWS_DEFAULT_REGION` | e.g. `eu-west-1` |

How to add them depends on your CI provider - see [GitHub Actions](../ci/github-actions.md#step-1-add-your-credentials-as-github-secrets), [GitLab](../ci/gitlab.md#step-1-add-your-credentials-as-cicd-variables), or [Azure DevOps](../ci/azure-devops.md#step-1-create-a-variable-group-for-credentials).

---

## Step 3: Deploy by merging to main

You don't run any deploy command. When you merge to main, CI automatically:

1. Validates your pipeline and scores test quotes
2. Generates a FastAPI app and Dockerfile
3. Builds the Docker image
4. Pushes the image to your ECR repository
5. *(Coming soon)* Updates the ECS service to use the new image

!!! success "What does success look like?"
    After a successful merge to main, you should see:

    1. **In your CI provider** - all pipeline steps show green ✓ (validation, build, push)
    2. **In the CI logs** - a message like `Pushed motor-pricing:a1b2c3d to 123456789012.dkr.ecr.eu-west-1.amazonaws.com`
    3. **In the AWS Console** - your ECS service shows a new task running with the latest image

    If CI is green and the ECS service is healthy, your pipeline is live.

Until the automatic service update lands, CI will output the image tag. Your IT team can then update the service manually:

```bash
aws ecs update-service \
  --cluster pricing-cluster \
  --service motor-pricing \
  --force-new-deployment
```

---

## Step 4: Test the API

Once the service is running, find the endpoint URL in the AWS Console (**ECS** → **Services** → your service → **Tasks** → **Public IP** or load balancer URL).

```python
import requests

response = requests.post(
    "http://<your-endpoint>:8080/quote",
    json=[{"IDpol": 99001, "VehPower": 7, "DrivAge": 42, "Area": "C", "VehBrand": "B12"}],
)
print(response.json())
```

---

## Troubleshooting

### "Access denied" when pushing to ECR

Your AWS credentials don't have permission to push images. Check the IAM policy includes the ECR actions listed in the infrastructure setup section.

### ECS service not updating

If the service doesn't pick up the new image, force a new deployment:

```bash
aws ecs update-service \
  --cluster pricing-cluster \
  --service motor-pricing \
  --force-new-deployment
```

### Container keeps restarting

Check the task logs in the AWS Console: **ECS** → **Clusters** → your cluster → **Tasks** → click the task → **Logs**. Common causes are missing dependencies or model files.

### Health check failing

Make sure the health check path is `/health` and the port matches your `haute.toml` `port` setting.

---

## Infrastructure setup (one-time, done by IT)

As an analyst, **you can skip this section entirely** - send this page to whoever manages your AWS account and they'll set things up for you.

??? note "1. Create an ECR repository (click to expand)"

    ECR (Elastic Container Registry) is where Docker images are stored:

    ```bash
    aws ecr create-repository \
      --repository-name motor-pricing \
      --region eu-west-1
    ```

    Note the repository URI - it looks like:
    ```
    123456789012.dkr.ecr.eu-west-1.amazonaws.com/motor-pricing
    ```

??? note "2. Create an ECS cluster (click to expand)"

    ```bash
    aws ecs create-cluster --cluster-name pricing-cluster
    ```

    Or create one in the AWS Console: **ECS** → **Clusters** → **Create Cluster**.

??? note "3. Create a task definition and service (click to expand)"

    This defines how the container runs (CPU, memory, port mappings) and keeps it running. Set this up through the AWS Console or CloudFormation.

    Key settings:

    - **Container port:** match the `port` in `haute.toml` (default 8080)
    - **Health check path:** `/health`
    - **CPU/Memory:** 256 CPU / 512 MB is fine for small workloads

??? note "4. Create an IAM user for deployments (click to expand)"

    Create an IAM user (or role) with permissions to push images and update services:

    ```json
    {
      "Version": "2012-10-17",
      "Statement": [
        {
          "Effect": "Allow",
          "Action": [
            "ecr:GetAuthorizationToken",
            "ecr:BatchCheckLayerAvailability",
            "ecr:GetDownloadUrlForLayer",
            "ecr:PutImage",
            "ecr:InitiateLayerUpload",
            "ecr:UploadLayerPart",
            "ecr:CompleteLayerUpload"
          ],
          "Resource": "*"
        },
        {
          "Effect": "Allow",
          "Action": [
            "ecs:UpdateService",
            "ecs:DescribeServices"
          ],
          "Resource": "*"
        }
      ]
    }
    ```

    Generate an access key for this user and give the **Access Key ID** and **Secret Access Key** to whoever is setting up CI secrets.
