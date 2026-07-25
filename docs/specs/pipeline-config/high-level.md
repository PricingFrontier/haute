# Pipeline Config — High-Level Specification

## Purpose

A Haute pipeline starts life as a plain Python file: a set of functions decorated with
`@pipeline.<type>` and wired together with `pipeline.connect(...)`. Pipeline-config is the
layer that turns that authoring surface into a structured, validated, typed representation —
both the in-memory graph the frontend GUI and the executor consume, and the on-disk JSON
sidecar files that hold each node's declarative (non-code) settings.

It owns five responsibilities: the decorator/registration API authors write against; building
each node's config dict from decorator keyword arguments, function body, and optional JSON
sidecar; assembling registered nodes and edges into the graph representation (including the
handful of topology invariants that apply regardless of node type); reading and writing the
sidecar JSON files so GUI edits and `.py` source stay in sync; and locating a Haute project's
root directory and pipeline entry file, including scaffolding a new one via `haute init`.

## Scope

**In scope:** the `Pipeline`/`Submodel`/`NodeRegistry` decorator API and its standalone
`run()`/`score()` executor; per-node-type config dict construction and its cross-check against
a user-declared `contract=`; the sidecar JSON path conventions, read/write helpers, and the
write-time key allowlist; the `VALID_KEYS` registry that decides which config keys are
recognised per node type; converting parsed source into `GraphNode`/`GraphEdge` models
(explicit `connect()` edges and implicit parameter-name-matching edges — never invented
ones); the topology-only shape contracts (currently: Explore node in/out-degree); Haute
project-root and pipeline-file discovery; and `haute init` project scaffolding.

**Out of scope:** actual node execution semantics — running a transform, scoring a model,
applying a banding table — belong to [execution-engine](../execution-engine/high-level.md),
even though this component owns the shared registry (`NODE_REGISTRY`) that both config
validation and execution dispatch through. Round-tripping a graph back into `.py` + sidecar
files is [codegen](../codegen/high-level.md)'s job. The AST walk that turns raw `.py` source
into the function/decorator data this component consumes happens upstream, in
[expression-parsing](../expression-parsing/high-level.md). The FastAPI routes that expose
save/load/discovery to the GUI are [server-api](../server-api/high-level.md).

## Behaviour

**Decorator API.** `Pipeline`/`Submodel` (both `NodeRegistry` subclasses) expose one
decorator per node type — `data_source`, `api_input`, `polars`, `banding`, `rating_step`,
`model_score`, `output`, `edge_join`, `live_switch`, `optimiser`, `optimiser_apply`,
`scenario_expander`, `modelling`, `constant`, `data_input`, `data_output`, `data_sink`,
`explore`, `external_file`, `instance` — each a thin wrapper that tags the function with its
`NodeType` and delegates to a shared registration path. A function with zero parameters is
treated as a source node; duplicate function names are rejected the moment a second decorator
tries to register them. `connect(source, target, source_port=, target_port=)` declares an
edge and is chainable; both endpoints must already be registered nodes, and port names, if
given, must be non-empty strings.

In the statically parsed representation, some node types store their declarative config in a
JSON sidecar file (referenced via `config="config/<folder>/<name>.json"`) instead of inline
decorator keywords — the folder convention is fixed per type. Parsing one of these types without
`config=` is rejected with a message naming the exact folder and pointing at `haute init` for a
starter sidecar. Every other type builds its config directly from decorator keywords and, for
several types, from Python code extracted out of the function body. The live `Pipeline` decorator
API does not load or validate a `config=` path; it records the keyword as ordinary node metadata,
and generated function bodies/runtime graph builders own the corresponding executable behaviour.

**Wiring.** In the statically parsed source graph, edges come from exactly two declared
sources: explicit `connect()` calls and function parameter names matching other node names.
Explicit calls take precedence and
implicit inference only fills in what wasn't already covered. Edges are never invented: a
file that declares no wiring parses as a disconnected graph. (A definition-order chain
fallback used to fabricate a linear chain for any multi-node file with zero declared edges;
it was removed because it made deliberately disconnected graphs unrepresentable — deleting
the last edge resurrected it on reparse, a GUI save then materialised the invented edge into
source, and the fabricated chain disagreed with `run()`, which fails loudly on unwired
transforms.)

