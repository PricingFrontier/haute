# Component Specifications

Retroactive spec-driven-development documentation for the haute codebase. Each component
directory contains two documents (see [TEMPLATE.md](TEMPLATE.md) for the required structure
and writing rules):

- **high-level.md** — purpose, observable behaviour, design rationale, failure model.
- **low-level.md** — module map, key types, control flow, edge cases, error handling, testing.

Specs describe the code **as it currently is**. A `> NOTE:` callout is reserved for a
current suspected defect and contains a direct, anchored link to the active package in the
owning component roadmap. Resolved history is folded into present-tense behaviour or removed;
design rationale, accepted trade-offs, and operational caveats use ordinary prose; unresolved
questions become `Decision` roadmap packages. `tests/test_docs_accuracy.py` enforces this
linkage for every component specification.

This inline-link rule is the repository's live-defect inventory decision. A separate reviewed
NOTE registry was rejected because it would duplicate callout text and could drift independently
from both the specification and roadmap. The callouts that predated this rule were classified
during the migration; that point-in-time working record is not retained as a second registry.

Planned changes follow the repository's spec-first workflow. Before tests or production code
change, the owning component spec may add an explicitly labelled
`## Approved change contract` section that states the agreed future behaviour, failure model,
compatibility decision, and acceptance evidence. That section must distinguish the current
behaviour from the approved target and link to its implementation plan. It is not a claim that
the change has shipped. When implementation lands, the release step folds the approved contract
into the normal present-tense sections, removes the temporary section, and verifies that code,
tests, and specification agree. The historical `## Polars backend contracts (<version>)`
heading is legacy temporary-contract debt: no new section may use it, and existing sections follow
the same fold-and-remove rule. A bare repository path in a temporary contract identifies an
intended edit; retirement requires positive symbol-level target or acceptance-test evidence.

`tests/test_docs_accuracy.py` enforces paths, symbols, headings, links and anchors, Testing
references, roadmap evidence, ownership claims, and temporary-contract retirement. Existing
violations are explicit one-line entries in `tests/docs_accuracy_baseline.txt`; a component deletes
its line when reconciled, while any new line is a reviewed ratchet change rather than a silent
fallback.

## Corpus review protocol

A broad semantic review records one coverage state for every file in the checked corpus:

- `full` means the whole file was semantically read;
- `partial` names exact inclusive, non-overlapping line ranges;
- `mechanical` means only a search, parser, or other mechanical check was applied; and
- `unread` makes the absence of review explicit.

The checked inventory uses the current working tree as one snapshot: it reads the exact on-disk
bytes, including staged and unstaged changes present there and untracked in-scope files, and
fingerprints every sorted path and file body together. It never combines counts from `HEAD`, the
index, and the working tree. Component high/low documents, root specification-governance
documents, and roadmap documents are reported separately; Markdown line totals use those same
fingerprinted bytes. Coverage totals are derived from per-file records, and only `full` counts as
a fully read file.

Run the current working-tree inventory with:

```powershell
uv run python scripts/spec_corpus_inventory.py --format json
```

When a semantic review makes coverage claims, pass `--coverage` with that
review's complete TOML ledger. The inventory validates the ledger against the
exact file set instead of relying on a permanently retained point-in-time
review artifact.

Documentation-accuracy tests remain a mechanical consistency gate. A green result proves the
checked paths, links, headings, ownership annotations, and related syntactic contracts; it does
not prove that prose is semantically complete or correct. Broad complexity conclusions likewise
separate current implementation complexity, complexity required by the specified product/design,
and corpus/editorial complexity, and inspect owning component roadmaps before calling work
unowned.

## Approved change contract — prerelease canonical-only formats

The delivered `ROAD-CANON-01` decision is the present-tense contract in this
section.
Haute is prerelease software with no external compatibility obligation. Each boundary therefore
accepts exactly its current canonical Haute representation. Production code must not retain an
obsolete Haute format through conversion, fallback, deprecated aliases, temporary
response keys, old generated-code recognition, warning-only handling, or historical-path cleanup.

The implementation has no branches or diagnostics that recognise historical Haute input. Such
input has no special status and is subject only to the ordinary validation of the current
canonical schema. All maintained call sites use current symbols and old symbols are removed.
Compatibility required for supported Python/platform/browser/dependency versions and explicitly
current public aliases is not historical Haute-format support and remains in scope.

Acceptance is a repository-wide executable inventory: migration-specific tests and fixtures are
deleted, canonical inputs remain green, maintained call-site searches cover removed internals,
and residual scans contain no unexplained executable legacy/backward-compatibility path. Owning
component specs state their canonical formats before the corresponding production changes.

