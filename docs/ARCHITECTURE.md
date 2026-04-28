# Haute - Architecture & Plan

**Open-source pricing engine for insurance teams**

---

## 1. Vision

Haute is an open-source Python library that gives insurance pricing teams a **code-first, GUI-friendly** way to build, test, and deploy pricing pipelines. It bridges the gap between:

- **Pricing analysts** who are comfortable with visual tools
- **Engineering best practices** that come from working in code: version control, CI/CD, unit tests, linting, code review

The core principle: **Python code is the source of truth**. The GUI is a live, editable view of that code. Edit either one - the other stays in sync.

Haute deploys pipelines as **live pricing APIs**. The team picks the target that matches their infrastructure - Databricks, a Docker container, SageMaker, or Azure ML. Every target gets the same safety pipeline: staging, impact analysis, smoke test, approval gate, production. See `docs/DEPLOY_DESIGN.md` for the full design.

---

## 2. What Haute Is (and Isn't)

### Haute IS:
- A Python DSL for defining pricing pipelines as code
- A browser-based React Flow UI for visualising and editing those pipelines
- A deployment tool that packages pipelines as live pricing APIs (Databricks, container, SageMaker, Azure ML)
- A CLI that scaffolds projects with CI/CD, linting, tests, and deployment config out of the box
- An opinionated framework that makes it hard to do the wrong thing

### Haute IS NOT:
- A general-purpose ML platform — it trains and deploys **pricing models**, not arbitrary ML workloads. Model training is integrated (CatBoost, GLM via RustyStats, split strategies, SHAP, cross-validation, MLflow logging) but scoped to the pricing workflow.
- A proprietary black box - everything is `.py` files on disk

---

## 3. Core Concepts

### 3.1 Pipeline

A **Pipeline** is a directed acyclic graph (DAG) of **Nodes**. It represents the full journey from raw data to a deployable price.

```python
import haute

pipeline = haute.Pipeline("motor_pricing_v2")
```

### 3.2 Nodes

Nodes are the building blocks. Each node is a decorated Python function with defined inputs, outputs, and logic. There are 17 node types grouped by function:

#### Entry / Exit (singleton — max 1 per pipeline)

| Node Type | Enum | Purpose |
|---|---|---|
| **Quote Input** | `apiInput` | Live API input for deployment; defines JSON schema |
| **Quote Output** | `output` | Final price / prediction assembly; selects output columns |

#### Data I/O

| Node Type | Enum | Purpose |
|---|---|---|
| **Data Source** | `dataSource` | Read from local CSV/Parquet or Databricks Unity Catalog table |
| **Data Sink** | `dataSink` | Write results to parquet, CSV, or directory |
| **External File** | `externalFile` | Load pickle, JSON, or joblib file as a DataFrame |
| **Constant** | `constant` | Named constant values injected as a 1-row DataFrame |
| **Source Switch** | `liveSwitch` | Route between live API input and batch data by scenario |

#### Data Transform

| Node Type | Enum | Purpose |
|---|---|---|
| **Polars** | `polars` | Polars transform / feature engineering (user code) |
| **Banding** | `banding` | Group numerical or categorical values into bands |
| **Scenario Expander** | `scenarioExpander` | Cross-join rows with scenario values for what-if analysis |
| **Rating Step** | `ratingStep` | Rating table lookup, factor application, cap/floor |

#### ML & Optimisation

| Node Type | Enum | Purpose |
|---|---|---|
| **Model Training** | `modelling` | Train a CatBoost or GLM model (produces MLflow artifact) |
| **Model Scoring** | `modelScore` | Score records using an MLflow-registered model |
| **Optimisation** | `optimiser` | Price optimisation via Lagrangian solver (online or ratebook) |
| **Apply Optimisation** | `optimiserApply` | Apply saved optimisation results (lambdas + factor tables) |

#### Structure / Composition

| Node Type | Enum | Purpose |
|---|---|---|
| **Submodel** | `submodel` | Reusable sub-pipeline (drill-down in GUI, flattened at execution) |
| **Submodel Port** | `submodelPort` | Input/output port for submodel boundary wiring |

All 17 types are defined in `_types.py` as a `NodeType(StrEnum)` enum, with per-type `TypedDict` config schemas, registered builder functions in `_builders.py`, and code generators in `codegen.py`.

### 3.3 External Config Files

Node configuration is externalized to JSON sidecar files under `config/<type>/<name>.json`. The decorator references the config file:

```python
@pipeline.banding(config="config/banding/vehicle_age_band.json")
def vehicle_age_band(df):
    ...
```

14 of the 17 node types store external config (all except `polars`, `submodel`, and `submodelPort`). The folder-per-type mapping is defined in `_config_io.py`:

| Node Type | Config Folder |
|---|---|
| Quote Input | `config/quote_input/` |
| Data Source | `config/data_source/` |
| Source Switch | `config/source_switch/` |
| Model Scoring | `config/model_scoring/` |
| Banding | `config/banding/` |
| Rating Step | `config/rating_step/` |
| Quote Output | `config/quote_response/` |
| Data Sink | `config/data_sink/` |
| External File | `config/load_file/` |
| Model Training | `config/model_training/` |
| Optimisation | `config/optimisation/` |
| Apply Optimisation | `config/apply_optimisation/` |
| Scenario Expander | `config/expander/` |
| Constant | `config/constant/` |

Polars nodes store all logic in their Python function body (no config file). The `code` key always lives in the `.py` function body, never in JSON.

For node types with JSON sidecar config, the parser requires an explicit `config=` reference and fails loudly if the referenced file is missing or invalid.

### 3.4 Submodels (Composable Sub-Pipelines)

A group of nodes can be extracted into a **submodel** — a separate `modules/<name>.py` file that the parent pipeline references via `pipeline.submodel("modules/<name>.py")`. In the GUI, a submodel appears as a single collapsible node with drill-down. At execution time, submodels are flattened into the parent graph.

```python
# main.py
import haute
pipeline = haute.Pipeline("motor_pricing")

@pipeline.data_source(path="data/claims.parquet")
def load_claims(): ...

pipeline.submodel("modules/model_scoring.py")
pipeline.connect("load_claims", "feature_engineering")
```

```python
# modules/model_scoring.py
import haute
submodel = haute.Submodel("model_scoring")

@submodel.polars
def feature_engineering(df): ...

@submodel.model_score(model="models/freq.cbm")
def score_frequency(df): ...

submodel.connect("feature_engineering", "score_frequency")
```

