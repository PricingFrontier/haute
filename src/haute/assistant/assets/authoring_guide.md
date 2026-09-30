# Haute pipeline authoring guide

Use the assistant to make small, explicit changes to a saved Haute graph.  Haute
pipeline source is a Python module, but the decorators and the graph wiring are
the durable interface: node functions should have clear names, typed frame
parameters, and a single responsibility.

The node catalog supplied alongside this guide in every assistant prompt is the
complete, mechanically-derived vocabulary. Use the specialised type whose name
describes the operation; do not substitute a generic `polars` node when a
domain node is required.

## The canonical shape

Most pricing pipelines have a source, a sequence of transformations, and one
terminal output:

```python
import haute
import polars as pl

pipeline = haute.Pipeline("pricing", description="Short analyst-facing description")


@pipeline.data_input(config="config/data_input/quotes.json")
def quotes(): ...


@pipeline.polars
def enriched(quotes: pl.LazyFrame) -> pl.LazyFrame:
    return quotes.with_columns(vehicle_age=2026 - pl.col("vehicle_year"))


@pipeline.output(config="config/quote_response/priced.json")
def priced(enriched): ...
```

Every node type except `polars` is configured: its decorator names its settings
and performs the node's work (reading the source, scoring, rating, joining,
assembling the response) when the file runs.  Such a node is a one-line
declaration whose parameters name its inputs and whose body is `...` (or its
docstring).  New logic on a `polars` node, and post-processing on a Data Input,
Load File, Rating Step, Model Scoring, Expander or Explore node, is authored as
steps (see "Steps" below); the saved file holds the code the steps render.
Never call Haute's loader or scoring helpers from a function body, and never
import from a `haute._` module.

`haute init` scaffolds a blank pipeline: `rating/main.py` declares the
`haute.Pipeline` and no nodes, and the analyst adds nodes in the editor.
Configured nodes keep their settings in project-relative `config/...` JSON
sidecars, and the packaged examples do the same, so follow their sidecar
references when authoring a real project.

Use `api_input` for the live request source, `data_input` for configured file,
database, lakehouse, Databricks, or inline tabular data, and `polars` for
ordinary feature engineering. Use the
specialised node decorators (`banding`, `rating_step`, `model_score`,
`edge_join`, and so on) when the operation has that domain meaning; do not hide
one of those operations inside an unlabelled transform.

## Names and wiring

- Use short, stable `snake_case` names that describe the data at each step:
  `quotes`, `customer_features`, `rated_quotes`.
- A function parameter with the exact upstream node name gives a clear implicit
  edge for a simple linear chain.
- Use `pipeline.connect("source", "target")` when a graph branches, has more
  than one input, or needs named ports.  Keep explicit connections together at
  the bottom of the module so the topology is easy to audit.
- An `edge_join` has two distinct incoming roles. Connect the primary frame
  with `target_handle="base"` and the lookup frame with
  `target_handle="join"` in graph-edit operations; Python source uses the
  equivalent `target_port` keyword on `pipeline.connect`. Exactly one
  incoming edge of each role is required.
- A node's input parameters are frame inputs.  Configuration belongs in the
  decorator or its JSON sidecar, not in a hidden module global.
- Keep one `output` node for the pipeline's returned quote document. A
  `data_output` is a separate explicitly-written branch for persisting tabular
  data; graph save, preview, trace, and ordinary execution never write it.

## Configuration and transforms

Folder-backed nodes refer to their sidecar with a project-relative `config/...`
path.  Preserve that convention when adding or changing a node.  Do not put
secrets, credentials, or machine-specific absolute paths in pipeline source.

Prefer lazy Polars expressions (`pl.col`, `with_columns`, `select`, `join`, and
`drop`) and vectorised arithmetic (`2026 - pl.col("vehicle_year")`) over
collecting a frame or calling Python per row.  Polars frames are immutable: a
bare `df.filter(...)` expression is discarded and is refused, so assign the
result to `df`.  A `polars` node with no steps has not chosen its input and is
refused; there is no implicit passthrough.  Make joins explicit about their keys
and join type, and name derived columns so downstream steps can refer to them
without guessing.

A modelling node's `algorithm` (`catboost`, `xgboost`, `lightgbm`, `ebm`, or
`glm`) is fixed when the node is created: to try another family, add a new
node rather than editing `algorithm`.  For the tree and EBM families, put the
loss in `loss_function` and the family's own keys in `params` (`iterations`,
`num_boost_round`, `num_iterations`, or EBM's required `max_rounds`); never set
objectives, threads, seeds, or aliases there.  An EBM never stops early, so give
it an explicit `max_rounds`.  A GLM is different: it has no `loss_function` or
`params`, and is configured by top-level `family`, `link`, `terms` and
`interactions` instead.

## Steps

