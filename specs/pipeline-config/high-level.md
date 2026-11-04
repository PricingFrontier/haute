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
declared config keys that parse and save enforce; the per-node-type recognised config contract; converting parsed source into the graph models
(explicit `connect()` edges and implicit parameter-name-matching edges — never invented
ones); the topology-only shape contracts (currently: Explore node in/out-degree); Haute
project-root and pipeline-file discovery; and `haute init` project scaffolding.

**Out of scope:** actual node execution semantics — running a transform, scoring a model,
applying a banding table — belong to [execution-engine](../execution-engine/high-level.md),
even though configuration validation and execution dispatch consume one shared
node-type registry. Round-tripping a graph back into `.py` + sidecar
files is [codegen](../codegen/high-level.md)'s job. The AST walk that turns raw `.py` source
into the function/decorator data this component consumes happens upstream, in
[expression-parsing](../expression-parsing/high-level.md). The FastAPI routes that expose
save/load/discovery to the GUI are [server-api](../server-api/high-level.md).

## Behaviour

**Decorator API.** `Pipeline`/`Submodel` (both `NodeRegistry` subclasses) expose one
decorator per authorable node type — `api_input`, `workbench_input`, `polars`, `banding`, `rating_step`,
`model_score`, `output`, `workbench_output`, `edge_join`, `live_switch`, `optimiser`, `optimiser_apply`,
`scenario_expander`, `modelling`, `constant`, `data_input`, `data_output`,
`explore`, `external_file` — each a thin wrapper that tags the function with its
`NodeType` and delegates to a shared registration path. Configured request inputs (Quote
and Workbench Inputs), Data Input and Constant nodes are sources whatever their signature; any other function with zero
positional parameters is treated as a source node; duplicate function names are rejected the
moment a second decorator tries to register them. `connect(source, target, source_port=, target_port=)` declares an
edge and is chainable; both endpoints must already be registered nodes, and port names, if
given, must be non-empty strings.

In the statically parsed representation, some node types store their declarative config in a
JSON sidecar file (referenced via `config="config/<folder>/<name>.json"`) instead of inline
decorator keywords — the folder convention is fixed per type. Parsing one of these types without
`config=` is rejected with a message naming the exact folder and pointing at `haute init` for a
starter sidecar. Every other type builds its config directly from decorator keywords and, for
several types, from Python code extracted out of the function body. The live `Pipeline` decorator
API records `config=` and the other keywords as node metadata at import time and reads a sidecar
only when a standalone run executes the node (see Standalone execution).