A static `connect()` whose source or target does not name a root node is retained as unresolved
until referenced submodels have been loaded, because cross-boundary connections legitimately name
child node ids. After that merge opportunity, every endpoint must identify either a root node or
a child of an authored submodel. Any remaining dangling endpoint raises `ParseError` with the
complete edge and handle identity; it is never omitted from an otherwise healthy-looking graph.
The live `Pipeline.connect()` API continues to require both endpoints to be registered
immediately.

The parameter-name rule belongs to static source parsing. A live `Pipeline` object records only
edges added through `connect()`; its `run()`, `score()`, and `to_graph()` methods do not infer
wiring from function signatures. With no registered edges, execution visits nodes in registration
order but still rejects any non-source node when it has no inbound edge.

**Standalone execution.** `Pipeline.run()` and `Pipeline.score(df)` are a self-contained
executor over the live decorator graph (distinct from the full graph executor used for
deployment/preview, which operates on the parsed `GraphNode`/`GraphEdge` representation
instead). They topologically sort the registered nodes and edges, run each node's function
with its wired-in DataFrame(s), and resolve which node's output to return: an explicit
`@pipeline.output` node wins if there is exactly one; otherwise the single node with no
outgoing edge is used; anything more ambiguous than that raises, naming every candidate.
`score(df)` additionally seeds a live input DataFrame into whichever source is marked as the
deploy input (`@pipeline.api_input`, or `api_input=True`) — or, when nothing is marked and
there is exactly one source, into that source — leaving every other source to run its own
load logic. Seeding is port-aware, with a complete seed-shape × port-count matrix: a **bare
DataFrame** is accepted for zero connected ports (a source-only pipeline, where the seeded
source is itself the output) and for exactly one distinct connected port (routed to that
port), and raises for two or more. A **`{frame_label: DataFrame}` dict** is accepted only
when the seeded source has one or more connected ports and the dict's keys match the
distinct connected ports *exactly* — a missing key, an unknown extra key, a dict against a
zero-port source (there are no ports for `{}` or anything else to match; source-only
pipelines take a bare frame), or a bare frame against a multi-port source all raise
`ExecutionError` naming the ports concerned. A frame is never silently fanned out to
multiple ports. Both `run()` and `score()` resolve each edge's frame through the same
port-aware selection the full executor uses (`_pick_source_frame` on
`RegisteredEdge.source_port`), keeping the single-execution-engine invariant.
`@pipeline.instance` registrations are not executable on this live-object surface: the
decorator records an internal instance marker, and `run()`/`score()` raise `ExecutionError`
before calling the node regardless of whether `instanceOf` or `inputMapping` is empty. Static
codegen may resolve an instance into a concrete generated function; the live registry may not
silently treat an unresolved instance as an ordinary Polars node.

**Project & discovery.** A Haute project is a directory containing `haute.toml` that also
sits inside a git repository. Every CLI command resolves "which pipeline file" through the
same four-tier chain: the `[project].pipeline` path from `haute.toml`, then a root-level
`main.py`, then a single unambiguous auto-discovered `.py` file, and finally a hard failure
enumerating what was tried. `haute init` scaffolds a new project: `haute.toml`,
`.env.example`, CI workflow YAML for one of several CI providers, a runnable starter pipeline
and starter test suite, and deploy-target-specific credentials and TOML sections for one of
several supported deploy targets.

**CI/CD generation.** `haute init --ci` supports three providers today — GitHub Actions,
GitLab CI, Azure DevOps — plus `none`, which writes no workflow files at all, crossed against
seven `--target` deploy targets (`databricks`, `container`, `azure-container-apps`, `aws-ecs`,
`gcp-run`, `sagemaker`, `azure-ml`). Whichever provider is chosen, the generated workflow
encodes the same fixed release flow: a validate job (lint, type check, test, pipeline lint,
`haute deploy --dry-run`), an automatic deploy-to-staging job, a smoke test against staging
(scores `tests/quotes/*.json`), an impact-analysis job comparing staging against production,
and a production-deploy job gated behind a provider-specific approval mechanism — GitHub uses
a separate `workflow_dispatch`-triggered workflow (so the split works without GitHub
Team/Enterprise environment protection rules); GitLab uses `when: manual` on the production
job; Azure DevOps runs the production job as a `deployment` under an `environment: production`
block, whose actual approval check is configured on that environment in the Azure DevOps
portal rather than in the generated YAML. Re-running `haute init --force` with a different
`--ci` prunes the previously-chosen provider's workflow files before writing the new ones, so
switching providers doesn't leave orphaned config behind.