## System overview

A Haute pipeline is a directed acyclic graph of nodes, authored as a decorated Python file
(`main.py`) with node parameters externalised to JSON sidecar files under `config/<type>/`. The
`.py` file is the **source of truth**: the browser GUI (a React Flow canvas) is a live, editable
view of it, not a separate model. Editing a node in the GUI regenerates the `.py` file and its
sidecar configs on disk; editing the `.py` file in an external editor is picked up by a filesystem
watcher and pushed to the GUI over a `/ws/sync` WebSocket. See
[pipeline-config](pipeline-config/high-level.md) for the decorator/sidecar model,
[codegen](codegen/high-level.md) for GUI→code generation, and
[server-api](server-api/high-level.md) for the watcher/WebSocket sync channel and self-write
tracking that keeps the two directions from feeding back into each other.

The system has three tiers:

- **Browser** — a React 19 + TypeScript single-page app. A React Flow canvas is the primary
  surface; a left node palette, a right inspector panel (node config, trace, imports, utility
  scripts, git), and a bottom preview panel (dataframe, modelling, or optimiser results) surround
  it. No client-side routing — submodel drill-down uses an in-memory view stack instead. See
  [frontend-graph-canvas](frontend-graph-canvas/high-level.md),
  [frontend-node-editors](frontend-node-editors/high-level.md), and
  [frontend-shared](frontend-shared/high-level.md).
- **Backend** — a single FastAPI + uvicorn process, packaged as one Python wheel with the built
  frontend bundled as static assets (`pip install haute`; `haute serve` opens the browser). It
  parses/executes/validates the pipeline graph, runs model training and price optimisation,
  bundles and ships deploy artefacts, watches the filesystem for external edits, and provides a
  simplified git workflow for non-git-fluent analysts. Storage is the local filesystem plus,
  optionally, a Databricks Unity Catalog connection for source tables. See
  [server-api](server-api/high-level.md), [execution-engine](execution-engine/high-level.md),
  [modelling](modelling/high-level.md), [optimiser](optimiser/high-level.md),
  [deploy](deploy/high-level.md), and [git-integration](git-integration/high-level.md).
- **Deploy target** — a live scoring endpoint using the same validated scoring contract as the
  editor. Databricks Model Serving is implemented end to end through an MLflow pyfunc model. The
  generic `container` target builds and pushes a FastAPI scoring image but deliberately does not
  choose a hosting platform. Azure Container Apps, AWS ECS, and GCP Cloud Run currently validate,
  build, and push that image, then fail loudly before service update because their SDK adapters are
  not implemented. SageMaker and Azure ML remain scaffold-visible planned targets and are rejected
  by deploy. See [deploy](deploy/high-level.md).

**One authored pipeline, one derived deploy graph.** Authors maintain one pipeline. Deployment
derives a scoring-only graph from it by retaining the selected output's ancestors and collapsing
each `liveSwitch` to its live branch. The same deploy scorer then handles request batches of one
or many records; the request batch size changes, but the authored pipeline is not duplicated into
separate live and batch implementations.

This is node-type convergence, not graph cardinality: a pipeline may contain multiple data inputs
and outputs.

Backend and frontend module-by-module layouts are not duplicated here — each component's
low-level specification has an accurate, current module map; see the component tables below.

## Repository coverage contract

The component specs cover maintained behaviour, not just the importable runtime:

- Every behavioural source under `src/haute/` is named in a backend component's low-level module
  map. Generated `src/haute/static/` assets are covered as a build output rather than one component
  per hashed file; `src/haute/py.typed` is a distribution marker.
- Every production `.ts`, `.tsx`, and `.css` source under `frontend/src/` is named in a frontend
  component's low-level module map. Test-only directories and the vitest setup files
  (`setupTests.ts`, `setupStorageCanary.ts`) belong to the verification system rather than the
  shipped application.
- Packaging, dependency locks, frontend compilation, static-asset bundling, and documentation-site
  publication are owned by [build-and-distribution](build-and-distribution/high-level.md).
- CI workflows, lint/type/coverage gates, repository scripts, mutation testing, browser E2E, and
  the role of the large test/audit corpora are owned by
  [engineering-quality](engineering-quality/high-level.md). Tests that verify a product component
  are also named in that component's `## Testing` section.
