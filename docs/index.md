---
template: home.html
hide:
  - navigation
  - toc
  - feedback
---

<style>
  /* Hide the default page title on the home page */
  .md-typeset h1 { display: none; }
</style>

## :material-help-circle-outline: What is Haute?

Haute is a free, open-source pricing engine for insurance teams. Build rating pipelines in a visual editor, keep everything as standard Python, and deploy to a live scoring API - all without leaving your IDE or waiting on engineering.

---

## :material-target: The problem it solves

Insurance pricing teams have been left behind. While the rest of the data science world moved to open-source tooling, version control, automated testing, and continuous deployment, pricing teams have been stuck with expensive proprietary platforms that don't automate anything, don't teach transferable skills, and lock teams into clunky workflows that haven't changed in a decade.

<div class="grid cards" markdown>

- :material-cash-lock:{ .lg .middle } **Expensive, closed platforms**

    ---

    Six-figure licences for software that does less than free, open-source alternatives. No transparency, no flexibility, no escape.

- :material-school-outline:{ .lg .middle } **No skills development**

    ---

    Proprietary tools don't teach analysts to code or build their own solutions. When the tool can't do something, neither can the team.

- :material-sync-off:{ .lg .middle } **No automation**

    ---

    Manual exports, manual deployments, manual everything. Best practices from software engineering and data science never make it through the door.

</div>

Haute fixes this by packaging modern data science and engineering tooling into a ready-to-go interface. Analysts get version control, automated CI/CD, experiment tracking, and a visual editor - all built on standard Python they can learn from and extend.

## :material-account-group: Who is this for?

Teams looking to keep in line with data science and engineering best practices and advancements, rather than falling further behind.

<div class="grid cards" markdown>

- :material-chart-line:{ .lg .middle } **Pricing and actuarial teams**

    ---

    Build, test, and deploy rating pipelines without waiting on engineering or learning DevOps from scratch.

- :material-trending-up:{ .lg .middle } **Teams outgrowing legacy platforms**

    ---

    Move off expensive proprietary tools without losing structure. Haute gives you the same guardrails with modern, open-source foundations.

- :material-lightbulb-on-outline:{ .lg .middle } **Teams investing in their analysts**

    ---

    Everything runs on standard Python. Skills learned here transfer everywhere - not just within one vendor's ecosystem.

</div>

---

## :material-cog: How it works

A pricing pipeline is a Python file. Each step is a function - load data, join sources, score a model, calculate a premium. Haute connects them into a graph.

```python
import haute
import polars as pl

pipeline = haute.Pipeline("motor_pricing")

@pipeline.data_source(config="config/data_source/policies.json")
def policies() -> pl.LazyFrame:
    return pl.scan_parquet("data/policies.parquet")

@pipeline.model_score(config="config/model_scoring/frequency.json")
def frequency(policies: pl.LazyFrame) -> pl.LazyFrame:
    from haute.graph_utils import score_from_config
    return score_from_config(policies, config="config/model_scoring/frequency.json")

@pipeline.polars
def premium(frequency: pl.LazyFrame) -> pl.LazyFrame:
    return frequency.with_columns(
        premium=pl.col("pred_freq") * pl.col("pred_sev") * 1.15,
        margin=pl.col("premium") - pl.col("burn_cost"),
    )

pipeline.connect("policies", "frequency")
pipeline.connect("frequency", "premium")
```

That same file is also a visual graph. Run `haute serve` and your pipeline appears in a browser-based editor - nodes for data sources, models, transforms, and outputs, connected by edges showing how data flows through the rating structure.

Rating step nodes support spreadsheet-style pricing work: many factor tables, raw string factors as well as banded factors, Excel-style paste/copy, optional combined outputs, and optional Polars code that runs after the table lookups. Their sidecar config files keep factor tables concise with nested factor-value maps, while the editor and runtime use canonical row entries. Banding nodes use compact boxed rule cells with range paste, whole-factor copy, and breakpoint ordering flags. When you trace a rating-step, banding, model, or optimiser output, Haute focuses the story on the values that actually drive the clicked result, with the full upstream trace still available for audit.

---

## :material-star-shooting: What makes it different

<div class="grid cards" markdown>

- :material-language-python:{ .lg .middle } **Your code, not ours**

    ---

    Pipelines are plain `.py` files. Not a proprietary format, not a visual-only diagram. You can read it, test it with pytest, version-control it, and take it with you. Any model that runs in Python works here.

    [:octicons-arrow-right-24: Getting started](getting-started/index.md)

- :material-shield-check:{ .lg .middle } **Safety is not optional**

    ---

    Every release follows the same path: staging, impact analysis, smoke test, human approval, production. One wrong factor can misprice an entire book - so there are no shortcuts.

    [:octicons-arrow-right-24: Safety pipeline](deployment/index.md)

- :material-cloud-upload:{ .lg .middle } **Deploy anywhere**

    ---

    Databricks, Azure Container Apps, AWS ECS, GCP Cloud Run, or plain Docker. Analysts never need Docker or cloud CLIs installed - CI handles everything.

    [:octicons-arrow-right-24: Deploy targets](deployment/index.md)

- :material-source-branch:{ .lg .middle } **CI/CD comes free**

    ---

    `haute init` generates GitHub Actions, GitLab CI, or Azure DevOps pipelines. Every change is tested, validated, and impact-analysed before it reaches production.

    [:octicons-arrow-right-24: CI/CD setup](deployment/ci/github-actions.md)

- :material-magnify-scan:{ .lg .middle } **Execution tracing**

    ---

    Click any row and trace exactly how it becomes a price. Per-node snapshots show which columns changed, what was added, how rating tables and banding rules selected their values, and how long each step took - no print statements, no guesswork.

- :material-source-branch-check:{ .lg .middle } **Git without the command line**

    ---

    Branch, save, revert, and submit for review - all from the visual editor. Built for teams where not everyone speaks git, with guardrails to prevent mistakes on shared branches.

</div>

---

## :material-rocket-launch: Deploy

You pick the target that matches your infrastructure. Haute handles the rest.

| Target | Description |
|---|---|
| **Databricks** | Deploys to Databricks Model Serving. For teams already on the Databricks platform. |
| **Azure Container Apps** | Deploys to Azure's serverless container platform. |
| **AWS ECS** | Deploys to Amazon's container orchestration service. |
| **GCP Cloud Run** | Deploys to Google Cloud's serverless container platform. |
| **Docker** | Builds a container image. You choose where to run it. |

Analysts only ever need Python installed. Everything else - building, testing, deploying - happens automatically when you push your changes. `haute init` generates the CI/CD pipeline for your platform (GitHub Actions, GitLab CI, or Azure DevOps), so there's nothing to set up manually.

Every change goes through the same process: deploy to staging, run smoke tests against real data, analyse the financial impact, and wait for approval before promoting to production. No one can skip a step, and no one can deploy from their laptop.

!!! info "The analyst's workflow"
    Edit your pipeline :material-arrow-right: `haute serve` to preview :material-arrow-right: push to Git :material-arrow-right: everything else is automatic.

---

## :material-speedometer: Quick start

```bash
uv add haute
haute init
haute serve   # open the visual editor
```

Three commands to go from nothing to a working project open in the visual editor.

[:material-arrow-right: **Full getting started guide**](getting-started/index.md){ .md-button .md-button--primary }
[:material-github: **View on GitHub**](https://github.com/pricingfrontier/haute){ .md-button }