### 3.5 Bidirectional Code ↔ GUI

```
┌──────────────────┐         ┌──────────────────┐
│   .py files      │ ──parse──▶  React Flow      │
│   (source of     │         │  graph UI         │
│    truth)        │ ◀──gen───  (editable)       │
└──────────────────┘         └──────────────────┘
```

**Code → GUI:** Haute parses pipeline `.py` files (via AST inspection with regex fallback) and renders them as a React Flow graph.

**GUI → Code:** When a user adds/edits/connects nodes in the GUI, Haute writes valid Python code back to the `.py` files on disk and persists node configs to JSON sidecar files. The generated code is clean, idiomatic, and diffable in git.

### 3.6 Project Structure (what `haute init` creates)

```
my-pricing-project/
├── .github/workflows/      # CI/CD (generated by haute init --ci github)
│   ├── ci.yml              #   PR checks: lint, test, pipeline validation
│   └── deploy.yml          #   Merge-to-main: staging → smoke → production
├── config/                 # Externalized node configs (JSON sidecar files)
│   ├── quote_input/        #   One folder per node type
│   ├── data_source/
│   ├── banding/
│   └── ...
├── data/                   # Local data files (parquet, CSV, JSON/JSONL)
├── models/                 # Serialised model files (CatBoost .cbm, GLM .rsglm)
├── modules/                # Submodel .py files (extracted via GUI or hand-written)
├── utility/                # Reusable Python helper scripts (importable by pipeline)
│   └── __init__.py
├── tests/                  # Tests + JSON test quotes for deploy validation
│   ├── quotes/
│   │   └── example.json
├── main.py                 # Pipeline definition (root-level)
├── main.haute.json         # Sidecar: node positions + scenario state for the GUI
├── haute.toml              # Project, deploy, safety & CI config
├── .env.example            # Template for secrets (DATABRICKS_HOST, etc.)
└── .gitignore
```

Pipeline files live in the **project root** (e.g. `main.py`), not in a subdirectory. This keeps the structure flat and simple for single-pipeline projects. The `haute.toml` `[project]` section points to the pipeline file.

For multi-pipeline projects, users can organise pipelines into a `pipelines/` directory and update `haute.toml` accordingly - but the default is root-level.

CI/CD workflows are optional - pass `--ci github` to generate them, or `--ci none` to skip. The deploy target is selected with `--target` (databricks, container, sagemaker, azure-ml). See `docs/DEPLOY_DESIGN.md` for full details.

---

## 4. Technical Architecture

### 4.1 Distribution

Single Python wheel with bundled React static assets (same model as atelier).

```
pip install haute
# or
uv add haute
```

### 4.2 System Architecture

```
┌─────────────────────────────────────────────────────┐
│  Browser (React + TypeScript + React Flow)           │
│  ┌───────────┬───────────┬────────────────────────┐ │
│  │ Pipeline  │ Node      │ Right Panel             │ │
│  │ Graph     │ Palette   │ (NodePanel / TracePanel │ │
│  │ (React    │           │  / Imports / Utility    │ │
│  │  Flow)    │           │  / Git)                 │ │
│  ├───────────┴───────────┼────────────────────────┤ │
│  │ Bottom Panel                                    │ │
│  │ (DataPreview / ModellingPreview / OptPreview)    │ │
│  └─────────────────────────────────────────────────┘ │
│          REST + WebSocket                            │
└──────────────┬───────────────────────────────────────┘
               │
┌──────────────┴───────────────────────────┐
│  Python Backend (FastAPI + uvicorn)       │
│  ┌─────────────────────────────────────┐ │
│  │  Pipeline Engine                    │ │
│  │  - Parse .py files → graph model    │ │
│  │  - Generate .py files ← graph edits │ │
│  │  - Execute pipelines locally        │ │
│  │  - Validate pipelines               │ │
│  └─────────────────────────────────────┘ │
│  ┌─────────────────────────────────────┐ │
│  │  Model Training                     │ │
│  │  - CatBoost + GLM (RustyStats)      │ │
│  │  - Split strategies, metrics, SHAP  │ │
│  │  - MLflow logging + model cards     │ │
│  └─────────────────────────────────────┘ │
│  ┌─────────────────────────────────────┐ │
│  │  Optimisation Engine                │ │
│  │  - Online (per-quote) solver        │ │
│  │  - Ratebook (factor table) solver   │ │
│  │  - Efficient frontier               │ │
│  │  - MLflow artifact logging          │ │
│  └─────────────────────────────────────┘ │
│  ┌─────────────────────────────────────┐ │
│  │  Deploy Targets                     │ │
│  │  - Container: FastAPI + Docker      │ │
│  │  - Databricks: MLflow + serving     │ │
│  │  - SageMaker / Azure ML (planned)   │ │
│  └─────────────────────────────────────┘ │
│  ┌─────────────────────────────────────┐ │
│  │  File Watcher                       │ │
│  │  - Watch .py files for changes      │ │
│  │  - Push updates to UI via WebSocket │ │
│  └─────────────────────────────────────┘ │
│  ┌─────────────────────────────────────┐ │
│  │  Git Integration                    │ │
│  │  - Branch/commit/push for analysts  │ │
│  │  - Protected-branch guardrails      │ │
│  └─────────────────────────────────────┘ │
│  Storage: local filesystem               │
└──────────────────────────────────────────┘
               │
┌──────────────┴───────────────────────────┐
│  Deploy target (remote)                  │
│  - Container host (ECS, Cloud Run, etc.) │
│  - OR: Databricks Model Serving          │
│  - OR: SageMaker / Azure ML endpoints    │
└──────────────────────────────────────────┘
```

### 4.3 Backend Stack