- The checked-in `rating/` project is a non-runnable layout/example snapshot, documented by
  [reference-pipeline](reference-pipeline/high-level.md). Missing input data and a referenced
  sidecar remain loud, and no dedicated test suite maintains it as an end-to-end compatibility
  fixture.
- A file may be named in several module maps only when one component is its **primary owner** and
  the others are consumers documenting their direct interaction. An explicit cross-component
  ownership claim in prose is subject to the same discipline even when only the primary component
  module-maps the file. The complete, machine-checked set is
  [ownership.toml](ownership.toml); new shared files and prose ownership claims must be added there
  rather than silently acquiring multiple owners. A proposed ownership claim confined to a
  temporary change contract does not become current ownership until delivery folds it into the
  present-tense specification.

Current delivery intent lives in the flat [engineering roadmap](roadmap/README.md): the index
links to one self-contained, non-normative improvement file per component. Roadmaps do not replace
code, tests, or behaviour specifications. Generated caches, coverage data, untracked local MLflow
state, `site/`, and built static assets are outputs, not additional source components. Tracked root
policy, legal, tooling, and snapshot artifacts are listed explicitly in the appropriate
repository-level module map even when they are non-runtime or non-normative; in particular, the
tracked `mlflow.db` is classified as a historical local MLflow SQLite snapshot rather than
silently grouped with untracked generated state.

## Where is each node type specced?

Specs are organised by owning subsystem, not one-per-node-type. Two blanket rules apply to
every node type: its generated-code template (`_gen_*`) is specced in
[codegen](codegen/high-level.md), and its config-editor UI in
[frontend-node-editors](frontend-node-editors/high-level.md). The table below covers all
19 node types and gives the components owning each node's core behaviour beyond those two.
The canonical set uses `dataInput` for all supported tabular sources and `dataOutput` for
tabular persistence; the removed `dataSource` and `dataSink` types have no compatibility path.

| Node type | Core behaviour specced in |
|---|---|
| `apiInput` | [json-shredding](json-shredding/high-level.md) (v2 input codec and JSON/JSONL/XML→frames shredding); [caching](caching/high-level.md) owns the structured-input cache HTTP route |
| `dataInput` | [io-layer](io-layer/high-level.md) (file, database, lakehouse, Databricks, inline, cache lifecycle, chunking, and optional Polars transform); [databricks-io](databricks-io/high-level.md) for Databricks browsing |
| `dataOutput` | [io-layer](io-layer/high-level.md) (registry-backed writers and explicit write action) |
| `polars` | [execution-engine](execution-engine/high-level.md) (execution), [sandbox-security](sandbox-security/high-level.md) (user-code validation), [expression-parsing](expression-parsing/high-level.md) (trace formulae) |
| `edgeJoin` | [json-shredding](json-shredding/high-level.md) (`_edge_join.py` join core) |
| `modelScore` | [mlflow-model-registry](mlflow-model-registry/high-level.md) (loading/scoring/explainability) |
| `banding` / `ratingStep` | [rating](rating/high-level.md) |
| `output` | [json-shredding](json-shredding/high-level.md) (output mapping and assembly), [server-api](server-api/high-level.md) (editor dry-run route), [deploy](deploy/high-level.md) (served response) |
| `explore` | [explore-eda](explore-eda/high-level.md) (backend), [frontend-preview-explore](frontend-preview-explore/high-level.md) (UI) |
| `externalFile` | [pipeline-config](pipeline-config/high-level.md) (config/builders), [io-layer](io-layer/high-level.md) (reading), [deploy](deploy/high-level.md) (bundling) |
| `liveSwitch` | [execution-engine](execution-engine/high-level.md) (`_node_apply.py`), [deploy](deploy/high-level.md) (live-branch collapse at deploy time) |
| `modelling` | [modelling](modelling/high-level.md) (backend), [frontend-modelling-optimiser-ui](frontend-modelling-optimiser-ui/high-level.md) (UI) |
| `optimiser` | [optimiser](optimiser/high-level.md) (backend), [frontend-modelling-optimiser-ui](frontend-modelling-optimiser-ui/high-level.md) (UI) |
| `scenarioExpander` | [execution-engine](execution-engine/high-level.md) (`_node_apply.py`), [tracing](tracing/high-level.md) (trace enrichment) |
| `optimiserApply` | [execution-engine](execution-engine/high-level.md) (`_node_apply.py`), [optimiser](optimiser/high-level.md) (ratebook apply + explainability) |
| `constant` | [pipeline-config](pipeline-config/high-level.md) (builders/config) |
| `submodel` / `submodelPort` | [submodels](submodels/high-level.md) (backend), [frontend-graph-canvas](frontend-graph-canvas/high-level.md) (canvas nodes + navigation) |