**Declarations and hooks.** A node with settings is a configured node: an Edge Join (its
settings are its decorator keywords), an Explore node, whose analysis settings do nothing
in a run, or a node of any other type except `polars` that names a `config=` sidecar. Its decorator performs the node's configured work —
loading, scoring, rating, expanding, joining, switching, assembling — when the file runs on its
own, and the function says only what the user added. On a type that accepts code (Data
Input, External File, Rating Step, Model Score, Scenario Expander, Explore), a function whose
first positional parameter is `df` (for an External File, a function with the keyword-only
parameter `obj`) is a **hook**: the decorator calls it with the frame its work produced as
`df` — for an External File, with the node's inputs by name, the loaded object as `obj`, and a
body that binds `df` to the first input itself — and the hook returns the node's result. Any
other function is a **declaration**: its body is `...`, `pass` or a docstring alone, it is
never called, and its positional parameters name the node's inputs. On a type that accepts no
code (API Input, Data Output, Edge Join, Banding, Output, Live Switch, Modelling, Optimiser,
Optimiser Apply, Constant) every function is a declaration, and a parameter named `df` is
just an input. Registration fails loudly for a declaration with a body (the code would never
run; on a type that accepts no code the message says so) and for a hook without one. The
static parser enforces the same shapes. Canvas execution and the deployed scorer give a hook's
code the same names the saved function has: the preamble's names, `df` and the node's other
inputs (an External File's inputs and `obj`), never the first input under its own name. One
difference remains: a Model Score's code on the canvas and when deployed can also use
`model`, which a standalone run does not provide. Node code
leaves its result in `df`; a closing `return <expr>` reads as `df = <expr>`, and an earlier
return is a parse error rather than an assignment that would fall through. The decorator returns a callable that runs the node:
calling a configured node's function directly — `quotes()` in a notebook — performs the same
work, and hands it to the same hook, as a run does, while a transform's decorator returns the
function itself. A sidecar-typed node registered without `config=` has no settings for its
decorator to act on, so the live API treats it as a plain function and a standalone run calls
its body as written; the static parser rejects that form, so it never appears in a saved
pipeline file.

**Binding parameters to inputs.** One rule binds every node's positional parameters to its
inputs, in the parser and in a standalone run alike, so `haute run`, a plain `import main` and
a deployed scorer wire a node the same way. A node's inputs are the connections its
`connect()` calls state, and one more for each parameter named after another node that no
`connect()` joins to it. Each parameter names one input: every positional parameter of a
Polars node (through a declared `input_mapping`), of a declaration and of an External File
(whose keyword-only `obj` is the loaded object); a frame hook (Rating Step, Model Score,
Scenario Expander, Explore) takes as `df` the frame its decorator makes from its first input,
which no parameter names and a `connect()` states, and its other parameters name the other
inputs; a Data Input hook's `df` is the frame it loads and names no input. An input that
arrives on a target port (an Edge Join's `base` and `join`, a Workbench Output's table) binds
by its port. Four shapes are refused, naming the node and the parameter: a parameter that
names no input; an input that no parameter names and the decorator does not take; a
parameter naming an input no `connect()` states while a stated input goes unnamed; and
stated inputs whose `connect()` calls come in another order than the parameters read them.
The parser refuses them as a `ParseError`, so the document loads degraded with the node
unavailable; a standalone run refuses them as an `ExecutionError` before any node runs. A
node that binds receives its frames in the order its parameters read them, in every run.

**JSON files Haute writes.** One printer writes every JSON file Haute commits to a project
(`haute._json_files.render_json`, READ-10): a node's config sidecar, the pipeline's
`.haute.json`, the global constants, the Workbench form folder and a test quote. It writes
UTF-8 with nothing escaped, LF line endings, a two-space indent and a final newline, and
shapes a record by its kind, never by a width limit, so a one-character edit changes one
line: a rating entry, a node position, a Workbench layout box, a schema column, a
show-when condition, an API Input column, a Quote Response mapping row (READ-42), and an
Explore pivot's filter, column, row and value placements, a pivot formula and a chart's
value encoding and series override (READ-43) each sit on one line, as does any list or
mapping of numbers alone
(a node position is whole pixels: the canvas places and drags nodes on them, and the save
rounds an older fractional one half up, as the canvas does, so loading and saving an
unchanged pipeline leaves its `.haute.json` byte for byte the same);
everything else, a sheet's list of fields among it, is one item a line. Each kind of
record keeps its key order, its identifying key first; a map whose order means nothing is
sorted, and the analyst's data keeps the order it was written in. A setting is left out at
the default its loader reads for an absent key, by each schema's own rules. Saving twice
gives identical bytes. The release form a release ships is not a source file and keeps
its own serialisation.

**Strict parsing and editor recovery.** `parse_pipeline_file()` and
`parse_pipeline_source()` are strict canonical entry points: Python syntax, decorator,
configuration, contract, topology, and submodel failures raise and no recovered graph
can reach execution, lint, code generation, Save verification, CI, or deploy. Editor loading
uses a separate recovery entry point and separate models. It first records authored node and
connection skeletons with source spans, then resolves known nodes independently. An expected
node-local configuration or contract failure becomes an unavailable recovery node without
inventing config or mutating its referenced file; an unexpected exception is isolated only
at that recovery boundary and receives a logged incident id. Syntax-invalid source has no
skeleton: the result is `source_only`, carrying the syntax error, never a recovered canvas or a
successful empty canonical graph.

The editor's `.haute.json` read distinguishes absent, valid, corrupt, and unreadable states.
Only a valid sidecar supplies source selection. Corrupt or unreadable content leaves its raw
bytes untouched, uses presentation-only default positions, degrades the document, and blocks
preview because active-source state is untrusted. Recovery revisions hash raw dependency
bytes and explicit missing sentinels rather than requiring a valid `PipelineGraph`.
A module-level statement or a comment that a save would drop (see
[codegen](../codegen/high-level.md)), in the pipeline file or in a registered submodel file
the pipeline's parse reads, is an `unkept_module_statement` diagnostic on its file and lines,
so the document is degraded until the statement moves between the imports and the
constructor or into preserve markers, or the comment moves there or into a node's code. Save refuses a document that is not ready on disk with `409`, naming each diagnostic's
location, message and fix, and leaves the file untouched.
Every mutation of a persisted document, including Save, names the `source_revision` it was
based on and fails closed when the on-disk document has moved; initial creation names no
revision and succeeds only while the target file is absent.

**The request input and the response node are named by their type.** A pipeline holds at
most one request input and one response node, so each has one name: a Quote Input is
`quote_input`, a Quote Response `quote_response`, a Workbench Input `workbench_input` and
a Workbench Output `workbench_output` (`SINGLETON_NODE_NAMES`, READ-46), in a pipeline or a
submodel module. A file whose function for one of them has another name is refused, naming
the name it must have, as is a standalone run of it; a save, a deploy and an assistant
edit refuse a graph that names one otherwise.

**Wiring.** In the statically parsed source graph, edges come from exactly two declared
sources: explicit `connect()` calls and function parameter names matching other node names.
Explicit calls take precedence and
implicit inference only fills in what wasn't already covered. Edges are never invented: a
file that declares no wiring parses as a disconnected graph, keeping deliberately disconnected
graphs representable and in agreement with `run()`, which fails loudly on unwired transforms.

A static `connect()` endpoint names a root node or a registered submodel occurrence. A
submodel's own nodes are reached only through its occurrence's ports (`source_port` and
`target_port`), so an endpoint naming one is dangling like any other unknown name. A dangling
endpoint raises `ParseError` with the complete edge and handle identity; it is never omitted
from an otherwise healthy-looking graph.
The live `Pipeline.connect()` API continues to require both endpoints to be registered
immediately.

The parameter-name rule belongs to graph construction. Live `run()`/`score()` execute only
edges added through `connect()`, while `Pipeline.to_graph()` delegates to the same static
builders as source parsing and therefore reports positional parameter-name edges consistently.
Keyword-only parameters are configuration and never become graph edges.

**Standalone execution.** `Pipeline.run()` and `Pipeline.score(df)` are a self-contained
executor over the live decorator graph (each refusing a pipeline with no nodes, "Pipeline has
no nodes") (distinct from the full graph executor used for
deployment/preview, which operates on the parsed `GraphNode`/`GraphEdge` representation
instead). They bind every node's parameters to its inputs (see Binding parameters to
inputs), topologically sort the registered nodes and edges, run each node with its
wired-in DataFrame(s) — a transform's function directly, a configured node's work through its
decorator and then its hook, if it has one — and resolve which node's output to return.
Like the graph walker, they never resolve a Source Switch's inactive branch: an edge into a
switch from an input its sidecar maps to another source (`score()` runs as `live`) is
inactive, a node whose every outgoing edge is inactive or leads to such a node is not run
(nor seeded), and the switch receives `None` for that input. A switch whose map does not name
the run's source keeps every edge and refuses the run itself. Of the output node, an explicit
response node (`@pipeline.output` or `@pipeline.workbench_output`) wins if there is exactly one;
two raise, naming both; otherwise the single node with no
outgoing edge is used; anything more ambiguous than that raises, naming every candidate.
`score(df)` additionally seeds a live input DataFrame into whichever source is marked as the
deploy input (`@pipeline.api_input` or `@pipeline.workbench_input`, or `api_input=True`) — or, when nothing is marked and
there is exactly one source, into that source — leaving every other source to run its own
load logic. Seeding is port-aware, with a complete seed-shape × port-count matrix: a **bare
DataFrame** is accepted for zero named connected ports (a source-only pipeline, or an
unnamed/`None` edge that consumes the source's whole output) and for exactly one distinct
named port (routed to that port), and raises for two or more. An unnamed edge does not invent
a dictionary key. A **`{frame_label: DataFrame}` dict** is accepted only
when the seeded source has one or more connected ports and the dict's keys match the
distinct connected ports *exactly* — a missing key, an unknown extra key, a dict against a
zero-port source (there are no ports for `{}` or anything else to match; source-only
pipelines take a bare frame), or a bare frame against a multi-port source all raise
`ExecutionError` naming the ports concerned. A frame is never silently fanned out to
multiple ports. Both `run()` and `score()` resolve each edge's frame through the same
port-aware selection the full executor uses, keeping the
single-execution-engine invariant.
A pipeline that registers submodels runs them. The pipeline file imports each definition's
module from the `modules/` folder beside it, `from modules import <name>`, as Python imports
any other module, and registers each occurrence of the `haute.Submodel` it defines,
`pipeline.submodel(<name>.submodel, "<occurrence>")`. `run()` and `score()` import nothing
more: they execute a copy of the pipeline in which every occurrence is replaced by its
registered definition's nodes,
named `<occurrence>.<node>`. An edge into an occurrence's input port feeds every node the port
targets, an edge out of an output port leaves the node the port names, as flattening wires them
for `haute run`, and a submodel's node with several inputs receives them in its parameters'
order: an input named by a parameter (its source's name, or the port's) takes that place, and
the others fill the remaining parameters in order, as a hook's `df` names no input (the
pipeline's own nodes keep their connection order, as without submodels). A definition module
stays in `sys.modules` as any import does, so its string annotations resolve, and a pipeline
whose nodes all live in submodels runs too. Two
occurrences of one definition each run its nodes under their own names.
`pipeline.submodel()` raises `TypeError` for a file path, naming the import form that
replaced it, and for anything else that is not a `haute.Submodel`, such as the imported
module itself, naming `<module>.submodel`; a module that defines no `submodel` fails with
Python's own `AttributeError` when the pipeline file is imported.
There are no node-level instances: `Pipeline` and `Submodel` have no `instance` decorator.
Their live node decorators also refuse the removed `of` and `instanceOf` keywords with
the same remediation. The parser
refuses `@pipeline.instance(...)` and an `of=` keyword on any node decorator with a message
naming the replacement. A node is reused through a one-node submodel, the node in its own
`modules/<name>.py`, registered once for the original and once per
instance (`instance_of="<original>"`), with each instance's inputs bound by
`connect(..., target_port="<input>")`; `run()` and `score()` execute those occurrences as they
do any submodel's.

`run()` takes the source to run under as the keyword `source`, which it requires: a bare
`run()` raises `TypeError` before any node runs, naming the sources the pipeline's
`.haute.json` sidecar lists (`live` alone when there is no sidecar), because no default source
is right for every pipeline. The source is what Source Switches route on, which value each
global constant takes, and (anything but `live`) selects batched model scoring.

**Project & discovery.** A Haute project is a directory containing `haute.toml` that also
sits inside a git repository. Every surface that binds one pipeline, including `run`, `lint`,
and deploy execution, uses the same four-tier chain: the
`[project].pipeline` path from `haute.toml`, then a root-level `main.py`, then a single
unambiguous auto-discovered `.py` file, and finally a hard failure enumerating what was tried.
Malformed TOML and a broken configured path fail before any lower tier is considered.

The GUI's plural listing path applies the same strict configured-path checks
but also returns additional valid root-level pipelines. It lists rather than
chooses among them; the single-pipeline binding policy remains authoritative.

**Project context.** A process resolves its project once, where it starts, and every call
reads it from the one accessor, `current_project()`; nothing reads the working directory or a
process global to find the project. The context (`ProjectContext`) is a small frozen value,
the project root; what the root's `haute.toml` says is read when used, so the pipeline folder
(the folder of the pipeline `[project].pipeline` names, or the root when it names none) and
settings the server can change while it runs, such as the `[mlflow]` table, reach the next
call, a worker's included. The context does not bind one pipeline file, because the editor
serves every pipeline in the project; a command that binds one pipeline resolves it through
the four-tier chain above as before. The one resolver,
`resolve_project`, walks up from where the process starts to the first `haute.toml` inside a
git repository, and anywhere else raises. Who installs the project:

- **The editor server.** `haute serve` resolves the project holding the directory it is run in
  and refuses to start outside one. The application is built for that context, and every
  request, websocket and the lifespan run with it installed, so an app built for project A
  serves A even after the working directory moves to project B. The server process runs in
  the project's root, set once at start: Haute never reads the working directory, but the
  user's pipeline code reads a relative path from it, and this way it names the same file a
  relative path in the project's configuration does.
- **The CLI.** A command works in the shell's working directory, read once when the command
  starts (a caller that installed a project already, such as a test, keeps it), so
  `haute deploy`, `haute init` and the rest read that directory's `haute.toml` as before; a
  command given a pipeline file (`run`, `lint`) runs in the project holding that file
  instead, and `haute serve` resolves the project holding the directory and refuses to start
  outside one.
- **A standalone run.** `Pipeline.run()`, `Pipeline.score()`, a configured node called
  directly and a `TrainingJob` run by an exported training script run in the project already
  installed, if any (a deploy's validated root);
  otherwise in the project holding the pipeline's file, or the file's own folder in no
  project. A pipeline with no defining file (built in an interactive session) runs in the
  project holding the session's working directory.
- **Workers and jobs.** Every worker request carries the project of the call that made it,
  and the worker installs it for that request alone, so a warm worker serving requests from
  different projects never mixes them and no worker changes a process global. A job thread
  runs in the project of the request that started it.
- **The underwriter app, the hosted editor and deployed models.** Each is built for its own
  folder: a release folder, the project folder the hosted bootstrap restored, or the folder
  a scoring container's `app.py` or an MLflow model's manifest ships in. A deploy resolves
  its pipeline inside the project it validated, and validates it, scores its test quotes and
  ships it there, whoever calls the deploy API.

A call that needs a project and finds none installed fails with
`NoProjectContextError` naming the fix. A relative path in project configuration names a file
under the project root, never one under the working directory.

`haute init` scaffolds a new project, asking at a terminal what its flags leave out
([cli](../cli/high-level.md)): `haute.toml`,
`.env.example`, CI workflow YAML for one of several CI providers, a valid blank pipeline and
starter parse test, and, for an automated project, deploy-target-specific credentials and TOML
sections for one of several supported deploy targets, or, for a workbench project, which hosts
no scoring API, the `[workbench]` table with the app's host and the quote store's database,
`[deploy.container]` for the app's image, the host's credentials, `connections.toml` and a
blank form folder, the pipeline folder's `workbench/` ([workbench](../workbench/high-level.md)). The blank pipeline declares its `Pipeline` object but no nodes. Init
removes a root `main.py`, creates no `prompts/` directory, and creates no node sidecars.

**Project layout.** The pipeline folder, the folder holding `[project].pipeline` (`rating/`
in a new project), is versioned as a whole: Commit takes every file in it into version
history ([git-integration](../git-integration/high-level.md)). Its `files/` folder holds the
files the pipeline reads that are not code: `files/data/` for the tables and data the
pipeline ships with, `files/models/` for model files with their feature contracts and
optimiser results, and `files/params/` for parameter files. `haute init` creates all three.
Outside the pipeline folder, the project
root's `data/` holds local data and `outputs/` holds what runs write. Neither is versioned,
and moving to another version deletes `outputs/`. A bare filename given to the Export pane's
"Save model to file" or to an optimiser result's Publish lands in `files/models/`. A project
whose pipeline sits at its root has no pipeline folder, and a bare filename there lands in
the root's `models/`. A file larger than 100 MiB, GitHub's per-file limit, cannot be
versioned, so the pipeline folder takes none.

**CI/CD generation.** `haute init --ci` supports three providers today — GitHub Actions,
GitLab CI, Azure DevOps — plus `none`, which writes no workflow files at all, crossed against
five `--target` deploy targets (`databricks`, `container`, `azure-container-apps`, `aws-ecs`,
`gcp-run`; the last three are labelled build and push only in the generated `haute.toml`,
`.env.example` and CI files, because deploy pushes their image to the required registry and
does not update their service yet). Whichever
provider is chosen, the generated workflow
encodes the same fixed release flow: a validate job (lint, type check, test, pipeline lint,
`haute deploy --dry-run`), an automatic deploy-to-staging job, a smoke test against staging
(scores `tests/quotes/*.json`), an impact-analysis job comparing staging against production,
and a production-deploy job gated behind a provider-specific approval mechanism. The
deploy starts on a push to the default branch that changes a release input: the
pipeline folder (`rating/`, its code, config and the versioned files under `files/`), the
test quotes, `haute.toml`, and the dependency
files `pyproject.toml` and `uv.lock`; a workbench's form folder is in the pipeline folder, so
a change to the form starts the deploy with them. GitHub and Azure DevOps filter the push on those paths;
GitLab's pipeline runs on every default-branch commit and filters none. A
build-and-push-only target's workflow has no smoke-test or impact-analysis job (its production
job follows staging directly): nothing runs the pushed image until someone updates the service
by hand, so those checks would test the old service. The production job tags the release in git:
Databricks as `deploy/v<registered model version>`; a container-based target, which has no
registered version, as `deploy/<short commit>` (the commit its image tag carries), leaving an
existing tag in place when the same commit is deployed again. Those tags are the record
`haute impact` reads, for a container target, of whether production has been released, so the
impact job's checkout keeps credentials for origin as the production job's does. GitHub uses
a separate `workflow_dispatch`-triggered workflow; GitLab uses `when: manual` on the production
job; Azure DevOps runs the production job as a `deployment` under an environment, `production`
unless `haute.toml` names another, whose actual approval check is configured on that
environment in the Azure DevOps portal rather than in the generated YAML. As generated, a
GitHub or GitLab production deploy runs for whoever may start that workflow or play that job,
which works on every plan, a free GitHub plan included. A team that wants a reviewer to approve
production names an environment in `[ci.production] environment`; `haute ci update` then puts
the GitHub and GitLab production jobs under it, so the reviewers configured on it in the
provider gate the deploy. Where the plan offers them: GitHub gives required reviewers to a
private repository only on GitHub Enterprise, and GitLab's protected environments need Premium.
A workbench project's workflows are a set of their own, the same for every provider in
shape: a validate job (lint, type check, test, pipeline lint, `haute app build`, keeping
the test quotes' report as its artifact) on a pull request and on a push to the default
branch that changes a release input; on that push a
publish job that logs in to the registry with the app host's credentials, runs
`haute app build --push`, which builds the underwriter app's image once, pushes it and prints
the reference by digest it pushed, rolls the staging app to that reference with
`haute app rollout staging --image` and runs `haute app impact`, keeping its
`impact_report.md` as the job's artifact; and a production job, run by hand after the provider's
approval (GitHub's `release-production.yml`, GitLab's manual `deploy-production` job, Azure
DevOps' `DeployProduction` stage, triggered by hand, under its `production` environment),
that rolls production to the release staging runs for the commit with `haute app rollout prod --from
staging`. It has no endpoint deploy or smoke test, and its impact prices quotes
production saved on both apps rather than a dataset on two endpoints
([workbench](../workbench/high-level.md)). Re-running `haute init` with a different
`--ci`, or for the other kind of project, prunes every workflow file Haute generated that the
new set does not hold before writing the new ones, so switching providers doesn't leave
orphaned config behind.

Every emitted GitHub Actions, GitLab CI, and Azure DevOps workflow is a complete
YAML document, not a template fragment that only looks plausible in Markdown.
The scaffold contract parses every provider/target combination and structurally
checks the release stages, branch/manual conditions, and target-secret mappings
at the exact deploy/smoke/impact steps that consume them.

The deployment guide's before/after project tree is generated from a real
`handle_init` fixture whose root `main.py` is removed. Documentation parity
tests derive the starter-pipeline node count from that same scaffold, compare
every documented `haute <command>` with the registered root help surface, and
import every `haute` Python surface named by a documentation example. The
checks have mutation-style negative fixtures for a drifted tree, stale count,
phantom command, and missing Python surface so an empty or self-confirming gate
cannot pass.

**Live callable arity and switching.** A live node derives its positional
DataFrame-input arity once when it is registered and reuses that immutable
result. Positional-only and positional-or-keyword parameters are inputs,
defaulted positional parameters are optional, keyword-only parameters are
configuration, and `*args` is the only supported variadic form. Unsupported
signatures or wiring fail with the node and expected/received arity. A
live-switch with no configured mapping may use its default source; once a
mapping exists it is exhaustive, and a missing active scenario raises
`LiveSwitchScenarioError` with stable code `live_switch_scenario_missing` and
stable switch/scenario/available-mappings detail. HTTP execution maps that
contract failure to 422 and background execution records the same fields.

**Canonical tabular I/O nodes.** The 19-value node vocabulary includes
non-singleton `dataInput` and `dataOutput` types exposed by both `Pipeline` and
`Submodel`. Data Input is a zero-parameter source with the strict provider
union and optional post-read Polars body; Data Output is a connected terminal
pass-through with the strict destination union. Multiple Data Outputs do not
create a primary writer: standalone return selection still requires one
explicit `output` or one unambiguous terminal leaf, while explicit persistence
names a particular Data Output. Their only sidecar folders are
`config/data_input/` and `config/data_output/`, and validation enforces the
active branch, format/group agreement, safe references, cache constraints, and
the absence of output code.

**Sidecars are authoritative.** No function body embeds a copy of a sidecar's
paths, source types, schemas, file types, model classes, switch mappings or constant
values. When a standalone run executes a configured node, the decorator's shared
helpers load the duplicate-key-rejecting sidecar (resolving its `config=` path against
the pipeline's directory: the directory of the file that defines the function, or, in a
submodel definition file below its pipeline, the directory its constructor's `pipeline_dir`
leads to), validate its active shape,
resolve relative paths through the normal project/pipeline policy, and perform the
same load, scoring or assembly the executor performs. Editing a valid sidecar therefore
changes the next parse and standalone execution without regenerating Python; a
missing, malformed, or shape-incomplete sidecar fails before the data/object file is
read.

**One config model per node type.** Every node type has one config model: the shape its
config may take, each key with its type, the values an enumerated key accepts, and which keys
hold `null` (where Haute writes it for a cleared setting; elsewhere an unset setting is an
absent key). The model is the one validation boundary. Parsing a pipeline, saving it from the
editor or the assistant, the node-scoped save, recovering a node's settings, writing a sidecar
and building a node to run it all hold the config to it, so a config any of them accepts the
others accept too. A key the type does not declare, a value of the wrong type or outside its
enumeration, and a value its engine rules refuse (a banding boundary, a GLM family and link
pair, a rating table row) fail at the first of these, with a message naming the node, the
setting's path in the config and what is wrong. (A Rating Step's rows are checked by its
builder as the first step of a run, not twice.) A config that is unfinished in a way the editor
allows (a Data Input with no file chosen yet, a new Modelling node, a Model Score whose source
mode is picked before its model) is not wrong: it saves, and the node reports what it lacks
when it runs or when training or a solve starts. The models are part of the generated browser
contract, so a node's config in every response is typed by its node type, and the node
reference's tables of config keys are generated from them.

**Keys are snake_case.** Every key a Haute schema owns is snake_case, in a node's config and its
sidecar, in a Polars step, in `.haute.json`, the global constants and the Workbench form folder,
and the browser's config types carry the same keys (READ-29). Before READ-29 these keys were
camelCase, and the loader refuses that form, naming each key's snake_case name. The keys
renamed:

| Before | Now | Where |
|---|---|---|
| `inputMapping` | `input_mapping` | any node's config, and the transform decorator's keyword in `main.py` |
| `inputType`, `outputType` | `input_type`, `output_type` | Data Input, Data Output |
| `sourceType` | `source_type` | Model Score, Optimiser Apply |
| `fileType` | `file_type` | External File |
| `leftOn`, `rightOn`, `maintainOrder` | `left_on`, `right_on`, `maintain_order` | Edge Join, and the join step |
| `outputMapping` | `output_mapping` | Output |
| `outputColumn`, `rightClosed` | `output_column`, `right_closed` | a banding factor |
| `outputColumn`, `defaultValue`, `onMissing`, `factorDtypes` | `output_column`, `default_value`, `on_missing`, `factor_dtypes` | a rating table |
| `timeUnit`, `timeZone` | `time_unit`, `time_zone` | a rating table's dtype descriptor |
| `combinedOutputs`, and its `outputColumn`, `baseValue` | `combined_outputs`, `output_column`, `base_value` | Rating Step |
| `stepCount` | `step_count` | Scenario Expander |
| `definitionId`, `instanceOf` | `definition_id`, `instance_of` | Submodel |
| `displayPath` | `display_path` | an API Input table |
| `orderBy` | `order_by` | a window expression |
| `nullsLast` | `nulls_last` | the sort step |
| `numberType` | `number_type` | a condition's list of numbers |
| `variableName`, `valueName` | `variable_name`, `value_name` | the unpivot step |
| `nodeId`, `handleId` | `node_id`, `handle_id` | a port endpoint in a submodel module's `haute.Submodel(...)` (READ-45) |

A key the analyst names never changes, whatever its casing: a column, a categorical level, an
input name in `input_mapping`, a factor in `factor_dtypes`, a source in `by_source`, a constant,
a sheet or a table. Values are not keys either: node type names such as `dataInput` stay as they
are. The graph the server and the browser exchange is not a saved file and keeps its own keys
(`nodeType`, `sourceHandle`, a submodel definition's `definitionId`, `inputPorts` and
`outputPorts`).

For tabular Data Input values specifically, a relative `path` is interpreted
from the Haute project root by both canvas execution and standalone runs. The
standalone runtime discovers the project root from the pipeline file and uses the
same canonical runtime resolver as the executor; the sidecar's own
`config/data_input/...json` location remains pipeline-relative. There is no
standalone-only rebasing of `data/foo.parquet` beneath the pipeline module
directory.

**Stepped transforms.** A `polars` transform may be authored as an ordered list of
low-code steps instead of hand-written code. The steps live only in the node's code in
the pipeline file (READ-22): a `@pipeline.polars` decorator takes no `config=`, and no
`config/polars/` file exists. Its column settings (`selected_columns`, `column_renames`)
are decorator keywords, as for any transform. Loading rebuilds the steps from the body
(`haute._polars_steps_lift.lift_steps`, whose canonical form
[structured-syntax-boundary](../codegen/structured-syntax-boundary.md) records): a
statement becomes a step when that step renders exactly its text, and each run of
statements it does not recognise becomes one code step, the statements around it staying
steps, so a hand edit to one statement or a comment keeps the rest. A body that does
not rebuild at all (one whose lines cannot be split into statements) loads as a
code-only transform. Each step's id is its kind and its place (`filter-3`), given anew at
each load. A `@pipeline.polars(config=...)` is the form before READ-22 and is refused,
saying the steps go in the function's body. One renderer (`src/haute/_polars_steps.py`) validates the closed step
schema and renders the steps into the function body, recording each step's inclusive
line range and raising a step-indexed error for any malformed or incomplete step.
Each structured step renders one statement in common Python style, as ruff and the
Polars documentation write it: brackets only where operator precedence needs them,
double quotes unless single ones need fewer escapes, group-by keys, aggregations and
selected, dropped or filled columns passed as separate arguments rather than lists,
and a sort or unique that uses Polars' defaults leaves them unsaid. A statement that does
not fit in 88 columns at the function body's indentation is laid out over several lines
the way `ruff format` lays it out,
except that a broken call always puts one argument per line with a trailing comma; a
list that still does not fit is broken the same way. The body reads like a formatted
pipeline file, and `ruff format` at its default line length and quote style leaves it
unchanged: a float is written as ruff writes it, with no `+` in its exponent (`1e20`), and
a free-code step keeps its authored layout with only the whitespace ruff removes taken
off, the spaces ending a line outside a string literal (a multiline string keeps every
character it holds). Since the rendering is a fixed point of `ruff format`, formatting
the pipeline file keeps a transform's steps; a statement in another layout or spelling
loads as code.
Step-id uniqueness checks for valid lists run in linear time in the number of steps.
Every step but a `free_code` step may carry a `comment`: the comment and blank lines
written above its statement, verbatim, each line ending in a newline
(`"# Exposure in years\n"`). The renderer prints it above the statement, so rebuilding
the steps from code keeps a comment with the step below it rather than turning that step
into code. Each line must be empty or a comment, with no whitespace at its end; the step's
line range covers its statement only. The step editor keeps the field through every edit
and shows no control for it
([structured-syntax-boundary](../codegen/structured-syntax-boundary.md), The `comment`
field).
The vocabulary covers filters
(comparison, null, membership, text and regex operators), derived columns (formulas,
typed functions over numbers, text, dates and durations, conditionals with one or more
branches, window aggregates of a column or an expression with a partition, an optional
in-partition order and an optional row filter, text joins, and sums across columns),
select, drop, rename, cast (the integer, float, string, boolean, date, datetime and
categorical dtypes, with a required `strict` flag: `false` gives a missing value
wherever a value does not convert, as Polars' `strict=False`), sort, unique, group-by with optional per-aggregate row filters
and quantiles (an empty key list summarises the whole frame), joins with optional
key-cardinality validation (inner, left and full joins only, as Polars refuses it
elsewhere) and output-order control, concat, fill-null, limit, and
node-local variables (a variable step is refused, naming it, when a `free_code` step
above it already assigns its name); a null literal renders as `pl.lit(None)` in expression position
and is refused in membership lists and variables. An operand may itself be a nested
expression (`{"kind": "expr", "expr": ...}`) wherever a value, column or variable is
accepted, except in membership lists, variable values and the plain-value function
arguments, which stay plain values; a value, formula, function or sum-across expression
may carry a `text` annotation (the
formula exactly as the editor's user typed it, kept for display and never rendered); a
nested formula is bracketed only where Python's left-to-right
evaluation needs it (a left operand only when its operator is weaker than the
parent's, a right operand and either side of `**` always, and anywhere outside a
formula), so a chain of terms reads flat, and nesting is capped at twelve levels (a
step's own expression is level one), beyond which the step is refused
with a message to compute part of the expression in an earlier step. A chain of one
operator counts as one level: a formula whose left operand is a formula with the same
operator (`+`, `-`, `*`, `/`, `//` or `%`, which Python chains without brackets), so a
sum of any number of terms is one step. The chain stays a binary tree and is never
regrouped, and a right operand or a left operand with another operator still counts a
level; inside a
group-by aggregation's or a window's row filter operands stay plain, because a nested
aggregate there would mean the group's or the partition's value rather than the
frame's. A conditional is a list of `branches`, each `{"match", "conditions", "then"}`,
and an `otherwise`, rendered as one `.when(...).then(...)` per branch after the first
`pl.when(...).then(...)` and then `.otherwise(...)`, so a row takes the value of the
first branch whose conditions hold (a single branch renders exactly as
`pl.when(c).then(t).otherwise(o)`). A window aggregate's `of` is a column or a nested
expression (`pl.col('limit').sum().over(['q'])`, or with a formula in brackets
`(pl.col('limit') * pl.col('load')).sum().over(['q'])`); a row count and a row number
take no `of` and every other aggregate needs one. A window whose aggregate reduces a
partition to one value (the group-by aggregates, sum through row count) may carry a
`where` row filter shaped like a group-by aggregation's, rendered as a `.filter(...)`
before the aggregate (`pl.col('limit').filter(...).sum().over(['q'])`, and a row count
as `(...).sum()`), so a partition with no passing row aggregates no rows (0 for sum
and the counts, null otherwise); the positional aggregates (row number, running total,
previous value, rank, dense rank, forward and backward fill) refuse a row filter,
because filtering changes the partition's length. A function's arguments are typed:
clip's bounds and divide's divisor (`number_operand`) and fill_null's value
(`scalar_operand`) take a column, a step variable, a constant of the argument's types,
a nested expression, or a plain value (a number; or a number, text or true/false),
rendered in value position (`pl.col('irpm').clip(pl.col('floor'), pl.col('cap'))`);
every other argument takes a plain value or a constant of its type. `divide(value,
divisor)` renders `pl.when(divisor != 0).then(value / divisor)`, which is null where
the divisor is zero or missing, while `/` stays Polars' division (`x / 0` is infinite
and `0 / 0` is NaN). A sum across columns (`{"type": "sum_horizontal", "parts":
[...]}`, two or more operands in expression position) renders `pl.sum_horizontal(...)`,
which skips missing values, so a row whose parts are all missing sums to 0. Two reshaping steps
complete the vocabulary: a fixed-column `pivot` (index columns, the column whose
values spread out, one entry per output column pairing a plain literal value with an
output name, the values column, and an aggregate among sum, mean, min, max, median,
first, last, count of non-null values and row count) is lowered to a maintain-order
group-by of filtered aggregates rather than Polars' own `pivot`, so the lineage and
cardinality models prove it and its output schema is fixed by the step (each cell
matches `pivot(on_columns=...)` value for value: an empty cell is 0 for sum and the
counts and null otherwise); duplicate values, duplicate or index-colliding names,
mixed value types and null values are refused; and `unpivot` renders the native
call with a non-empty literal `on` list, an optional index, and distinct name and
value columns that must not collide with the index (row order afterwards is
unspecified, as in Polars). Select and drop take an optional list of column types
beside the named columns, rendered as one `pl.col(<dtype>)` per type excluding the
named columns so a column is never projected twice (`df.select(['g',
pl.col(pl.Float64).exclude('g')])`), and a group-by aggregation may target every
column of one type instead of a column, naming its outputs by suffix
(`pl.col(pl.Float64).mean().name.suffix('_mean')`; no row filter, no row count, and
no uniqueness check on suffixes). Those dtype selections are the closed selector forms
the lineage model expands from an upstream dtype schema; without one, or after a
computed column whose dtype is not propagated, lineage fails closed exactly as the
same hand-written selector would. The node data
model enforces
one invariant on construction: a `polars` config that carries `steps` always carries the
rendering of those steps as its `code`, or an empty `code` plus a `steps_error` message
in the editor state it carries beside the config when they cannot be rendered, so every consumer that reads
transform code (execution, chunk-local classification, projection, estimation, tracing, codegen)
sees the same program without knowing about steps and a stale `code` in a browser
payload is overwritten on ingress; in-process config replacement uses the validated
`GraphNode.with_config` helper rather than an unvalidated model copy so the invariant
also holds after assistant and submodel operations. A stepped transform addresses its
inputs by their current edge names and never carries `input_mapping` (a config with both
fails loudly), and submodel flattening rewrites the input references inside a stepped
transform's steps instead of recording a mapping. A transform with an unfinished step
is saved with the raising placeholder for its body and its whole step list, the steps
before and after the unfinished one included, in the pipeline's `.haute.json` under
the node's name (`unfinished_steps`), and the save warns which step; so the node fails
in the editor, `haute run`, a standalone run and a deploy alike, never returning the
steps before the unfinished one. Every load of the file gives such a node the steps the
sidecar holds (the editor's, `haute run`'s and a deploy's parse, a degraded document and
Recover alike); a save of the one node keeps them in its
file's sidecar as a whole save does; once every step is complete the next save renders
them into the body and drops them from the sidecar. A sidecar entry for a node whose body is not the
placeholder is ignored, and the next save drops it.

**Stepped surfaces.** The same step list authors the Polars code of every surface
that has one: a Data Input's post-load code, an External File's code over the loaded
object, the post-processing of a Rating Step, a Model Score and a Scenario
Expander, and an Explore node's analysis frame. The renderer takes a required start mode: `input` is the transform's (the
first step chooses the input and renders `df = <input>`), and `frame` is for a
surface whose code runs with `df` already bound (the opened snapshot, the first input
beside `obj`, the rated, scored or expanded frame). In frame mode an empty list renders
to empty code without error, because the node then simply keeps its base behaviour, a
`source` step is refused at any position with a step-indexed message, and
`join`/`concat` references are checked against the surface's eligible input names.
Every surface's code is the only copy of its steps (READ-44), as the transform's is:
parsing rebuilds them from the body, a statement a step renders exactly becoming that
step and each run of other statements one code step, so a hand edit keeps the other
steps. A frame surface whose function is a declaration has no steps yet (an empty
list), and one whose lines do not split into statements is code-only. Its sidecar writes no `steps`,
and loading one that does is refused, naming the file and saying the steps are
rebuilt from the body. An incomplete step list is saved as a transform's is: the body
is the raising placeholder and the list waits in the pipeline's `.haute.json`
(`unfinished_steps`), for any stepped node.
An Explore node keeps its overview, pivot formulas, pivots and charts in its sidecar,
`config/explore/<node>.json`, checked and normalised on the way out and in by the
Explore display-config validators, each left out when empty; its decorator carries only
`config=`, and one that still carries settings or steps is refused like any node whose
settings belong in a sidecar (READ-43). A standalone run of the module refuses them too,
and holds the sidecar to the same validators, so a malformed one fails the run as it
fails the parse.
One table (`STEPPED_NODE_TYPES` in `haute._polars_steps`) maps every stepped node type
to its start mode and its input eligibility: `edges` for a transform and for an
External File (whose code sees every connected input by name, the first also as
`df`), `none` for the surfaces whose code sees only `df` (Data Input, Rating Step,
Model Score, Scenario Expander, Explore), where a join or concat is refused. Every path that
renders a node's steps (the node data model, the parser's rebuild, the executor
builder, codegen, the deploy interceptors and the render endpoint) obtains the
eligible names from it. Only an `edges` surface has its step references rewritten when
an input is renamed (a node rename, an Edge Join insertion, a submodel boundary), and a
stepped `edges` original never carries `input_mapping`. A free-code step's code is not
rewritten. Where a submodel boundary renames an input such a step names, the expanded
node runs its code (the server's rendering of its current steps) as code without steps
runs, reading the name it was written with through `input_mapping`; steps that do not
render yet stay, so the node still fails as unfinished. The editor refuses a node rename
or an Edge Join insertion that would change an input such a step reads, scanning the
code's text for the name, since its copy of the code may not be the current steps'
rendering: the message says to read the input with a Join step, or remove the code step.
A node
type outside that table that carries a `steps` key is left alone. A Scenario
Expander's grid size is its required `step_count` (a whole number of at least 1, read
by the executor builder, the standalone runtime's helper, the chunk planner, the RAM
estimator and the trace enrichment through one `scenario_step_count` function with no
absent-key default; a new node is created with an explicit 21), so `steps` on that
type is free for its step list. A missing or malformed `step_count` is a config defect the
user can fix, so `scenario_step_count` raises `NodeConfigError` (stable code
`node_config_invalid`, safe field `setting`): a preview, trace, output write or job reports
it as the public contract failure (HTTP 422, background `contract_error`) with the message
naming the setting, never as an internal error. The planner and estimator, which only ask
whether they can plan the node, still see it as a `ValueError`. The node-data invariant applies to every stepped type:
`code` is always the steps' rendering (or empty, with a steps error), and on load the
steps are rebuilt from the body (READ-44), the rebuild modelling what the extractor
normalises (a lone `df = (df.head(2))` free-code step loses its brackets) so the node
still reloads in step mode. A Data Input's sidecar holds its source settings and never
`steps`. An unrenderable Data Input step list is saved as a hook whose body is the
generated placeholder, with the list in the pipeline's `.haute.json`, so `haute run`, a
deploy and a standalone run raise rather than silently reading the source unchanged,
and every load gives the node its steps back, exactly as for a transform; a placeholder
whose list that file does not hold is refused, naming the node, since running it would
skip the steps. A new Data Input starts in step mode with an
empty list.

A `free_code` step carries a `code` string containing Python statements and can
appear anywhere after the source step (anywhere at all in frame mode). Its statements run inline, in order with
the low-code steps: `df` is the current frame, `pl` is available, and earlier
Define variable values can be used. Assign transformations back to `df`; later
steps consume that frame. Multiline expressions, comments, local helpers and
control flow are supported. A snippet may hold only comments and blank lines, which is
how a comment at the end of a body is kept. Blank snippets, invalid Python, and
node-level `return`, `yield`, `await`, or loop control outside a loop fail with the
offending step index; returns inside helper functions are allowed. Validation
compiles but never executes authored code; frame validation models `df` already
bound, and diagnostics and ranges refer to authored lines. The stored snippet is
preserved; rendering normalises line endings and removes the whitespace ending each line
outside a string literal, as `ruff format` does, so generated code round-trips through the
existing extractor and through a reformat. A step whose code opens with a bare string is
never its function's docstring: the generated body puts an empty docstring before it, so
neither the parser nor a formatter treats the string as documentation. Free code shares the existing
code execution and planning contracts, with no separate evaluator. Input renames
continue to rewrite structured input fields; authored Python is unchanged. Use
`df` to operate on the current frame across input renames; direct references to
other input names in a snippet must be kept in sync by the author.

**Global constants.** A pipeline may declare global constants: named, typed values that node
code reads as `global_constants.<name>`. A name starts with a letter, continues with letters,
digits and underscores, is not a Python keyword, and is unique (case-sensitively). The type is
`integer`, `float`, `text`, `boolean` or `date`, and a constant is either uniform (one `value`)
or split by source (`by_source`, at most one value per source; a source it lacks is incomplete,
not invalid). Values are JSON scalars of their type: an `integer` lies within ±(2⁵³ − 1), the
whole numbers the editor holds exactly; a `float` is stored as a float however it
is written, and a `date` is `YYYY-MM-DD` text naming a real date. The constants live in
`config/global_constants.json` beside the node configs: one object whose only key, `constants`,
lists them in order, each with `name`, `type` and exactly one of `value` and `by_source`. The
pipeline constructor names the file, `global_constants="config/global_constants.json"` (the
only value it accepts), and the statement after the constructor binds the reserved name,
`global_constants = pipeline.global_constants`; every submodel file the pipeline writes binds
`global_constants = submodel.global_constants`, because a submodel reads its pipeline's
constants and declares none. A pipeline without constants has no file, keyword or binding. No
other module-level statement, node function, preamble, node code or step variable may bind
`global_constants`. In the live API both bindings return one sentinel: every read of it raises
a global-constant error saying that constants are read in node code while the pipeline runs.

Code reads a constant as `global_constants.<name>` in the code box and the free-code steps of
every stepped node type (Transform, Data Input, External File, Rating Step, Model Score,
Scenario Expander and Explore), and in a pipeline file's node functions. A structured step
reads one through a Constant operand, offered wherever the step editor offers a variable, and
in a typed function argument; the step renders as `global_constants.<name>`. A comparison,
fill, formula or branch value takes a constant of any type, a string operator's value a `text`
one, and a function argument a constant of its type (a whole number of zero or more takes an
`integer` whose every value is at least zero; a number takes an `integer` or a `float`; a type
argument takes none). Save refuses a step operand naming an undefined constant, or one whose
type or value its slot does not take, naming the node, the step and the constant, except while
the constants file fails to load, when the definitions are unavailable.
A node reads the constants its code names as `global_constants.<name>` (in an f-string's
expressions too) and its steps reference.
Code that uses the name `global_constants` any other way (passing it to a function, `getattr`
with a computed name) may read every constant. No other route to a constant is tracked, so a
read by any other route (`eval` of a string, say) fails in that node, naming the constant and
asking for `global_constants.<name>`. A cached result can therefore never depend on a constant
its identity does not sign.

Every run binds `global_constants` for the source it executes under, which is the value its
Source Switches route on:

- previews, traces, free-code column resolution, Explore and training use the toolbar's source,
  which their requests carry;
- the optimiser, and a Data Output write started under `live`, use the graph's batch scenario
  (its Source Switches' one non-live source, or `"batch"`), as they do today;
- deployed scoring uses `live`;
- `pipeline.run(source=...)` uses the source it is given, which it requires, and
  `pipeline.score()` uses `live`, and `haute run` `live` unless `--source` names another.

A run binds concrete values, never a lookup that depends on the calling thread, so a lazy
Polars callback that reads a constant during collection reads the run's value on whichever
thread runs it, and two runs under different sources never see each other's values. A
submodel's nodes read the run's constants through the binding in their own file, as the
pipeline's nodes do.

No node, step variable, node code or preamble may bind the name `global_constants`, so nothing
shadows it. A constant's name is only ever an attribute of that name, so it can never collide
with an input, a preamble binding or a step variable.

Reading an undefined constant, a split constant with no value for the run's source, or any
constant while its file failed to load raises a global-constant error in the node that reads
it, naming the constant, the source and the fix, and so does a read by a route the reads
analysis cannot see, such as `eval` of a string. That error is an ordinary node failure: a
preview shows it on the reading node and keeps previewing the rest, and every other run stops
with it. A trace shows each constant's value where a formula reads it.

Deployment: the deployed graph carries the pipeline's constants and reads their `live`
values. Deploy validation refuses a deployed graph that reads a constant with no `live` value,
naming it. A constant read only on a branch that deployment prunes away does not need a `live`
value.

The editor edits constants in the Constants pane
([frontend-shared](../frontend-shared/high-level.md)), whose every execution request and save
carries them, and the step editor offers them as operands
([frontend-node-editors](../frontend-node-editors/high-level.md)).

## Design rationale

The component leans hard on failing loudly rather than guessing: duplicate node names,
`async def` node bodies, ambiguous pipeline auto-discovery, a `contract=` declaration that
disagrees with what the config implies, and a missing sidecar for a folder-backed node type
all raise a specific, named error rather than silently picking a default. Unrecognised config
keys follow the same rule. A key the node type does not declare fails the parse with a
`ConfigError` naming the node and the key, and a save that carries one is refused before
anything is written. A key that is dropped and logged would lose persisted work without the
user seeing it. The editor opens a pipeline whose sidecar carries such a key in recovery,
where only that node is unavailable, and the node's repair removes the key while keeping it
in the recovery evidence.

Config that is genuinely code (pricing logic, transforms) lives in the `.py` function body;
everything else declarative lives in a JSON sidecar. This keeps generated/round-tripped
Python readable — no large JSON blobs embedded as string literals — while letting the GUI
edit the declarative parts without ever touching Python source.

A node type's declared keys are its config model's keys plus the universal keys. The same
model is checked at parse, at save validation, at the sidecar write boundary, in recovery and
when a node is built to run. An off-spec key or value from external tooling, a
not-yet-hardened code path, or a frontend bug therefore fails the operation that carries it,
and no code path removes a key and continues. The model is the node type's existing config
`TypedDict`, which Pydantic validates closed and strict, rather than a second class beside it:
the engine code that reads a config, the recovery walk over its fields and the generated
contract all read one definition, and a key such as `validate` or `model_path` needs no alias.
Value rules that need more than a type (a banding boundary, a Polars call's arguments, a GLM
family and link) stay the engine's own functions, which the model calls, so a rule has one
implementation.

Rating-step sidecars have one canonical persisted entry shape: ordered row arrays. Reads and
writes validate that shape directly. Object-key maps are not accepted because a JSON object key
cannot preserve the scalar identity or dtype metadata of a rating level.
Validation and canonicalisation finish before the save service stages any file, so a malformed
rating table leaves the prior sidecar untouched.

Contract validation at parse time deliberately avoids contacting MLflow for model-scoring nodes:
their input side is treated as opaque while the locally configured output column is still
checked. For other node types, only a named infrastructure failure raised while deriving a
contract (`OSError`, `ImportError` or `MlflowException`) makes that comparison use an opaque
contract, as the [canonical-input rule](../README.md#canonical-only-format-policy) allows. A
`ConfigError`, a `RuntimeError` and programmer-shaped errors such as `TypeError`,
`AttributeError` and `KeyError` propagate, so the node fails to load with that error.

Windows-reserved device filenames (`CON`, `NUL`, `COM1`, etc.) are rejected on every
platform, not only when running on Windows, so a project saved on Linux or macOS stays
loadable on a Windows checkout.

`haute.toml`'s `[ci]` table records only *what* — the provider name and the staging endpoint
suffix — never *how*: the actual step-by-step YAML lives in the project's own
`.github/workflows/`, `.gitlab-ci.yml`, or `azure-pipelines.yml`, generated by `haute init`
and owned by the team. Two explicit commands write it again, and nothing else does, so a
team's hand-edits to their workflow (extra jobs, custom notifications, org-specific runners)
stay stable across unrelated `haute.toml` changes: `haute ci update` regenerates the chosen
provider's files from the installed Haute's templates and the `[ci]` table (the staging
deploy, smoke and impact steps pass the recorded suffix), a workbench project's being the
workbench's set for its `[workbench.app] host`, overwriting hand-edits to those files
and pruning every other generated file; `haute init` in an initialised project asks its
questions again, writes `haute.toml` afresh from the answers, prunes the previous provider's
workflow files and rewrites the newly-chosen provider's from the templates, leaving the
pipeline folder and the tests as they are.

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
  this component produces. The two components share one per-node-type
  execution/column-contract registry, but this component owns registration
  and the parse-time contract cross-check, while
  execution-engine owns what each builder actually computes at runtime.
- [codegen](../codegen/high-level.md): round-trips the `GraphNode`/`GraphEdge` graph this
  component builds back into `.py` source and sidecar JSON, and consumes the
  same registry.
- [expression-parsing](../expression-parsing/high-level.md): performs the AST walk over the
  raw `.py` pipeline file (splitting out function bodies, decorator keyword arguments,
  docstrings) that this component's graph-builder consumes as input.
- [server-api](../server-api/high-level.md): exposes sidecar save/load, project discovery,
  and graph construction to the GUI over HTTP.

## Failure model

A transform written as code keeps its keyword-only parameters with literal defaults
(`def scaled(source, *, factor=1.2)`) through parse, the editor, save and every run, so the
parameter, not a same-named preamble value, is what its body reads. A keyword-only form the
pipeline file cannot carry back (a parameter without a default, a default that is not a
scalar literal, a list or dict default included, a keyword-only parameter on any other node or on a stepped transform, apart
from an External File hook's `obj`, or a `**kwargs` parameter) is a parse error naming the
node, the parameter and its line, so the editor shows it and Save is refused rather than
the next save dropping it.

A `connect()` call that the live signature would refuse (an unknown keyword, a third
positional argument, a value bound twice, a missing or non-string endpoint, a port that is
neither `None` nor a non-empty string) is a parse error naming the call's line and the
argument, so the editor shows it on the call and Save is refused; it is never skipped or
repaired.

Missing, unreadable, or invalid-JSON sidecar files raise a config error naming the path.
Using a folder-backed node type without a `config=` sidecar raises, naming the concrete
config folder and suggesting `haute init`. A JSON sidecar with a repeated key is rejected at
read time rather than silently keeping the last value. Every failure to read a sidecar names
its file, also in a run outside the editor, where no parser wraps the error. Two node functions sharing a name are
rejected, both at live decorator-registration time and again at static parse time (the
function name becomes the graph node id, so a silent collision would drop a node).
Registration also applies the codegen specification's executable-name rule, naming the node:
a reserved or built-in name, a name equal ignoring case to another node or submodel
occurrence, and a module-level function whose name the module already binds to something
else (a preamble helper, an import) are refused, while `pipeline.polars(f)` on an
already-defined `f` registers it. A
Constant node whose `values` hold two entries with one non-empty name is refused with a
node-config error naming the name, wherever its frame is built (`constant_frame`, which
canvas execution and a standalone run share), rather than keeping the second value; an
entry whose name is not text is refused the same way, and an entry without a name is
skipped, as before. Its sidecar writes `values` as a map from each name to its value,
one a line (`haute._constant_config`, READ-28): a value that reads as a number and
reads back to the text it was typed as is a JSON number, anything else as typed; each
runs as before, and the editor reads the map back into its rows. A row without a name
is not written, two rows with one name are refused, and a sidecar still holding rows is
refused, naming the map form. An
`async def` node body is rejected at parse time. A user-declared `contract=` that disagrees
with the contract derived from the rest of the node's config raises, naming which side
(inputs/outputs) mismatched and what was missing or extra on each. Ambiguous or absent
pipeline-file resolution raises, enumerating every candidate it considered. Not being inside
a Haute project (no `haute.toml`, or no git repository above it) raises. An unrecognised config
key is a `ConfigError` at parse that names the node and the key, and a save that carries one is
refused with HTTP 400 naming both, before any file is written. Retired identity fields are
refused with their own targeted message. Optimiser
`data_input`/`banding_source` and Optimiser Apply `ratebook_input` persist exact incoming-edge
names and are never remapped from node ids; an unmatched name fails graph/runtime validation.
Plural discovery still skips a candidate whose contents cannot be read. A declared global
constants file that is missing, unreadable, not strict-UTF-8 JSON, or invalid (an unknown key,
a bad or duplicate name, a value that does not fit its type, both or neither of `value` and
`by_source`) is a load error naming the file and the entry: the pipeline still parses, with a
graph warning, and no save rewrites or deletes the file. A constructor keyword naming any other
path, a submodel constructor that names one, and any other binding of `global_constants` are
parse errors naming the line, apart from a node function of that name: it breaks the codegen
naming rule like a node named `pl`, which the strict parse refuses and the editor load reports
as a renameable name violation.