A `polars` node and every node that takes post-processing (Data Input, Load
File, Rating Step, Model Scoring, Expander, Explore) holds an ordered `steps`
list in its config, and the editor's step builder shows each step as a card.
Write new logic as steps with one free-code card:

- On a `polars` node, start with a `source` step whose `input` names the
  incoming edge that becomes `df`, then a `free_code` step:
  `[{"id": "start", "kind": "source", "input": "quotes"}, {"id": "logic", "kind": "free_code", "code": "..."}]`.
- On every other stepped node `df` is already bound, so the list is one
  `free_code` step: `[{"id": "logic", "kind": "free_code", "code": "..."}]`.
  On a Load File node `df` is the first input; elsewhere it is the frame the
  node produced.

Every step needs a non-empty id, unique in its list.  The code transforms `df`
and assigns the result to `df` (a `return` is refused).  It reads other inputs
by their edge names only on a `polars` or Load File node; every other stepped
node sees only `df`.  On a Load File node the loaded object is `obj`.  Start the
code with a one-line `# intent` comment: the step builder shows it as the
card's title.  A node that needs no post-processing keeps `steps: []`.

Keep what the analyst built.  Change a node that already holds steps with
`edit_steps`, which names steps by the ids `get_pipeline` and the graph brief
list: `{"insert_after": "<step id>", "step": {...}}` (`null` inserts at the
start), `{"replace": "<step id>", "step": {...}}` or `{"remove": "<step id>"}`,
applied in order.  A step sent without an id gets one.  Every step you do not
name keeps its id, its place and its content, so a free-code step whose code
the project's policy withholds is kept, or replaced or removed whole, but never
edited in place.  A node in code mode (a `code` config without `steps`) is
edited through its `code`.  Never switch a node between steps and code; that is
the analyst's choice in the editor.  To edit a structured step, read
`step_grammar`, which comes with this guide: each step kind with its fields and
the closed vocabularies those fields take.

A `polars` node fed by `proposer_claims` and `additional_drivers_claims` that
totals both sources' August claims per policy:

```json
[
  {"id": "start", "kind": "source", "input": "proposer_claims"},
  {"id": "logic", "kind": "free_code", "code": "# Total August claims per policy across both claim sources\ndf = pl.concat([df, additional_drivers_claims]).filter(pl.col(\"claim_month\") == \"2026-08\").group_by(\"policy_id\").agg(august_claims=pl.col(\"amount\").sum())"}
]
```

A Rating Step capping the premium it produced:

```json
[
  {"id": "logic", "kind": "free_code", "code": "# Cap the premium at 150\ndf = df.with_columns(premium=pl.col(\"premium\").clip(0, 150))"}
]
```

A Load File node whose file holds regional loadings, with `quotes` as its first
input and `regions` as its second:

```json
[
  {"id": "logic", "kind": "free_code", "code": "# Add each quote's zone and regional loading\ndf = df.join(regions, on=\"region\", how=\"left\").with_columns(loading=pl.col(\"region\").replace_strict(obj, default=1.0))"}
]
```

Keeping only the policies with more than 100 of August claims on that `polars`
node, once it is saved, without resending its other steps:

```json
{"op": "edit_steps", "node": "august_totals", "edits": [
  {"insert_after": "logic", "step": {"kind": "free_code", "code": "# Keep policies with over 100 of August claims\ndf = df.filter(pl.col(\"august_claims\") > 100)"}}
]}
```

## A safe editing pattern

1. Read the saved graph before editing; node ids are the function names.
2. Retrieve the complete capability descriptor for every node type that will
   be added or configured, and follow its ports, wiring rules, closed config
   schema, enums, and anti-patterns. Its card shows a minimal and a realistic
   configuration with real values and the meaning of each field; write the
   node's configuration in the same shape.
3. Make the smallest ordered graph edit that expresses the user's intent.
4. Connect new nodes immediately and check that every input has the intended
   upstream frame.  Do not add disconnected decorative nodes.
5. Ask for a node schema when a downstream expression depends on columns that
   were created, renamed, joined, or dropped upstream.
6. Dry-run the complete batch. The dry run resolves affected lazy schemas
   without collecting rows and binds that evidence to the exact plan.
7. Apply the plan exactly once with the returned plan hash. Never reconstruct
   or resend operations at apply time.
8. Leave execution, training, optimisation, deployment, and git operations to
   the analyst's explicit product actions.  The assistant authors the graph; it
   does not run costly jobs. Pipeline runs and external writes are protected at
   execution time, not graph-authoring time.

## Do and don't

Do preserve existing names, descriptions, contracts, and sidecar conventions
unless the analyst asks to change them.  Do use the existing node type that
matches the operation.  Do keep changes local and verify the resulting edges.

Don't replace the whole graph for a one-node request.  Don't invent a node type
or config key, guess a column name when the schema tool can answer, or create a
second output singleton.  Don't edit inside a submodel in v1: submodels are
boundaries, and their internal graph must be changed through the appropriate
project workflow.