| Concern | Library | Notes |
|---|---|---|
| **Language** | Python >= 3.11 | |
| **Package manager** | uv | |
| **Build system** | Hatchling | |
| **Web framework** | FastAPI | |
| **ASGI server** | uvicorn | |
| **WebSockets** | websockets | File watcher → UI sync |
| **Validation** | Pydantic v2 | |
| **DataFrames** | Polars | Fast, no pandas dependency |
| **JSON** | orjson | Fast JSON serialisation for API responses and JSON flattening |
| **CLI** | Click | `haute init`, `haute serve`, `haute deploy`, `haute lint`, etc. |
| **Logging** | structlog | Structured JSON logging throughout backend |
| **Env loading** | python-dotenv | `.env` credential loading |
| **Gradient boosting** | CatBoost | Model training + scoring nodes |
| **GLM engine** | RustyStats | GLM fitting via dict API (via `modelling/_rustystats.py`) |
| **Optimisation** | price-contour | Lagrangian price optimisation solver |
| **MLflow client** | mlflow | Model registry, tracking, serving (optional: `haute[databricks]`) |
| **Databricks client** | databricks-sdk | Unity Catalog, Model Serving, jobs (optional: `haute[databricks]`) |
| **AST parsing** | ast (stdlib) | Parse pipeline `.py` files; regex fallback for syntax errors |
| **Code gen** | libcst | Reserved for future surgical write-back (not yet used in read path) |
| **File watching** | watchfiles | Detect .py changes, push to UI |
| **Type checking** | mypy | Dev-time static type analysis (CI) |
| **Linting** | ruff | Fast Python linting + import sorting |

### 4.4 Frontend Stack

| Concern | Library | Notes |
|---|---|---|
| **Language** | TypeScript ~5.9 | |
| **UI framework** | React 19 | |
| **Graph/flow editor** | @xyflow/react (React Flow) | Core visual pipeline editor |
| **Graph layout** | elkjs | ELK auto-layout engine for node positioning |
| **Code editor** | CodeMirror v6 | Python code editing in polars/imports panels (6 packages) |
| **Bundler** | Vite | |
| **Styling** | Tailwind CSS v4 | All custom components, no external UI library |
| **Icons** | lucide-react | |
| **State management** | Zustand | Lightweight, works well with React Flow |

Note: the app is a single-page application with no client-side routing. Submodel navigation uses an internal view-level stack with breadcrumbs.

### 4.5 Backend Module Layout

```
src/haute/
├── __init__.py              # Public API surface
├── cli/                     # Click CLI subpackage
│   ├── __init__.py          #   Entry point & group registration
│   ├── _init_cmd.py         #   `haute init` command
│   ├── _serve.py            #   `haute serve` command
│   ├── _run.py              #   `haute run` command
│   ├── _deploy.py           #   `haute deploy` command
│   ├── _train.py            #   `haute train` command
│   ├── _lint.py             #   `haute lint` command
│   ├── _smoke.py            #   `haute smoke` command
│   ├── _status.py           #   `haute status` command
│   ├── _impact.py           #   `haute impact` command
│   └── _helpers.py          #   Shared CLI utilities
│
├── # ── Core pipeline engine ──────────────────────────
├── pipeline.py              # Pipeline DSL: Node, NodeRegistry, Pipeline, Submodel
├── parser.py                # AST parser: .py → PipelineGraph
├── _parser_helpers.py       #   Shared helper functions for all parser modules
├── _parser_regex.py         #   Regex fallback parser (syntax-error tolerance)
├── _parser_submodels.py     #   Submodel parsing and merging
├── codegen.py               # Code generator: PipelineGraph → .py (dispatch table)
├── executor.py              # Graph executor: run/preview/sink (eager + lazy)
├── _execute_lazy.py         #   Shared eager execution core (EagerResult)
├── _builders.py             #   Per-node-type builder registry (_NODE_BUILDERS dispatch)
├── trace.py                 # Single-row trace engine (per-node snapshots + schema diffs)
│
├── # ── Type system & graph utilities ─────────────────
├── _types.py                # NodeType enum, GraphNode, GraphEdge, PipelineGraph, per-type TypedDicts
├── _topo.py                 # Topological sort
├── _flatten.py              # Submodel flattening for execution
├── _submodel_graph.py       #   Submodel placeholder & cross-boundary edge rewiring
├── graph_utils.py           # Re-export facade for graph utilities
│
├── # ── Config & validation ───────────────────────────
├── _config_io.py            # JSON sidecar config read/write (NODE_TYPE_TO_FOLDER mapping)
├── _config_validation.py    #   Validate config keys against TypedDict schemas (warn-only)
├── schemas.py               # Pydantic request/response models for API
│
├── # ── Node logic ────────────────────────────────────
├── _rating.py               # Rating table lookups + banding rule application
├── _model_scorer.py         # MODEL_SCORE node: model loading, feature intersection, prediction
├── _node_builder.py         #   Hook system for intercepting/wrapping node builders
│
├── # ── Model & artifact I/O ──────────────────────────
├── _mlflow_io.py            # ScoringModel wrapper + thread-safe LRU model cache (16 entries)
├── _mlflow_utils.py         #   Shared MLflow helpers (version resolution, safe search)
├── _optimiser_io.py         #   Optimiser artifact loading + mtime-aware LRU cache (8 entries)
├── _io.py                   # File I/O: read_source, load_external_object
├── _json_flatten.py         #   Schema-aware JSON/JSONL flattening + parquet caching
│
├── # ── Caching ───────────────────────────────────────
├── _cache.py                # Graph execution caching
├── _fingerprint_cache.py    #   Generic single-entry cache keyed on graph fingerprint
├── _lru_cache.py            #   Thread-safe bounded LRU cache with optional TTL
│
├── # ── Infrastructure ────────────────────────────────
├── _sandbox.py              # Security: safe_globals, validate_user_code, safe_unpickle
├── _databricks_io.py        # Databricks table fetch + local parquet cache
├── _polars_utils.py         #   Atomic writes, parquet metadata, heap compaction, safe sink
├── _ram_estimate.py         #   RAM/VRAM estimation for training (OOM prevention)
├── _logging.py              # structlog configuration
├── _git.py                  # Git operations layer with guardrails (branch, commit, push, revert)
├── discovery.py             # Pipeline file discovery
├── _scaffold.py             # Project scaffolding templates (haute init)
│
├── # ── Web server ────────────────────────────────────
├── server.py                # FastAPI app factory, middleware, WebSocket, file watcher
├── routes/                  # FastAPI route modules
│   ├── __init__.py
│   ├── pipeline.py          #   Pipeline CRUD, run, preview, trace, sink, save
│   ├── databricks.py        #   Unity Catalog browsing, data fetching
│   ├── files.py             #   File browsing, schema inspection
│   ├── submodel.py          #   Submodel create, get, dissolve
│   ├── modelling.py         #   Train, export, MLflow log endpoints
│   ├── optimiser.py         #   Optimisation solve, apply, frontier, save, MLflow log
│   ├── mlflow.py            #   MLflow discovery (experiments, runs, models, versions)
│   ├── git.py               #   Git panel endpoints (branch, commit, push, revert, archive)
│   ├── utility.py           #   Utility script CRUD (list, read, create, update, delete)
│   ├── json_cache.py        #   JSON/JSONL → parquet cache (build, cancel, progress, status)
│   ├── _helpers.py          #   Shared route helpers (broadcast, discovery, error raising)
│   ├── _job_store.py        #   In-memory job store with TTL eviction (training + optimiser)
│   ├── _save_pipeline.py    #   Save service: validate, codegen, write configs + sidecar
│   ├── _submodel_ops.py     #   Pure graph ops for submodel create/dissolve
│   ├── _train_service.py    #   Training orchestrator: validate → split → fit → evaluate
│   └── _optimiser_service.py #  Optimiser orchestrator: online + ratebook solvers
│
├── modelling/               # Model training & diagnostics subsystem
│   ├── __init__.py          #   Public API: TrainingJob, generate_training_script
│   ├── _algorithms.py       #   Algorithm registry (CatBoost, GLM/RustyStats), fit/predict/SHAP
│   ├── _split.py            #   SplitConfig + split_data (random, temporal, group)
│   ├── _metrics.py          #   Metric registry, double lift, AvE per feature
│   ├── _training_job.py     #   TrainingJob orchestrator: split → fit → evaluate → log
│   ├── _result_types.py     #   Result dataclasses for training outputs
│   ├── _rustystats.py       #   RustyStats GLM integration (dict API bridge)
│   ├── _mlflow_log.py       #   Standalone MLflow experiment logging
│   ├── _model_card.py       #   Self-contained HTML model card generation
│   ├── _charts.py           #   Pure-SVG chart generation (zero external deps)
│   └── _export.py           #   Standalone training script code generation
│
├── deploy/                  # Deployment subsystem
│   ├── _config.py           #   haute.toml + .env resolution
│   ├── _mlflow.py           #   MLflow model packaging + Databricks serving
│   ├── _container.py        #   Container (Docker) deployment
│   ├── _pruner.py           #   Graph pruning (scoring path only)
│   ├── _bundler.py          #   Artifact bundling
│   ├── _schema.py           #   Input/output schema inference
│   ├── _scorer.py           #   Test quote scoring
│   ├── _validators.py       #   Pre-deploy validation
│   ├── _impact.py           #   Impact analysis
│   ├── _model_code.py       #   Generated model serving code
│   └── _utils.py            #   Deploy utilities
│
└── static/                  # Bundled React frontend assets (built by Vite)
```

