<div align="center">

# Haute

### Open-source pricing engine.

<br>

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue?style=flat-square)](LICENSE)
[![Databricks](https://img.shields.io/badge/deploy-Databricks-FF3621?style=flat-square&logo=databricks&logoColor=white)](https://databricks.com)
[![Azure Container Apps](https://img.shields.io/badge/deploy-Azure_Container_Apps-0078D4?style=flat-square&logo=microsoftazure&logoColor=white)](https://azure.microsoft.com/en-us/products/container-apps)
[![AWS ECS](https://img.shields.io/badge/deploy-AWS_ECS-FF9900?style=flat-square&logo=amazonaws&logoColor=white)](https://aws.amazon.com/ecs/)
[![GCP Cloud Run](https://img.shields.io/badge/deploy-Cloud_Run-4285F4?style=flat-square&logo=googlecloud&logoColor=white)](https://cloud.google.com/run)
[![Docker](https://img.shields.io/badge/deploy-Docker-2496ED?style=flat-square&logo=docker&logoColor=white)](https://docker.com)

</div>

---

Haute is a free, open-source pricing engine. It gives you a visual editor that runs in your browser - you build pricing pipelines by connecting nodes on a canvas rather than writing code from scratch.

Everything you build is saved as a standard Python file on disk. Not a proprietary format. Not a binary export. A Python file you can open, read, version, and take with you.

---

## Getting started

One line to install, one to initialise the project, one to open your model.

```bash
uv add haute
haute init
haute serve
```

---

## Why open source matters for pricing

Pricing models sit at the centre of regulated businesses. They determine what customers pay, they get scrutinised by regulators, and they need to be understood by people who didn't build them. When the tool that produces those models is a black box, everyone - actuaries, regulators, auditors - has to take it on trust that the numbers are right.

Open source changes that relationship. The engine that calculates your prices is fully visible. Anyone on your team can inspect how it works, verify its behaviour, or extend it. There is no hidden logic, no opaque compilation step, no proprietary runtime sitting between your model and your output.

This also means your work is portable. Your pipelines are Python files. Your models are standard formats. Your data stays in your infrastructure. If you ever want to stop using Haute, everything you've built still works: your pipelines are ordinary Python files (with JSON config saved alongside) that run with the open-source library - there's no hosted service or proprietary runtime to lose access to.

Being open source and code means Haute can lean on best-in-class tools and engineering practices instead of reinventing them. Polars, Catboost, MLFlow, GIT, CI/CD, Haute doesn't try to rebuild these things behind a proprietary wall. It connects them and wraps them in an interface designed for pricing work.

---

## A visual editor, and a code editor

Haute opens in your browser. You build pricing pipelines by dragging nodes onto a canvas and connecting them - data sources, transforms, model scores, rating steps, outputs. Click any node and its data appears instantly in a preview table below. For nodes that involve data logic, there's a built-in code editor with line numbers, auto-indentation, and bracket matching - you write small, focused pieces of Python, not entire programs.

The visual editor and the code are always the same thing. Every node you create, every connection you draw, every parameter you set - it's all reflected in a Python file on disk in real time. This means a team can have some members working visually and others working in code, on the same pipeline, at the same time.

If you've built rating structures in spreadsheets or used other pricing tools, this will feel familiar - except everything runs on your machine and you own the output.

Rating step nodes are built for the messy middle of pricing work: many factor tables, raw string factors as well as banded factors, Excel-style copy and paste, optional combined outputs, and optional Polars code that runs after the table lookups. Their sidecar config files store lookup entries as compact factor-value maps, while the editor and runtime keep the row shape they need. Banding nodes use compact boxed cells for continuous, categorical, and breakpoint rules, with range paste, whole-factor copy, and ordering flags for breakpoints. Compact edge-join nodes let you join another dataframe into an existing connection without dropping into custom code, while still saving as standard Python.

---

## An AI assistant that authors your pipeline (early preview)

Open the Assistant panel and describe what you want in plain language — "add
`data/nb_batch.parquet` as a data source", "band vehicle age into five groups" — and the
assistant reads your graph, checks the real columns in your data, and makes the edit
through the same validated save path the visual editor uses. Every change lands in your
Python file, on your git working branch, exactly as if you had made it by hand.

Bring your own model through an explicit Anthropic, OpenAI, or Databricks provider.
Databricks model serving reuses the project's standard `DATABRICKS_HOST` and
`DATABRICKS_TOKEN` values from `.env`; its serving URL is derived automatically, so
workspace identity is not duplicated in `haute.toml`. Credentials stay in `.env`;
conversations persist per project in the untracked `.haute/` directory, so they survive
server restarts and never enter git.

This is an early preview — the core authoring loop is solid and fully tested, but expect
rough edges while the experience gets tuned.

---

## Click any price. See exactly how it was calculated.

Click any cell in your output table. Haute traces the path through every node that contributed to it, showing the value at each step:

```
base rate → area factor → discount → loading → final price
```

The graph highlights the path visually. Nodes that contributed glow. The rest fade. A sidebar shows what happened at each step - which values were used, what changed, and what the result was. Rating steps show the table factors used, the selected value from each table, default usage, and combined-output inputs. Banding traces show the source value that selected the band and continue the upstream lineage.

The first click runs through the full pipeline and caches the result. Every click after that pulls from cache - the trace updates instantly. Click a different row, a different column, a different output. Each one is immediate.

When you need to show a regulator, a stakeholder, or a colleague exactly how a price was derived, this gives you that answer in a click.

---

## Your file on disk is the source of truth

There is no database behind Haute. No proprietary project file. Every pipeline is a `.py` file on disk.

Edit a node in the visual editor and the Python file updates. Edit the Python file in a text editor and the visual editor updates. Both views stay in sync automatically through a file watcher - changes propagate in under a second.

This means your pipeline works with every tool that understands files: version control, code review, text editors, automated testing. You don't need to learn those tools to use Haute, but they're there when your team is ready for them.

---

## Built to be fast

When you click a node, you want to see its data now. Haute caches results at every node in the pipeline, so previewing data at any point is near-instant. Change a rating factor and only the downstream nodes recalculate - everything upstream stays cached.

For batch work, Haute builds a full execution plan and optimises it end-to-end before processing your data. This is a different execution strategy to the one used for preview, and it's chosen automatically - you don't need to configure anything.

There's a built-in timing breakdown that shows how long each step took, colour-coded so you can see at a glance where time is being spent.

---

## Built-in price optimisation

Haute includes a constrained price optimisation engine as a core part of the pipeline.

You can optimise prices in real time (per-record, gradient-based) or across a ratebook (factor-table, coordinate descent). Set constraints - minimum and maximum prices, factor bounds - and the optimiser finds the best solution within them.

The results include convergence diagnostics, scenario distributions, and an efficient frontier showing the tradeoff between your objectives. You can save the optimisation output and apply it downstream in the same pipeline.

---

## Any model, same interface

Haute works with any Python model. CatBoost, scikit-learn, LightGBM, XGBoost, Rustystats - if it runs in Python, it works in Haute.

Behind the scenes, every model is wrapped in a uniform interface. You can swap a CatBoost model for an XGBoost model without changing your pipeline configuration. Feature type casting is handled automatically - the model receives data in the format it expects.

Models are loaded with an intelligent cache that watches for file changes. Update a model file and the next pipeline run picks it up automatically.

---

## Safe deployments, built in

When you're ready to go live, Haute packages your pipeline and deploys it as a service that returns a price when it receives a request.

**Before anything goes live**, Haute runs your pipeline against a set of test inputs. If anything breaks, the deployment stops. You can also set expected outputs with tolerances, so you're testing not just "does it run" but "does it produce the right results."

**Before you promote to production**, Haute scores a sample through both the new version and the current one, and produces a segment-level impact report - how many results changed, by how much, and which groups were affected most. You review the report, approve the change, and promote. Every deployment records what changed, who approved it, and what it replaced.

You choose where to deploy during initial setup:

| Target | What it does |
|---|---|
| **Databricks** | Deploys to a Databricks serving endpoint |
| **Docker** | Builds a container that runs anywhere |
| **Azure / AWS / GCP** | Deploys to your cloud provider |

The infrastructure is set up once by whoever manages your technical environment. After that, every change follows the same path: test, review, deploy.

---

## Version control without the learning curve

Haute includes a built-in panel for saving and managing versions of your work. You don't need to know what Git is - the interface gives you simple actions: save your progress, see your history, go back to a previous version, and submit your work for review.

Behind the scenes, it uses Git with guardrails. Protected branches can't be overwritten. Destructive actions create automatic backups. Switching between versions saves your current work first. The complexity is handled for you; you just see a clean history of your changes.

---

## Knows your machine's limits

Before training a model on a large dataset, Haute probes a sample of your data to estimate how much memory the full run will need. If it would exceed your machine's available memory, it tells you before you start - and suggests a safe dataset size.

This is a small detail, but it catches the kind of silent crash that can lose work before it happens. The estimate reads your machine's available memory directly; inside a memory-capped container it can't see the cap, and where it can't read memory at all it falls back to a conservative default.

---

## Keep complex pipelines manageable

Pricing work often has many steps. Submodels let you group a set of nodes into a single collapsible block. In the main view, you see one clean node. Double-click it to see the internals. Click the breadcrumb to go back.

You can reuse a submodel across different pipelines - build something once and reference it everywhere. If you change your mind, dissolve the submodel and the nodes expand back into the parent pipeline.

---

## One pipeline, many uses

Build it once. Use it five ways:

- **Live pricing** - a single request, real-time response from your deployed service
- **Batch scoring** - process large datasets on your machine
- **What-if analysis** - change inputs and watch the price move through every step
- **Impact analysis** - compare a new version against the current one across your data
- **Price tracing** - click any output and see exactly how it was calculated

No rebuilding, no re-exporting, no maintaining separate versions for different use cases.

---

## Open source

Licensed under the [GNU Affero General Public License v3.0](LICENSE).

---

## Getting started

```bash
uv add haute
haute init
haute serve
```

`haute serve` opens the visual editor in your browser. From there, you're building.

The local editor API is protected by a per-session token that `haute serve`
wires into the browser automatically. Keep the default loopback host for normal
use. For local debugging only, set `HAUTE_DISABLE_LOCAL_SESSION_AUTH=1` before
starting the server to disable the token and WebSocket origin checks.

## Development checks

For local performance, benchmark, bundle, and memory smoke commands, see
[Local Performance Checks](docs/PERFORMANCE_CHECKS.md).