## Design rationale

The component leans hard on failing loudly rather than guessing: duplicate node names,
`async def` node bodies, ambiguous pipeline auto-discovery, a `contract=` declaration that
disagrees with what the config implies, and a missing sidecar for a folder-backed node type
all raise a specific, named error rather than silently picking a default. The one deliberate
exception is unrecognised config keys, which are logged at WARNING and otherwise ignored —
both when a node's config is first built and again when it is written back to its sidecar —
so a stale or externally-introduced key is observable without turning every load into a hard
failure.

Config that is genuinely code (pricing logic, transforms) lives in the `.py` function body;
everything else declarative lives in a JSON sidecar. This keeps generated/round-tripped
Python readable — no large JSON blobs embedded as string literals — while letting the GUI
edit the declarative parts without ever touching Python source.

Sidecar writes pass every config dict through an allowlist derived from each node type's
`TypedDict` annotations before serialising, dropping (and logging) anything outside it. This
catches off-spec keys smuggled in by external tooling, a not-yet-hardened code path, or a
frontend bug, without failing the save itself.

Contract validation at parse time deliberately avoids contacting MLflow for model-scoring nodes:
their input side is treated as opaque while the locally configured output column is still
checked. For other node types, a `ConfigError`, `OSError`, `ImportError`, `RuntimeError`, or
`MlflowException` raised while deriving a contract causes that comparison to use an opaque
contract; programmer-shaped errors such as `TypeError`, `AttributeError`, and `KeyError`
propagate. This fallback is broader than infrastructure-only failure because `ConfigError` and
`RuntimeError` are included by the implementation.

Windows-reserved device filenames (`CON`, `NUL`, `COM1`, etc.) are rejected on every
platform, not only when running on Windows, so a project saved on Linux or macOS stays
loadable on a Windows checkout.

`haute.toml`'s `[ci]` table records only *what* — the provider name and the staging endpoint
suffix — never *how*: the actual step-by-step YAML lives in the project's own
`.github/workflows/`, `.gitlab-ci.yml`, or `azure-pipelines.yml`, generated once by
`haute init` and then owned by the team, not regenerated on later `haute` invocations. This
keeps a team's hand-edits to their workflow (extra jobs, custom notifications, org-specific
runners) stable across unrelated `haute.toml` changes. The one deliberate exception is
`haute init --force`, an explicit re-scaffold that prunes the previous provider's workflow
files and rewrites the newly-chosen provider's from the templates — re-running `--force`
is the only way generated CI config changes after the initial `init`.

Sidecars are organised one type-folder per `NodeType` (`config/banding/`, `config/rating_step/`,
etc.) rather than three alternatives considered and rejected: a single flat `config/` directory,
which would scale poorly and give no type information from the path alone (a node's type would
only be recoverable by opening the file); extending `.haute.json` (the separate layout-metadata
file that already exists for GUI positions/viewport) to also carry business config, rejected
because layout and business logic are different concerns whose write paths and failure modes
shouldn't be coupled; and Python-native config files, rejected because JSON is both easier for
non-engineers to hand-edit and is the format the GUI already round-trips internally, whereas a
`.py` config would need its own parser/serialiser pair distinct from the pipeline file's own AST
handling.

## Interactions

- [execution-engine](../execution-engine/high-level.md): consumes the `NodeType`/config dicts
  this component produces. The two components share `NODE_REGISTRY` — a per-node-type
  registration of an executable builder plus a column-contract callback — but this component
  owns the registration mechanics and the parse-time contract cross-check, while
  execution-engine owns what each builder actually computes at runtime.
- [codegen](../codegen/high-level.md): round-trips the `GraphNode`/`GraphEdge` graph this
  component builds back into `.py` source and sidecar JSON, and is the other consumer of
  `NODE_REGISTRY`.
- [expression-parsing](../expression-parsing/high-level.md): performs the AST walk over the
  raw `.py` pipeline file (splitting out function bodies, decorator keyword arguments,
  docstrings) that this component's graph-builder consumes as input.
- [server-api](../server-api/high-level.md): exposes sidecar save/load, project discovery,
  and graph construction to the GUI over HTTP.

## Failure model