Import direction flows **downward**: `cli` / `server` / `routes` → `executor` / `codegen` / `deploy` → `parser` → `_types` / `_topo` / `graph_utils`. Circular imports are not permitted (see `docs/COMMIT_STANDARDS.md` §23).

### 4.6 Frontend Layout

```
frontend/src/
├── App.tsx                  # Root component: React Flow canvas + panel layout
├── main.tsx                 # Entry point
├── index.css                # Tailwind imports + global styles
│
├── api/                     # Backend communication
│   ├── client.ts            #   Typed fetch wrapper (all REST endpoints)
│   └── types.ts             #   Shared API DTOs (PipelineGraph, responses, etc.)
│
├── stores/                  # Zustand state management
│   ├── useNodeResultsStore.ts  # Preview data, solve/train progress & results, column cache
│   ├── useUIStore.ts           # Panel toggles, modals, sync status, dirty flag, hover state
│   ├── useToastStore.ts        # Toast notifications
│   └── useSettingsStore.ts     # Row limit, section collapse, MLflow status, scenario, file cache
│
├── hooks/                   # React hooks
│   ├── usePipelineAPI.ts       # Load, preview, save pipelines; node/edge/preamble sync
│   ├── useWebSocketSync.ts     # File watcher sync via WebSocket (exponential backoff)
│   ├── useUndoRedo.ts          # Undo/redo for nodes + edges (MAX_HISTORY=100)
│   ├── useNodeHandlers.ts      # Add, delete, duplicate, rename, createInstance, autoLayout
│   ├── useEdgeHandlers.ts      # Connect, disconnect, selection, drag-drop
│   ├── useKeyboardShortcuts.ts # Ctrl+S, Ctrl+Z/Y, Delete, Ctrl+A, etc.
│   ├── useTracing.ts           # Cell click → trace request → graph highlighting
│   ├── useSubmodelNavigation.ts # Submodel drill-down with view stack
│   ├── useDataInputColumns.ts  # Column cache for data input nodes
│   ├── useMlflowBrowser.ts    # Lazy-load MLflow experiments/runs/models/versions
│   ├── useSchemaFetch.ts       # File schema fetching (shared by DataSource + ApiInput)
│   ├── useJobPolling.ts        # Generic job polling with exponential backoff
│   ├── useBackgroundJobs.ts    # Solve + train job orchestration
│   ├── useConstraintHandlers.ts # Optimiser constraint CRUD
│   ├── useDragResize.ts        # Panel drag-to-resize
│   └── useClickOutside.ts      # Dropdown dismiss handler
│
├── panels/                  # Right sidebar + bottom panels
│   ├── PanelShell.tsx          # Layout wrapper (sizing, z-index priorities)
│   ├── PanelHeader.tsx         # Reusable header (title, icon, subtitle, close)
│   ├── NodePanel.tsx           # Node config editor dispatcher → type-specific editors
│   ├── TracePanel.tsx          # Execution trace sidebar (step cards, schema diffs)
│   ├── DataPreview.tsx         # Bottom panel: node output table with column info
│   ├── ImportsPanel.tsx        # Pipeline imports / preamble editor
│   ├── UtilityPanel.tsx        # Utility scripts CRUD
│   ├── GitPanel.tsx            # Simplified git workflow for analysts
│   ├── NodePalette.tsx         # Left sidebar: drag-to-canvas node type picker
│   ├── ModellingConfig.tsx     # Model training configuration
│   ├── ModellingPreview.tsx    # Training results (7 tabs: Summary, Loss, Lift, etc.)
│   ├── OptimiserConfig.tsx     # Optimisation solver configuration
│   ├── OptimiserPreview.tsx    # Solver results (Summary, Frontier, Convergence)
│   ├── editors/                # Per-node-type config editors
│   │   ├── TransformEditor.tsx
│   │   ├── DataSourceEditor.tsx
│   │   ├── ModelScoreEditor.tsx
│   │   ├── BandingEditor.tsx
│   │   ├── RatingStepEditor.tsx
│   │   ├── ApiInputEditor.tsx
│   │   ├── OutputEditor.tsx
│   │   ├── ConstantEditor.tsx
│   │   ├── SinkEditor.tsx
│   │   ├── ScenarioExpanderEditor.tsx
│   │   ├── OptimiserApplyEditor.tsx
│   │   ├── LiveSwitchEditor.tsx
│   │   ├── ExternalFileEditor.tsx
│   │   ├── SubmodelEditor.tsx
│   │   ├── MlflowModelPicker.tsx  # Shared experiment/run/model/version picker
│   │   ├── _shared.tsx            # CodeEditor, FileBrowser, SchemaPreview, style constants
│   │   └── _DatabricksSelector.tsx # Warehouse/catalog/schema/table pickers
│   ├── modelling/              # Training sub-config panels + result tabs
│   └── optimiser/              # Optimiser sub-config panels
│
├── components/              # Shared UI components
│   ├── Toolbar.tsx             # Top bar: undo/redo, zoom, save, panel toggles, WS status
│   ├── Toast.tsx               # Toast notifications (auto-dismiss)
│   ├── ModalShell.tsx          # Modal backdrop with Escape/click-outside
│   ├── ContextMenu.tsx         # Right-click: delete, duplicate, rename, create-instance
│   ├── BreadcrumbBar.tsx       # Submodel hierarchy navigation
│   ├── BreakdownDropdown.tsx   # Timing/memory breakdown by node
│   ├── ColumnTable.tsx         # Reusable column display table
│   ├── ToggleButtonGroup.tsx   # Multi-option toggle
│   ├── KeyboardShortcuts.tsx   # Shortcut reference modal
│   ├── NodeSearch.tsx          # Node search/filter
│   ├── CacheFetchButton.tsx    # JSON cache build trigger
│   ├── SubmodelDialog.tsx      # Create-submodel modal
│   ├── RenameDialog.tsx        # Rename-node modal
│   ├── ErrorBoundary.tsx       # React error boundary
│   ├── PolarsIcon.tsx          # Custom Polars icon
│   └── form/                   # Form primitives (ConfigInput, ConfigSelect, ConfigCheckbox, EditorLabel)
│
├── nodes/                   # Custom React Flow node components
│   ├── PipelineNode.tsx        # Standard node (zoom-responsive: full/medium/compact)
│   ├── SubmodelNode.tsx        # Submodel node (child count, ports, drill-down)
│   └── SubmodelPortNode.tsx    # Submodel input/output port
│
├── utils/                   # Helper functions
│   ├── nodeTypes.ts            # NODE_TYPE_META: icons, colours, labels, defaults for all 17 types
│   ├── buildGraph.ts           # API response → React Flow nodes/edges
│   ├── graphHelpers.ts         # Graph manipulation helpers
│   ├── layout.ts               # ELK auto-layout integration
│   ├── formatValue.ts          # Value formatting for trace/preview
│   ├── formatBytes.ts          # Byte size formatting
│   ├── formatTime.ts           # Duration formatting
│   ├── sanitizeName.ts         # Node name sanitisation
│   ├── hoverHandlers.ts        # Edge/node hover dimming
│   ├── color.ts                # Colour utilities
│   ├── dtypeColors.ts          # Polars dtype → colour mapping
│   ├── banding.ts              # Banding rule helpers
│   ├── configField.ts          # Config field helpers
│   └── makePreviewData.ts      # Preview data transformation
│
└── types/                   # TypeScript type definitions
    ├── node.ts                 # HauteNodeData, ColumnInfo
    ├── trace.ts                # TraceSchemaDiff, TraceStep, TraceResult
    └── banding.ts              # ContinuousRule, CategoricalRule, BandingFactor
```

