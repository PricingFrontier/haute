<div align="center">

# Haute

### Open-source pricing engine for insurance teams.

[![PyPI](https://img.shields.io/pypi/v/haute?style=flat-square)](https://pypi.org/project/haute/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue?style=flat-square)](https://github.com/PricingFrontier/haute/blob/main/LICENSE)

[Documentation](https://pricingfrontier.github.io/haute/) · [Getting started](https://pricingfrontier.github.io/haute/getting-started/) · [GitHub](https://github.com/PricingFrontier/haute)

</div>

---

Haute is a free, open-source pricing engine. Build rating pipelines in a visual editor, train and score models, optimise prices, trace how any price was calculated, and deploy the result as a pricing API.

Every pipeline is a plain Python file. Analysts build on the canvas and developers work in the code, on the same files, so pricing work gets version control, code review, testing and CI/CD like any other software. Because the engine is open source, your team can inspect exactly how prices are calculated and run pipelines on your own infrastructure.

<!--
  Screenshot placeholder: the editor with a pricing pipeline on the canvas and a rating step's
  factor table open. README.md is also the PyPI page, so reference the image by absolute URL, e.g.
  <p align="center"><img src="https://raw.githubusercontent.com/PricingFrontier/haute/main/docs/assets/editor.png" alt="The Haute editor" width="900"></p>
-->

## Quick start

```bash
uv add haute
haute init
haute serve
```

`haute init` scaffolds a project with its configuration, test quotes and CI/CD workflows, and `haute serve` opens the editor in your browser. The [getting started guide](https://pricingfrontier.github.io/haute/getting-started/) covers setup in more detail.

## A pipeline is a Python file

```python
import haute
import polars as pl

pipeline = haute.Pipeline("motor_pricing")


@pipeline.data_input(config="config/data_input/quotes.json")
def quotes():
    """Read the quote rows."""


@pipeline.polars
def features(quotes: pl.LazyFrame) -> pl.LazyFrame:
    """Derive the rating features."""
    df = quotes.with_columns(
        vehicle_age=2026 - pl.col("vehicle_year"),
        driver_band=pl.col("driver_age").cut([25, 40, 65]).cast(pl.String),
    )
    return df


@pipeline.rating_step(config="config/rating_step/premium.json")
def premium(features):
    """Look up the rating factors and combine them into a premium."""


@pipeline.output(config="config/quote_response/priced.json")
def priced(premium):
    """Return the priced quotes."""


pipeline.connect("quotes", "features")
pipeline.connect("features", "premium")
pipeline.connect("premium", "priced")
```

Nodes that Haute configures for you, such as data inputs, rating steps and model scores, are declarations that point to a JSON settings file. Code you write is ordinary Polars. The canvas layout lives in a separate sidecar file, so the pipeline runs the same with or without the editor. Saving in the editor updates these files, and changes you save from your IDE show up in the editor.

## Features

### Build rating logic

- **Rating steps.** Factor tables of one to three factors, banded or raw, with spreadsheet copy and paste, default values, and combined outputs (multiply, add, min or max).
- **Banding.** Group continuous values with breakpoints, or map categories into rating groups.
- **Polars step builder.** Filter, derive, join, group, pivot and more without writing code. The steps are saved as Polars in the pipeline, and you can drop into code when you need to.
- **Data.** Parquet, CSV, JSON and Arrow files, plus Excel, Delta Lake, Iceberg and Databricks Unity Catalog through optional packages. Nested JSON or XML quotes are split into flat tables, and responses can be nested JSON.
- **Reuse.** Group nodes into submodels, and create instances that apply a node's logic to different inputs.
- **Live and batch.** Source switches send development or batch data through the same logic that prices live quotes.

### Explore, model and optimise

- **Explore.** Profile, pivot, chart and check relationships at any point in the pipeline, off the scoring path.
- **Train.** CatBoost, GLMs (RustyStats), XGBoost, LightGBM and EBMs, for regression or classification, with exposure weights, offsets, and Poisson, Gamma or Tweedie objectives depending on the model. Random, temporal or group splits, cross-validation, an optional held-out test set, and Optuna tuning.
- **Diagnose.** Gini, lift, actual versus expected, residuals, partial dependence and feature importance, plus SHAP for CatBoost, XGBoost and LightGBM, GLM coefficients and relativities, and EBM terms.
- **Reproduce.** Export a runnable training script from the same configuration, and log results to MLflow with an HTML model card.
- **Score.** Load models from MLflow runs or the model registry, and use their predictions in your rating logic.
- **Optimise.** With [price-contour](https://github.com/PricingFrontier/price-contour), choose the best candidate price for each quote, or optimise banded factors across a ratebook, within your constraints. Compare points on the efficient frontier, then save a result and apply it downstream.

### Explain and collaborate

- **Trace any price.** Click a cell to see how it was calculated: the path through the canvas, a waterfall of how the value built up, the factors each rating step chose, the band each value fell in, how a model's score breaks down, and how each input was derived.
- **Git without the command line.** Every save is recorded. Group saves into milestones with a message and version label, work on branches, push to a shared remote and open any past version, all from the editor. Protected branches such as `main` can't be written to directly.
- **Assistant (early preview).** Describe an edit in plain language, and the assistant makes it through the editor's validated save path. Bring your own model through Anthropic, OpenAI or Databricks.

<!--
  Screenshot placeholder: tracing a priced row, with the lineage highlighted on the canvas and the
  trace panel open. Use an absolute URL, e.g.
  <p align="center"><img src="https://raw.githubusercontent.com/PricingFrontier/haute/main/docs/assets/trace.png" alt="Tracing a price" width="900"></p>
-->

## Deploy a pricing API

`haute deploy` packages the live scoring path of a pipeline as an API that takes quotes and returns prices, leaving out training and analysis branches. It first runs your test quotes through the pipeline and can check expected outputs within a tolerance. A real deploy runs only in CI; `--dry-run` validates locally.

| Target | Current support |
|---|---|
| **Databricks** | Registers the pipeline and creates or updates a Model Serving endpoint. |
| **Docker** | Builds a serving image (`POST /quote`, `GET /health`) and optionally pushes it to a registry for your team to run. |
| **Azure Container Apps / AWS ECS / GCP Cloud Run** | Builds and pushes the image; pointing the running service at it is a manual step for now. |

`haute init` also generates CI/CD for GitHub Actions, GitLab CI or Azure DevOps. For Databricks, the workflow deploys to staging, smoke-tests it and writes an impact report comparing staging with production; the production release is a separate step that someone approves. For the three container clouds it stops after pushing the image.

Generic MLflow (pyfunc) models can be scored in the editor but not yet deployed. See the [deployment documentation](https://pricingfrontier.github.io/haute/deployment/) for targets and release workflows.

## Command line

| Command | What it does |
|---|---|
| `haute init` | Scaffold a project |
| `haute serve` | Open the editor |
| `haute run` | Run a pipeline from the terminal |
| `haute lint` | Check a pipeline without running it |
| `haute train` | Run an exported training script |
| `haute deploy` | Validate and deploy a pipeline (`--dry-run` validates only) |
| `haute smoke` | Send the test quotes to a live endpoint |
| `haute impact` | Compare staging and production pricing on a sample |
| `haute status` | Show a model's latest registered version |
| `haute gpu-setup` | Switch XGBoost to its GPU build |

Every command also runs as `python -m haute`, for managed machines that block console launchers.

## Status

Haute is currently in alpha. It is licensed under the [GNU Affero General Public License v3.0](https://github.com/PricingFrontier/haute/blob/main/LICENSE).

## Development checks

For local performance, benchmark, bundle, and memory smoke commands, see
[Local Performance Checks](docs/PERFORMANCE_CHECKS.md).
