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
    return quotes.with_columns(
        vehicle_age=pl.col("vehicle_year").map_elements(
            lambda year: 2026 - year,
            return_dtype=pl.Int64,
        )
    )


@pipeline.output(config="config/quote_response/priced.json")
def priced(enriched): ...
```

Every node type except `polars` is configured: its decorator names its settings
and performs the node's work (reading the source, scoring, rating, joining,
assembling the response) when the file runs.  Such a node is a one-line
declaration whose parameters name its inputs and whose body is `...` (or its
docstring).  To add post-processing code to a Data Input, Rating Step, Model
Score, Scenario Expander or Explore node, make its first parameter `df` — the
frame the node produced — and return the result:

```python
@pipeline.model_score(config="config/model_scoring/frequency.json")
def frequency(df: pl.LazyFrame) -> pl.LazyFrame:
    df = df.with_columns(frequency=pl.col("prediction").clip(0, 5))
    return df
```

An External File's code keeps its inputs by name, receives the loaded object
as the keyword-only `obj`, and starts from `df = <first input>`.  Never call
Haute's loader or scoring helpers from a function body, and never import from a
`haute._` module.

This is also the shape produced by `haute init`: its starter pipeline reads
`config/data_input/raw_rows.json`, enriches the frame with a `polars` stage,
and writes the terminal response through
`config/quote_response/priced.json`.  Keep those project-relative sidecar
references when authoring a real project.  The packaged examples use only
self-contained decorators where possible so the parser guard can load them
without inventing project sidecar files.

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
`drop`) over collecting a frame in a node.  A polars node's inputs are its
named parameters (one per incoming edge) and `df` is only its output variable —
`df` is never pre-bound to an input, so start from the input you mean by name
(`df = quotes.filter(...)`) and never read `df` before assigning it.  Polars
frames are immutable: explicit node code must assign the transformed result to
`df` or return the transformed frame.  A bare `quotes.filter(...)` expression
is discarded by the generated wrapper and is therefore invalid, and a polars
node with no code at all raises if the pipeline is run — there is no implicit
passthrough.
Make joins explicit about their keys and join type, and name derived columns so
downstream steps can refer to them without guessing.

A modelling node's `algorithm` (`catboost`, `xgboost`, `lightgbm`, `ebm`, or
`glm`) is fixed when the node is created: to try another family, add a new
node rather than editing `algorithm`.  For the tree and EBM families, put the
loss in `loss_function` and the family's own keys in `params` (`iterations`,
`num_boost_round`, `num_iterations`, or EBM's required `max_rounds`); never set
objectives, threads, seeds, or aliases there.  An EBM never stops early, so give
it an explicit `max_rounds`.  A GLM is different: it has no `loss_function` or
`params`, and is configured by top-level `family`, `link`, `terms` and
`interactions` instead.

## A safe editing pattern

1. Read the saved graph before editing; node ids are the function names.
2. Retrieve the complete capability descriptor for every node type that will
   be added or configured, and follow its ports, wiring rules, closed config
   schema, enums, and anti-patterns.
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