**Panel priority** (right sidebar, one at a time): GitPanel > UtilityPanel > ImportsPanel > TracePanel > NodePanel. Clicking a node or the graph background closes non-node panels.

**Bottom panel** swaps between: DataPreview (default), ModellingPreview (when training node selected), OptimiserPreview (when optimiser node selected).

### 4.7 Key Technical Decisions

- **AST for parsing, libcst reserved for surgical write-back**: The read path (`parser.py`) uses Python's `ast` module with a regex fallback for syntax errors. `libcst` is reserved for Phase 2 surgical write-back - editing individual nodes in a `.py` file without regenerating the whole file. The current write path (`codegen.py`) regenerates the full file, which is fine while the GUI is the only write source.
- **External config files**: Node configuration lives in JSON sidecar files (`config/<type>/<name>.json`) rather than inline in decorator kwargs. This keeps `.py` files clean and diffable, makes config editable by non-programmers, and separates concerns between code logic and node parameters.
- **Dispatch table pattern**: Both `executor.py` (via `_builders.py`) and `codegen.py` use a `NodeType → handler` dispatch table. Adding a new node type means registering one builder function and one codegen function.
- **ScoringModel wrapper**: `_mlflow_io.py` provides a `ScoringModel` class that wraps CatBoost native, RustyStats GLM, and MLflow pyfunc models behind a uniform `predict()` interface. Thread-safe LRU cache (16 entries) avoids redundant model loading.
- **Polars over pandas**: Faster, more memory efficient, better API.
- **File watcher for sync**: When a user edits `.py` files in their IDE, the file watcher detects changes and pushes updated graph state to the React Flow UI via WebSocket. This enables true bidirectional editing.
- **Service layer pattern**: Route handlers are thin HTTP adapters; orchestration logic lives in service classes (`SavePipelineService`, `TrainService`, `OptimiserSolveService`). This enables testing without HTTP and reuse from CLI.
- **Job store for background work**: Training and optimisation run in background threads tracked by `JobStore` (in-memory, 24h TTL eviction). Fine for a single-server dev tool.

---

## 5. Scoring & Deployment

### 5.0 Core Mental Model

A pipeline is a small data transformation: **input → Polars DataFrame → pipeline nodes → output DataFrame**.

The same pipeline code runs in both modes - only the input differs:

| Mode | Input | DataFrame shape | Use case |
|---|---|---|---|
| **Live (API)** | Single JSON/XML request | 1 row | Real-time quoting via Databricks Model Serving |
| **Batch (offline)** | Polars DataFrame / Parquet / Databricks table | N rows | Batch scoring, optimisation, back-testing |

In live mode, the incoming request is parsed into a 1-row Polars DataFrame, passed through the pipeline, and the result is returned as JSON. In batch mode, the same pipeline processes an N-row DataFrame.