## Backend components (`src/haute/`)

| Component | Covers |
|---|---|
| [execution-engine](execution-engine/high-level.md) | Graph execution: executor, lazy evaluation, topological ordering, admission control, worker isolation, chunking |
| [caching](caching/high-level.md) | Dataframe execution cache, fingerprint/stat-gated/LRU caches, hashing, structured API-input cache routes |
| [pipeline-config](pipeline-config/high-level.md) | Pipeline/graph configuration model, builders, validation, config IO, project scaffolding |
| [codegen](codegen/high-level.md) | Python code generation from pipeline configs, code extraction, AST helpers |
| [expression-parsing](expression-parsing/high-level.md) | Parsing user expressions and pipeline code into structured form |
| [io-layer](io-layer/high-level.md) | Polars IO registry/schema, file ops, path resolution, dataset discovery |
| [databricks-io](databricks-io/high-level.md) | Databricks connectivity and data access routes |
| [json-shredding](json-shredding/high-level.md) | API-input schema, JSON shredding/flattening, JSONPath, output assembly, edge joins |
| [sandbox-security](sandbox-security/high-level.md) | Sandboxed execution of user code, local security, environment guards |
| [git-integration](git-integration/high-level.md) | Git operations backing pipeline versioning and the git API routes |
| [tracing](tracing/high-level.md) | Execution traces: correlation, enrichment, export, waterfall |
| [rating](rating/high-level.md) | Rating steps and banding configuration |
| [submodels](submodels/high-level.md) | Submodel graphs, paths, and submodel API routes |
| [modelling](modelling/high-level.md) | Model training: algorithms, metrics, charts, splits, training jobs, train routes |
| [mlflow-model-registry](mlflow-model-registry/high-level.md) | MLflow IO/utils, model flavors, scoring, explainability, MLflow routes |
| [optimiser](optimiser/high-level.md) | Price optimiser IO, explainability, service and routes |
| [server-api](server-api/high-level.md) | FastAPI app shell, schemas/contracts, core pipeline/file/utility routes, logging, event bus |
| [background-jobs](background-jobs/high-level.md) | Background job store, lifecycle, timeouts |
| [explore-eda](explore-eda/high-level.md) | EDA/explore overview computation and routes |
| [assistant](assistant/high-level.md) | In-app AI assistant: agent loop, LLM provider adapters, graph-authoring tools, chat sessions and streaming routes |
| [deploy](deploy/high-level.md) | Deployment bundling: containerisation, pruning, scoring service, validators |
| [cli](cli/high-level.md) | `haute` CLI commands: init, run, serve, train, deploy, lint, smoke, status, impact |

## Frontend components (`frontend/src/`)

| Component | Covers |
|---|---|
| [frontend-graph-canvas](frontend-graph-canvas/high-level.md) | React Flow pipeline canvas, node components, graph store |
| [frontend-node-editors](frontend-node-editors/high-level.md) | Node palette, node panel, per-node-type config editors, form widgets |
| [frontend-preview-explore](frontend-preview-explore/high-level.md) | Data preview panels and EDA/explore visualisations |
| [frontend-modelling-optimiser-ui](frontend-modelling-optimiser-ui/high-level.md) | Modelling and optimiser configuration/preview panels |
| [frontend-git-ui](frontend-git-ui/high-level.md) | Git panel, commit graph, branch management UI |
| [frontend-trace-ui](frontend-trace-ui/high-level.md) | Trace panel and trace visualisation |
| [frontend-assistant-ui](frontend-assistant-ui/high-level.md) | Assistant chat panel: transcript, streaming consumption, send gates |
| [frontend-shared](frontend-shared/high-level.md) | API client, global stores, hooks, theme, shared widgets |

## Repository, delivery, and reference components

| Component | Covers |
|---|---|
| [build-and-distribution](build-and-distribution/high-level.md) | Python package metadata and Hatch hook, frontend production build, bundled static assets, dependency locks, typed-package marker, and MkDocs publication |
| [engineering-quality](engineering-quality/high-level.md) | CI workflows, pre-commit/lint/type/test gates, critical coverage, mutation/performance suites, browser E2E, developer scripts, and non-normative engineering evidence |
| [reference-pipeline](reference-pipeline/high-level.md) | The checked-in non-runnable `rating/` layout/example snapshot: generated graph code, available sidecars, utilities, and model artefacts, with missing referenced data/sidecar and no dedicated end-to-end tests |
