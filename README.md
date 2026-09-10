<div align="center">

# Haute

### Open-source pricing engine for insurance teams.

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue?style=flat-square)](LICENSE)

[Documentation](https://haute.dev) · [Getting started](https://haute.dev/getting-started/)

</div>

---

Haute is a free, open-source pricing engine for insurance teams. Build rating pipelines visually, explore data, train and score models, optimise prices, and trace how each price was calculated.

Pipelines are saved as readable Python with configuration alongside. Pricing analysts can work on a canvas, developers can work in code, and both use the same project files, with standard version control, testing, and deployment workflows.

## Build your pricing pipeline

Connect data sources, transformations, models, rating steps, and outputs in a browser-based editor. Preview data at each step, configure rating logic through tables and forms, and write focused Python expressions in the built-in code editor when you need custom calculations.

Rating steps support multiple factor tables, banded and raw categorical factors, spreadsheet-style copy and paste, and combined outputs. Banding nodes turn continuous or categorical values into rating groups. Join data through the canvas, and group related steps into reusable submodels to keep larger pipelines readable.

Changes saved in the visual editor update the Python and configuration files on disk. External file changes are picked up by a file watcher, keeping visual and code-based workflows connected.

### Reuse logic across your pipeline

Instances let you apply shared logic to different inputs. Reuse a transformation, score the same model against different datasets, or connect a shared submodel in several places. Update the original definition and its instances use the updated logic, so you can maintain it in one place.

### Your work stays yours

The pricing engine is open source, so your team can inspect, test, and extend how it calculates results. Pipelines and configuration remain ordinary project files that you can manage with standard Python tools and run with the open-source Haute library in your own infrastructure.

### An assistant for pipeline authoring (early preview)

Describe an edit in plain language, such as "add this parquet file as a data source" or "band vehicle age into five groups". The assistant can inspect your graph and data columns, then make changes through the editor's validated save path. Its edits are saved to the same project files as your own.

Bring your own model through Anthropic, OpenAI, or Databricks.

## Audit history and team collaboration

Haute's built-in Git panel keeps a history of how your pricing pipeline evolved, with recorded versions, authors, and change messages. Save incremental progress and group it into meaningful milestones, giving colleagues and reviewers a record of what changed and the context your team recorded alongside it.

Analysts can work on separate branches and share their changes through a shared Git repository, all from the visual editor. The underlying Python and configuration files fit standard code review workflows, so pricing and engineering teams can collaborate on the same project using their preferred tools.

## Explore data and build models

Attach an Explore node at any point in a pipeline to inspect the data there. Profile columns, check data quality, build pivot tables, and create charts without adding that analysis work to the live scoring path.

Train CatBoost models and GLMs through dedicated modelling nodes, with insurance conventions such as exposure weights, offsets, and Poisson, Gamma, and Tweedie losses. Model development includes:

- **Evaluation:** random, temporal, or group-based partitions, validation options, and an optional held-out final test.
- **Tuning:** CatBoost hyperparameter searches using the configured validation strategy.
- **Diagnostics:** metrics and charts such as Gini, actual-versus-expected, residuals, SHAP, and GLM coefficients, depending on the model.
- **Reproducibility:** saved models and runnable Python training scripts exported from the same configuration used by the editor.
- **Model documentation:** explicitly log completed results to MLflow, including a self-contained HTML model card for review and sharing.

For scoring, Haute supports CatBoost and RustyStats models, plus other frameworks through compatible MLflow models. Scoring nodes handle model loading, feature validation, and supported type conversions, so you can combine predictions with rating logic in the same pipeline.

## Optimise prices and explain the result

Haute integrates constrained price optimisation through the open-source `price-contour` library. Choose among scored candidate prices for each quote, or optimise banded rating factors across a ratebook, using your objectives and constraints.

Inspect convergence diagnostics and efficient-frontier results to understand the trade-offs. Save a selected result and apply it to new data downstream in your pricing pipeline.

### Trace a price through the pipeline

Click an output cell to inspect how its value was calculated:

```text
base rate → area factor → discount → loading → final price
```

The graph highlights the contributing path, and the trace panel shows the values used along the way. Rating traces show selected factors and default usage; banding traces show which source value selected a band. Model and optimiser traces provide explanations specific to those steps, with upstream lineage available for further inspection.

Trace results are cached for reuse, making it easier to explore different rows and outputs. This gives analysts, reviewers, and stakeholders a way to investigate a price directly in the pipeline.

### One pipeline for live and batch pricing

Use the same pricing logic for individual quotes, batch scoring, what-if work, and impact analysis. Source switches let development and batch data feed the same downstream logic used for live requests.

Polars powers execution. Preview caching reuses intermediate results where valid, while batch execution builds an optimised lazy plan. Timing breakdowns help identify expensive steps, and memory estimates help assess large training jobs before they start.

## Deploy your pricing API

Haute packages the live scoring path of your pipeline as an API that accepts quote data and returns pricing outputs. Training and analysis branches stay outside that scoring path.

Pre-deployment validation runs test quotes through the pipeline and can check expected outputs with tolerances. Impact analysis compares staging and production predictions across a sample, including segment-level changes, to support release review.

| Target | Current support |
|---|---|
| **Databricks** | Registers the packaged pipeline and creates or updates a Model Serving endpoint. |
| **Docker** | Builds a serving container image and optionally pushes it to a registry for your team to run. |
| **Azure Container Apps / AWS ECS / GCP Cloud Run** | Builds and pushes a container image; updating the running cloud service currently requires a manual step. |

Project scaffolding includes CI/CD templates for GitHub Actions, GitLab CI, and Azure DevOps, supporting testing, impact analysis, and approval before production promotion. Your platform team configures the infrastructure and release controls for your environment.

See the [deployment documentation](https://haute.dev/deployment/) for target capabilities and release workflows.

## Getting started

```bash
uv add haute
haute init
haute serve
```

`haute serve` opens the visual editor in your browser. See the [getting started guide](https://haute.dev/getting-started/) for installation and configuration.

Haute is currently in alpha. The library is licensed under the [GNU Affero General Public License v3.0](LICENSE).

## Development checks

For local performance, benchmark, bundle, and memory smoke commands, see
[Local Performance Checks](docs/PERFORMANCE_CHECKS.md).