Missing, unreadable, or invalid-JSON sidecar files raise a config error naming the path.
Using a folder-backed node type without a `config=` sidecar raises, naming the concrete
config folder and suggesting `haute init`. A JSON sidecar with a repeated key is rejected at
read time rather than silently keeping the last value. Two node functions sharing a name are
rejected, both at live decorator-registration time and again at static parse time (the
function name becomes the graph node id, so a silent collision would drop a node). An
`async def` node body is rejected at parse time. A user-declared `contract=` that disagrees
with the contract derived from the rest of the node's config raises, naming which side
(inputs/outputs) mismatched and what was missing or extra on each. Ambiguous or absent
pipeline-file resolution raises, enumerating every candidate it considered. Not being inside
a Haute project (no `haute.toml`, or no git repository above it) raises. The sole silent path
is unrecognised config keys, which are logged at WARNING and dropped or ignored rather than
failing the surrounding operation — called out above as the deliberate exception to this
component's fail-loud default.

## Polars backend contracts (0.6.0)

Remaining pipeline-configuration improvement work is tracked in the
[pipeline authoring roadmap](../../roadmap/pipeline-authoring.md).

- A configured live-switch mapping is exhaustive for the active scenario set. When a mapping
  exists and the active scenario is absent, execution raises
  `LiveSwitchScenarioError(ExecutionError)` with stable code
  `live_switch_scenario_missing` and stable `switch`, `scenario`, and
  `available_mappings` fields. Synchronous API execution maps it to HTTP 422; background
  execution records `contract_error` with the same code and fields. Default-source fallback
  remains valid only when no mapping is configured.
- A node derives positional DataFrame-input arity once at registration/construction and reuses
  that immutable result. Positional-only and positional-or-keyword parameters are supported;
  defaulted positional parameters are optional; keyword-only parameters are configuration, not
  edges; and `*args` is the sole supported variadic-input form. Unsupported signatures and
  invalid wiring fail loudly with node identity and expected/received arity.

Focused tests cover configured versus unconfigured live-switch fallback, missing-scenario
diagnostics and transport mappings, fixed/optional/variadic arities, cached signature
inspection, and malformed wiring. The 0.6 pre-1.0 release/migration notes must identify the
formerly silent configured-mapping fallback and the new stable error code and fields.
Non-goals: implicit-edge changes, static source-graph inference changes, and changes to
successful mapped live-switch selection or unconfigured fallback.

## Approved change contract — 0.7.0 canonical data I/O node types

Remaining pipeline-configuration improvement work is tracked in the
[pipeline authoring roadmap](../../roadmap/pipeline-authoring.md).

- `Pipeline` and `Submodel` expose `data_input` and `data_output` as the only tabular I/O
  decorators. `data_source` and `data_sink` and their `NodeType` values are deleted. This changes
  the node-type set from 21 to 19; it does not restrict how many input or output nodes a graph may
  contain.
- `dataInput` remains a zero-parameter source function and now carries the strict provider union
  and optional Polars body defined by the I/O spec. `dataOutput` remains a connected terminal
  pass-through during ordinary execution and carries the strict destination union.
- Multiple Data Outputs do not change standalone return selection: `Pipeline.run()`/`score()`
  still return the single explicit `output` node, otherwise the single terminal leaf, and raise
  on ambiguous leaves. Explicit persistence always names one `dataOutput` id, so graph
  cardinality is not resolved by guessing a “primary” writer.
- Only `config/data_input/<name>.json` and `config/data_output/<name>.json` are valid tabular-I/O
  sidecars. The removed folders are neither read nor cleaned up as compatibility inputs.
  Repository-owned pipelines which contain removed decorators are intentionally reset to blank
  pipelines; no parser conversion, alias, migration command, or config salvage is provided.
- Config validation enforces active-branch keys, group/format agreement, safe connection
  references, cache-mode constraints, and the absence of `code` on outputs. Unknown legacy
  decorators/configs fail through the ordinary unknown-node/config error surface.
- The node registry, decorator map, config-folder map, valid-key map, standalone pipeline API,
  scaffold, assistant-facing graph schema, and parse/build round trip contain no removed node
  token after the cutover.

Acceptance includes registry completeness over exactly the retained node enum, valid/invalid
cases for every input/output discriminant, multiple data inputs and outputs in one graph,
sidecar save/load, standalone registration and explicit-output/ambiguous-leaf behaviour,
scaffold parsing, and repository-wide assertions that `dataSource`, `dataSink`, `data_source`,
and `data_sink` are absent outside the 0.7 plan, release note, and historical current-state spec
text.
