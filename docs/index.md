---
template: home.html
title: Home
hide:
  - navigation
  - toc
  - feedback
---

<div class="grid cards" markdown>

-   :material-rocket-launch-outline:{ .lg .middle } __Getting started__

    ---

    Set up your machine, install Haute and find your way around the editor.

    - [Setting up your environment](getting-started/environment.md)
    - [Installing Haute](getting-started/installing-haute.md)
    - [UI overview](getting-started/ui-overview.md)
    - [Polars](getting-started/polars.md)

-   :material-graph-outline:{ .lg .middle } __Building models__

    ---

    Prepare your data, build pipelines from nodes and see how Haute runs them.

    - [Overview](building-models/index.md)
    - [Preparing your data](building-models/preparing-your-data.md)
    - [Node types](building-models/nodes/index.md)
    - [Execution strategy](building-models/execution-strategy.md)
    - [Filesystem portability](building-models/filesystem-portability.md)

-   :material-cloud-upload-outline:{ .lg .middle } __Deployment__

    ---

    Release a pipeline as a pricing API through CI/CD.

    - [Overview](deployment/index.md)
    - [Before you start](deployment/before-you-start.md)
    - Targets: [Databricks](deployment/targets/databricks.md), [Docker](deployment/targets/docker.md), [AWS ECS](deployment/targets/aws.md), [Azure Container Apps](deployment/targets/azure.md)
    - CI/CD: [GitHub Actions](deployment/ci/github-actions.md), [GitLab](deployment/ci/gitlab.md), [Azure DevOps](deployment/ci/azure-devops.md)

</div>

## Node reference

<div class="haute-node-reference" markdown>

| Category | Nodes |
|---|---|
| Inputs | [Quote Input](building-models/nodes/quote-input.md) · [Data Input](building-models/nodes/data-input.md) · [Constant](building-models/nodes/constant.md) |
| Transforms | [Polars](building-models/nodes/polars.md) · [Edge Join](building-models/nodes/edge-join.md) · [Banding](building-models/nodes/banding.md) · [Rating Step](building-models/nodes/rating-step.md) · [Scenario Expander](building-models/nodes/scenario-expander.md) · [Source Switch](building-models/nodes/source-switch.md) |
| Models | [Model Training](building-models/nodes/model-training.md) · [Model Score](building-models/nodes/model-score.md) · [External File](building-models/nodes/external-file.md) |
| Optimisation | [Optimiser](building-models/nodes/optimiser.md) · [Optimiser Apply](building-models/nodes/optimiser-apply.md) |
| Outputs | [Output](building-models/nodes/output.md) · [Data Output](building-models/nodes/data-output.md) |
| Analysis | [Explore](building-models/nodes/explore.md) |
| Organisation | [Submodel](building-models/nodes/submodel.md) · [Instances](building-models/nodes/instances.md) |

</div>

## Quick start

```bash
uv add haute
haute init
haute serve
```

`haute serve` opens the editor in your browser. [Installing Haute](getting-started/installing-haute.md) walks through each step.