### 5.1 Local Scoring

```python
import haute
import polars as pl

pipeline = haute.Pipeline.load("motor.py")

# Batch - N rows
quotes = pl.read_parquet("data/quotes_2025.parquet")
results = pipeline.score(quotes)  # Polars DataFrame in, Polars DataFrame out

# Single quote - same code, 1 row
single = pl.DataFrame({"vehicle_age": [3], "postcode": ["SW1A"], "driver_age": [35]})
price = pipeline.score(single)
```

### 5.2 Live Scoring (API)

When deployed, the pipeline receives a JSON request, converts it to a 1-row Polars DataFrame internally, runs the pipeline, and returns JSON:

```
POST /quote
{"vehicle_age": 3, "postcode": "SW1A", "driver_age": 35}
-> {
  "rows": [{"technical_price": 412.50}],
  "row_count": 1,
  "returned_rows": 1,
  "truncated": false,
  "limit": 1000
}
```

### 5.3 Deploy Targets

Haute deploys a **pricing API**, not an ML model. The default target packages the pipeline as a **FastAPI app in a Docker container**. Teams on Databricks can deploy via MLflow instead.

```bash
# Deploy to production (reads config from haute.toml + credentials from .env)
haute deploy

# Deploy to a staging endpoint
haute deploy --endpoint-suffix "-staging"

# Validate without deploying (parse, prune, score test quotes)
haute deploy --dry-run
```

This:
1. Parses the pipeline and prunes to the scoring path
2. Validates structure and scores test quotes locally
3. Packages the pipeline (container: FastAPI + Docker image; databricks: MLflow model)
4. Pushes to the configured target
5. Returns the endpoint URL

| Target | `[deploy].target` | What it produces | Status |
|---|---|---|---|
| **Container** | `"container"` | FastAPI Docker image, `/quote` + `/health` | Next |
| **Databricks** | `"databricks"` | MLflow model → serving endpoint | Implemented |
| **SageMaker** | `"sagemaker"` | Container → ECR → SageMaker endpoint | Planned |
| **Azure ML** | `"azure-ml"` | Container → ACR → Azure ML endpoint | Planned |

### 5.4 Deployment Configuration

All config lives in `haute.toml` (no YAML, no separate deployment files):

```toml
[project]
name = "motor-pricing"
pipeline = "main.py"

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

[safety]
impact_dataset = "data/portfolio_sample.parquet"

[safety.approval]
min_approvers = 2

[ci]
provider = "github"

[ci.staging]
endpoint_suffix = "-staging"
```

Credentials are read from `.env` (loaded automatically, gitignored). TOML values can be overridden by `HAUTE_` prefixed environment variables (e.g. `HAUTE_MODEL_NAME`, `HAUTE_TARGET`), which are in turn overridden by CLI flags.

---

## 6. Engineering Practices (Baked In)

### 6.1 What `haute init` gives you for free

| Practice | Implementation |
|---|---|
| **Version control** | Git-native - everything is `.py` files, diffable and reviewable |
| **CI/CD** | Pre-configured GitHub Actions: lint → test → deploy on merge to main |
| **Linting** | `ruff` for Python, `haute lint` for pipeline-specific validation |
| **Type checking** | `mypy` (strict mode) |
| **Testing** | `pytest` stubs auto-generated for each pipeline; `haute test` runs them |
| **Pre-commit hooks** | ruff, mypy, haute lint - runs on every commit |
| **Dependency management** | `uv` with lockfile |

### 6.2 `haute lint` - Pipeline-specific checks

- Pipeline parses without syntax errors
- At least one node exists
- No disconnected (orphan) nodes in the graph
- No edges referencing non-existent nodes
- No nodes with parse errors

Runs as `haute lint` (auto-discovers pipeline from `haute.toml`) or `haute lint path/to/pipeline.py`.

### 6.3 `haute test` - Auto-generated tests

```python
# tests/test_motor.py (auto-generated, user can extend)
from haute.testing import PipelineTestCase

class TestMotorPipeline(PipelineTestCase):
    pipeline = "motor.py"

    def test_pipeline_loads(self):
        """Pipeline parses and all nodes resolve."""
        self.assert_pipeline_valid()

    def test_score_sample(self):
        """Score a sample record and check output shape."""
        result = self.score(self.sample_record())
        self.assert_has_output("technical_price", result)
```

---

## 7. CLI Commands

| Command | Description |
|---|---|
| `haute init` | Scaffold a new pricing project (`--target`, `--ci` flags) |
| `haute serve` | Start the browser UI (FastAPI + React Flow) |
| `haute run [pipeline]` | Execute a pipeline locally (batch scoring) |
| `haute deploy` | Deploy to configured target (`--endpoint-suffix`, `--dry-run`) |
| `haute train` | Train a model from a pipeline's modelling node |
| `haute lint [pipeline]` | Validate pipeline structure (parse, orphans, edges) |
| `haute smoke` | Score test quotes against a live endpoint (`--endpoint-suffix`) |
| `haute status [model]` | Show deployed model version and status (`--version-only`) |
| `haute impact` | Compare live vs staging pricing across a sample dataset (`--endpoint-suffix`, `--sample`) |

---

## 8. Design Decisions (Finalised)

### 8.1 Code ↔ GUI granularity → Hybrid
Coarse pipeline stages by default, expandable to fine-grained operations. Users see a clean high-level graph and can drill into any node to see/edit individual operations.

### 8.2 Code generation strategy → Decorators + External Config
Each decorated function (`@pipeline.banding(config="config/banding/age.json")`) corresponds to a node in the GUI. The decorator references a JSON config sidecar file for node parameters (paths, model references, banding rules, rating tables, etc.), while the function body contains user code. Low floor, high ceiling.

```python
import haute
import polars as pl

pipeline = haute.Pipeline("motor_pricing")

@pipeline.data_source(config="config/data_source/claims.json")
def load_claims():
    """Source node — reads claims data."""
    return pl.scan_parquet("data/claims.parquet")

@pipeline.polars
def clean_vehicle(df: pl.LazyFrame) -> pl.LazyFrame:
    """Polars node — standardise vehicle codes."""
    return df.with_columns(...)

@pipeline.model_score(config="config/model_scoring/freq.json")
def score_frequency(df: pl.LazyFrame) -> pl.LazyFrame:
    """Model scoring node — CatBoost frequency model."""
    ...

pipeline.connect("load_claims", "clean_vehicle")
pipeline.connect("clean_vehicle", "score_frequency")
```

