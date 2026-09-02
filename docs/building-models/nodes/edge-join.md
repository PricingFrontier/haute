# Edge Join

An Edge Join is a compact transform node for joining another dataframe into an existing flow. It is useful when you want to enrich the table already travelling along a connection without adding a full Polars code node.

!!! tip "Spreadsheet equivalent"
    Like using a lookup table to add columns to the current sheet, while keeping the main sheet as the dominant input.

!!! info "When to use"
    - Adding external scores, lookup columns, or reference data to an existing dataframe.
    - Keeping a common join visible on the canvas without writing custom Polars code.
    - Joining into a branch that already continues downstream.

## How it works

Create an Edge Join by dragging a connection onto an existing edge, or by connecting the output of one node to the output of another node when there is no edge to drop onto. Haute inserts a small join node and stores the result as normal pipeline code.

While you drag a source connection over a compatible edge, that edge is
highlighted and announced as an Edge Join insertion target before you release
the pointer. Moving away or cancelling removes the feedback without changing
the graph. Invalid targets, including self-joins and cycle-forming edges, are
not presented as valid insertion targets.

The node has two fixed input roles, represented by the target handle on each
incoming edge:

- **Dominant input**: the dataframe already flowing along the original edge.
- **Joining input**: the dataframe being joined in.

The incoming edge handles are `base` and `join`. Use the swap button in the
editor if the roles need to be reversed; it swaps those edge handles. The
stored config contains only join semantics, so the graph remains the source of
truth for input roles.

## Canvas handles

The compact marker has a **base input on the left**, a **join input above or
below**, and one **output on the right**. The join handle follows the connected
source: it sits above the marker when that source is above it and below when
the source is below it. Before the joining input is connected, both the top and
bottom join-handle candidates are available; they represent the same stored
`join` target handle.

Input names shown in the editor come from executable edge identity: Haute uses
the API frame handle (for example, `quote_info`) when one exists; otherwise it
uses a submodel output's canonical `alias__port` name or a sanitized ordinary
source label. Names are not derived from internal node IDs such as
`Quote_Input_1`.

## A common canvas join

For example, connect `policies` to the **base** target handle and
`competitor_scores` to the **join** target handle, then configure a left join on
`quote_id`:

```json
{
  "how": "left",
  "on": ["quote_id"]
}
```

The projection planner keeps the columns needed by downstream nodes **and**
the join keys. Do not remove or rename a join key before this node. If a custom
branch has an uncertain column contract, the join can become a conservative
execution boundary; see [Execution Strategy](../execution-strategy.md) for how
to read that result.

| Config | Description |
|---|---|
| `how` | Join type: `"inner"`, `"left"`, `"right"`, `"full"`, `"semi"`, `"anti"`, or `"cross"`. |
| `on` | Same-name join keys, for example `["quote_id"]`. |
| `leftOn` / `rightOn` | Paired join keys when the two dataframes use different column names. |
| `suffix` | Suffix Polars applies to duplicate right-side columns. Defaults to `"_right"`. |
| `coalesce` | Polars option for combining join key columns where supported by the join type. |
| `validate` | Polars cardinality validation, such as `"1:1"` or `"m:1"`. |
| `maintainOrder` | Polars option for preserving selected input order where supported. |

## Join keys

Supported join types are `inner`, `left`, `right`, `full`, `semi`, `anti`, and
`cross`.

Use `on` when both dataframes share the same key names:

```json
{
  "how": "left",
  "on": ["quote_id"]
}
```

Use `leftOn` and `rightOn` when the key names differ:

```json
{
  "how": "left",
  "leftOn": ["quote_id"],
  "rightOn": ["id"]
}
```

Cross joins do not use keys: `on`, `leftOn`, and `rightOn` must all be absent.
Every other join type requires either a non-empty `on` value or both
`leftOn` and `rightOn` with the same non-zero number of keys. Do not combine
`on` with `leftOn`/`rightOn`.

For bespoke joins, expression-based keys, or custom post-processing, use a
normal [Polars](polars.md) node before or after the Edge Join.

## Source code

An Edge Join is still a regular Python node. When saved from the UI, Haute emits an `@pipeline.edge_join` decorator and ordinary `pipeline.connect(...)` calls with role handles:

```python
@pipeline.edge_join(
    how="left",
    on=["quote_id"],
    suffix="_right",
)
def join_scores(
    policies: pl.LazyFrame,
    competitor_scores: pl.LazyFrame,
) -> pl.LazyFrame:
    return pipeline._apply_edge_join("join_scores", policies, competitor_scores)


pipeline.connect("policies", "join_scores", target_port="base")
pipeline.connect("competitor_scores", "join_scores", target_port="join")
```

The role handles are important: `base` and `join` tell the parser, executor,
preview, and code generator which incoming dataframe has which role. Generated
or authored decorator calls do not accept `base_input` or `join_input`.

## Errors

The node fails loudly when the shape is invalid. Common examples are missing
`base` or `join` edge handles, duplicate role handles, non-cross joins without
keys, Polars schema errors such as missing join columns, or any supplied
`baseInput`, `joinInput`, `base_input`, or `join_input` role configuration or
argument. Those removed role fields and arguments have no compatibility path
and are rejected.

**See also:** [Polars](polars.md) for custom dataframe logic and
[Execution Strategy](../execution-strategy.md) for planning diagnostics.
