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

The node has two fixed input roles:

- **Dominant input**: the dataframe already flowing along the original edge.
- **Joining input**: the dataframe being joined in.

Use the swap button in the editor if the roles need to be reversed. The canvas handles and the saved config move together, so the code and UI stay in sync.

## Canvas handles

The compact marker has a **base input on the left**, a **join input above or
below**, and one **output on the right**. The join handle follows the connected
source: it sits above the marker when that source is above it and below when
the source is below it. Before the joining input is connected, both the top and
bottom join-handle candidates are available; they represent the same stored
`join` role.

Swapping inputs exchanges the incoming `base` and `join` handles and updates
`baseInput` and `joinInput` in the stored config as one edit. The displayed
geometry, generated code, preview, and saved graph therefore continue to agree
about which dataframe has each role.

## A common canvas join

For example, connect `policies` to the **Dominant input** and
`competitor_scores` to the **Joining input**, then configure a left join on
`quote_id`. The canvas remains explicit about which table continues downstream:

```json
{
  "baseInput": "policies",
  "joinInput": "competitor_scores",
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
| `baseInput` | The dominant input node id. Set from the canvas connection. |
| `joinInput` | The joining input node id. Set from the canvas connection. |
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
    base_input="policies",
    join_input="competitor_scores",
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

The role handles are important: `base` and `join` tell the parser, executor, preview, and code generator which incoming dataframe has which role.

## Errors

The node fails loudly when the shape is invalid. Common examples are missing role connections, stale `baseInput` or `joinInput` values, duplicate role handles, non-cross joins without keys, and Polars schema errors such as missing join columns.

**See also:** [Polars](polars.md) for custom dataframe logic and
[Execution Strategy](../execution-strategy.md) for planning diagnostics.