### 8.3 Rating tables → All in Databricks
All rating tables (even small ones) live in Databricks Unity Catalog. Referenced by `catalog.schema.table` URI. Viewable/editable in the GUI. This keeps a single source of truth and avoids CSV sprawl in git.

### 8.4 Shared preprocessing logic
Preprocessing transforms (e.g., categorical grouping) are defined once in `utility/` and reused across both modelling pipelines (for training) and rating pipelines (for deployment). The rating pipeline is the deployable unit; modelling pipelines are a separate workflow that produces MLflow-registered models.

### 8.5 PyPI package name → `haute`
Short, available, memorable. CLI command: `haute`. Import: `import haute`.

### 8.6 Git workflow for analysts
The built-in Git panel provides a simplified branch-commit-push workflow for pricing analysts who don't use the terminal. Branch naming is enforced (`pricing/<user>/<description>`), protected branches (main/master/develop/production) prevent accidental overwrites, and revert creates backup tags before resetting. No `gh`/`glab` dependency — PR creation uses comparison URLs.

---

## 9. Killer Features - What Gets Attention

The features below are ordered by impact. Features 1–2 are what get **attention** (demos, HN, LinkedIn). Features 3–4 are what get **adoption** (teams choosing haute over alternatives). Features 5–6 are what make engineering teams **insist** on using it.

### 9.1 Live Code ↔ GUI Sync (the headline feature)

Edit a `.py` file in VS Code → the React Flow graph updates in real-time. Edit the graph → the `.py` file updates on disk. No other tool does this well. This is the feature that sells itself in a 30-second demo.

Implementation: Python's `ast` module parses pipeline `.py` files into a `PipelineGraph`; a regex fallback handles files with syntax errors. File watcher (`watchfiles`) detects `.py` changes and pushes updated graph state to the React Flow UI via WebSocket. GUI edits regenerate clean, idiomatic, diffable Python code back to disk via `codegen.py`.

The hard part isn't parsing - it's **conflict resolution** when both sides edit simultaneously. Treat the `.py` file as the single source of truth; GUI edits write to disk immediately, and the file watcher debounces to avoid loops.

### 9.2 What-If Sensitivity Mode (the "wow" demo feature)

Pin a single input row and show sliders for each input variable. Drag "driver age" from 25 to 45 and watch the price update **live** through every node in the graph. Each node shows its intermediate output updating in real-time.

This is what pricing analysts do all day in spreadsheets. Making it visual and instant in the pipeline graph is a **demo moment** - the kind of thing people share.

Implementation: Since the pipeline uses Polars lazy evaluation, scoring a single row is near-instant. The frontend sends a modified 1-row DataFrame on each slider change, the backend runs the pipeline and returns per-node results. Debounce slider input to ~100ms.

### 9.3 Execution Trace / Data Lineage (the regulatory feature)

Click any cell in the output preview table → highlight the path through every node that produced it, showing intermediate values at each step.

Example: click a price of £412.50 and see:
```
base rate £300 → area factor ×1.2 → NCD ×0.85 → frequency load ×1.35 → £412.50
```
traced visually through the graph with each node lit up.

This is **regulatory gold** for insurance (Solvency II, IFRS 17 require explainability). No open-source pricing tool does this. Achievable because we already have per-node lazy execution and preview results.

#### Implementation status

**Phase A (done)** - Foundation in `src/haute/trace.py`:
- `execute_trace()` runs the pipeline on a single row and captures per-node input/output snapshots
- `SchemaDiff` classifies columns at each node as `added`, `removed`, `modified`, or `passed_through`
- `TraceStep` / `TraceResult` dataclasses carry the full trace payload
- Column relevance filtering: pass a `column` name to keep only steps whose output contains the column (irrelevant ancestors are removed)
- `POST /api/pipeline/trace` endpoint in `routes/pipeline.py`
- Reuses the shared `_execute_eager_core` from `_execute_lazy.py` - no duplication with executor
- **Performance**: eager single-pass execution (O(n) vs O(n²) lazy re-execution) with single-entry cache keyed on graph fingerprint (`graph_utils.graph_fingerprint`). Source nodes capped at the frontend's `rowLimit` (default 1000) so model-scoring nodes process 1K rows, not the full dataset. First trace click executes the pipeline once; subsequent clicks on any row/column extract from cached DataFrames in <1ms. Same caching approach used by `executor.execute_graph` for preview

**Phase B (done)** - Frontend trace panel in `frontend/src/`:
- Clickable cells in `DataPreview` trigger `POST /api/pipeline/trace`
- Graph highlighting: column-relevant nodes glow with value badges, off-path nodes dim to 30%, trace edges animate
- `TracePanel` sidebar replaces `NodePanel` when trace is active: collapsible step cards, schema diff colours, input→output transitions
- Escape / pane click / node switch clears the trace
- Shared utils (`utils/nodeTypes.ts`, `utils/formatValue.ts`) - no duplication between components

**TODO - future phases** (see `docs/EXECUTION_TRACE_DESIGN.md` for full design):
- [ ] Compare-trace: two rows side-by-side with per-node diff highlighting
- [x] Trace caching: single-entry cache keyed on graph fingerprint (row_index and column don't require re-execution)
- [ ] Human-readable expression generation ("base × area × ncd = £412")
- [ ] `haute trace export` CLI for regulatory PDF/HTML reports
- [x] Row-identity tracking via `row_id_column` on the `deploy_input` source node (e.g. `quote_id`); persists through save/reload, displayed in TracePanel header
- [ ] `JoinInfo` / `AggregationInfo` for cardinality-changing nodes

### 9.4 One-Command Deploy to Databricks (the adoption feature)

```bash
haute deploy
```

Three commands from init to live API:
```bash
haute init
# ... edit pipeline ...
haute deploy
```

This parses the pipeline, prunes to the scoring path, validates against test quotes, packages it as an MLflow pyfunc model, registers it in MLflow Model Registry, creates/updates a Databricks Model Serving endpoint, and returns the URL. Pricing teams spend **weeks** on deployment plumbing - this makes it a one-liner.

Staging deploys use `haute deploy --endpoint-suffix "-staging"`, followed by `haute smoke --endpoint-suffix "-staging"` to validate the live endpoint before promoting to production.

### 9.5 Pipeline Visual Diff (the engineering team feature)

```bash
haute diff HEAD~1
```

Renders a side-by-side graph diff: green nodes = added, red = removed, amber = changed (with inline code diff on hover). Turns every PR review into a visual experience.

Git diffs of pipeline `.py` files are hard to review; a graph diff is immediately legible. This is genuinely novel - no pipeline tool does this.

### 9.6 Natural Language → Polars Code (the adoption accelerator)

Add a "describe what you want" input to the Polars node. Type:
> "join on postcode and calculate average claim cost by area"

→ generates the Polars code automatically.

This massively lowers the barrier for pricing analysts who know SQL/Excel but can't write Polars. Implementation: call an LLM API (OpenAI/Anthropic) with a well-crafted prompt that includes the input schema (already available from lazy scan) and available column names. No need to build an LLM - just a smart prompt.

### 9.7 Rating Table Hot-Reload with Impact Preview

Edit a rating factor table in Databricks → instantly see how it changes output prices across the preview dataset. Show a histogram: "this change increases 12% of quotes by >5%".

This is the #1 workflow in pricing. Making it instant and visual (instead of re-running a notebook) is a genuine competitive advantage over legacy tools.

### 9.8 Schema Validation Between Nodes

Automatically check that the output columns of node A match the expected input of node B. Show a **red edge** if there's a mismatch. Catch "column not found" errors before execution, not during.

Low-hanging fruit with high impact - the schema is already available from Polars lazy scans. This is the kind of polish that makes the tool feel professional.

---

## 10. Phased Roadmap

### Phase 1 - Hello World UI ✅
- [x] Scaffold project (pyproject.toml, frontend/, src/haute/)
- [x] FastAPI backend with pipeline API endpoints
- [x] React Flow frontend rendering a pipeline graph
- [x] `haute serve` CLI command opens browser
- [x] Node palette, drag-and-drop, context menu, keyboard shortcuts
- [x] Data preview panel with resizable split
- [x] Polars lazy execution with configurable row limit
- [x] Dark theme, polished UI
- [x] Static asset bundling (frontend builds into Python wheel)

### Phase 2 - Live Code ↔ GUI Sync ✅
- [x] Parse decorated `.py` files → React Flow graph (AST + regex fallback)
- [x] GUI edits write back to `.py` files (clean, diffable code via full regeneration)
- [x] File watcher pushes changes to UI via WebSocket
- [x] Conflict resolution (file = source of truth, debounced sync with self-write detection)
- [x] Submodels: group nodes into `modules/*.py` files, drill-down view, dissolve back
- [x] External config files: node config externalized to JSON sidecar files (`config/<type>/<name>.json`)
- [ ] Surgical write-back with libcst (edit individual nodes without regenerating the whole file)
- [ ] Schema validation between connected nodes (red edge on mismatch)

### Phase 3 - Deploy & Score ✅
- [x] Package a pipeline as an MLflow pyfunc model
- [x] `haute deploy` registers model and deploys to Databricks Model Serving
- [x] Local scoring engine (`pipeline.score(df)` for dev/testing)
- [x] Graph pruning for deployment (only ancestors of output node)
- [x] Artifact bundling (model files, static data)
- [x] Schema inference (input + output) via dry-run
- [x] Pre-deploy validation (`_validators.py`)
- [x] Test quote scoring
- [x] `haute status` to check deployed model versions
- [x] `haute deploy --endpoint-suffix` for staging deploys
- [x] `haute deploy --dry-run` for validation without deploying
- [x] `haute smoke --endpoint-suffix` to score test quotes against live endpoints
- [x] `haute status --version-only` for scripting (used in deploy workflow for git tagging)
- [x] `[safety]` and `[ci]` config sections in `haute.toml`
- [x] `HAUTE_` environment variable overrides for 12-factor config
- [x] DataSource node with Databricks Unity Catalog support
- [ ] Rating table viewer (reads from Databricks)

### Phase 4 - Killer Demo Features (partially complete)
- [ ] What-if sensitivity mode (slider-driven single-row scoring)
- [x] Execution trace / data lineage - Phase A: single-row trace engine + schema diffs (`src/haute/trace.py`, `POST /api/pipeline/trace`)
- [x] Execution trace / data lineage - Phase B: frontend trace panel (clickable cells, graph highlighting, trace sidebar)
- [ ] Execution trace / data lineage - Phase C: join/agg info, expression gen, compare mode, trace export
- [ ] Rating table hot-reload with impact preview
- [ ] Natural language → Polars code (LLM-powered node assistant)

### Phase 5 - Engineering Practices (partially complete)
- [x] `haute init` scaffolds a new pricing project (`--target`, `--ci` flags)
- [x] GitHub Actions CI template (lint → type check → test → pipeline validation)
- [x] GitHub Actions deploy template (staging → smoke test → impact → production with approval)
- [x] GitLab CI + Azure DevOps pipeline templates
- [x] `haute lint` pipeline-specific validation
- [x] Target-aware scaffolding (databricks, container, sagemaker, azure-ml)
- [x] Pre-commit hooks (ruff auto-format on commit)
- [ ] Auto-generated test stubs + `haute test`
- [ ] Pipeline visual diff (`haute diff HEAD~1`)

### Phase 6 - Model Training & Optimisation ✅
- [x] CatBoost training with split strategies, cross-validation, SHAP
- [x] GLM training via RustyStats integration
- [x] MLflow experiment logging + model cards
- [x] Training results UI (7 tabs: Summary, Loss, Lift, Residuals, Features, AvE, PDP)
- [x] RAM/VRAM estimation before training (OOM prevention)
- [x] Price optimisation: online (per-quote) and ratebook (factor table) solvers
- [x] Efficient frontier computation
- [x] Optimisation results: save to disk + log to MLflow
- [x] Apply saved optimisation results via `optimiserApply` node

### Phase 7 - Collaboration & Workflow ✅
- [x] Composable pipelines / submodels (sub-pipelines as nodes with drill-down)
- [x] Git panel: simplified branch/commit/push for pricing analysts
- [x] Utility scripts panel: reusable Python helpers with auto-import
- [x] Imports panel: pipeline preamble editor
- [x] JSON/JSONL → parquet caching with progress tracking
- [x] MLflow browser: experiment/run/model/version discovery in UI

### Phase 8 - Advanced (planned)
- [ ] Monitoring dashboard (actual vs expected pricing)
- [ ] A/B testing for deployed endpoints
- [ ] Collaborative editing (multi-user)
