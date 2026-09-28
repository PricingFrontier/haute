# Edge Join

An Edge Join is a compact node for joining another dataframe into an existing flow. It is useful when you want to enrich the table already travelling along a connection without adding a Polars node.

!!! info "When to use"
    - Adding external scores, lookup columns, or reference data to an existing dataframe.
    - Keeping a common join visible on the canvas without writing custom Polars code.
    - Joining into a branch that already continues downstream.

An Edge Join takes exactly two inputs - the dataframe already flowing along the connection, and the one being joined in - and has one output, the joined dataframe. The panel has two tabs: **CONFIG** and **COLUMNS**.

## How it works

Create an Edge Join by dragging a connection onto an existing edge, or by connecting the output of one node to the output of another node when there is no edge to drop onto. Haute inserts a small join node on the canvas.

While you drag a source connection over a compatible edge, that edge is highlighted and announced as an Edge Join insertion target before you release the pointer. Moving away or cancelling removes the feedback without changing the graph. Invalid targets, including self-joins and cycle-forming edges, are not presented as valid insertion targets.

The node has two fixed input roles:

- **Dominant input**: the dataframe already flowing along the original edge.
- **Joining input**: the dataframe being joined in.

## Canvas handles

The compact marker has a **base input on the left**, a **join input above or below**, and one **output on the right**. The join handle follows the connected source: it sits above the marker when that source is above it and below when the source is below it. Before the joining input is connected, both the top and bottom join-handle candidates are available; either one connects the joining input.

## The CONFIG tab

If the node's settings or connections cannot work, a red box at the top, **EDGE JOIN CONFIG NEEDS ATTENTION**, lists what to fix, for example "Connect exactly one input to the join handle.", "Non-cross joins need join keys." or "Base key policy_ref is not in the current upstream columns."

### INPUT ROLES

| Control | What it does |
|---|---|
| **DOMINANT INPUT** | The input on the base handle, by name, or "Not connected". Its bin removes that connection. |
| **JOINING INPUT** | The input on the join handle, by name, or "Not connected". Its bin removes that connection. |
| **Swap** | Swaps the two inputs' roles. It is available once each role has exactly one input. |

The names are the ones the node's code uses for its inputs: a Quote Input table's label (for example, `quote_info`), a submodel output's `alias__port` name, or the upstream node's name - never an internal node ID such as `Quote_Input_1`.

### Join settings

| Field | What it does |
|---|---|
| **JOIN TYPE** | How rows are matched: **Left** (the default), **Inner**, **Full**, **Right**, **Semi**, **Anti** or **Cross**. Choosing **Cross** clears the join keys, because a cross join pairs every row with every row. |
| Key mode | **Same-name keys** when both dataframes use the same key names, or **Paired base/join keys** when they differ. Hidden for **Cross**. Switching mode carries your keys across where they fit. |
| **SAME-NAME KEY 1**, 2, … | The key columns, chosen from the columns both inputs share. **Add same-name key** adds another; the bin removes one. When both inputs are first connected, the first shared column is filled in for you. |
| **BASE KEY 1** and **JOIN KEY 1**, … | Paired keys: a column of the dominant input and the column of the joining input it must equal. **Add key pair** adds another pair. |
| **SUFFIX** | Added to a joining-input column whose name clashes with a dominant-input column. Defaults to `_right`. |
| **COALESCE** | Polars' option for combining the key columns where the join type supports it: **Not set**, **True** or **False**. |
| **VALIDATE** | Polars' cardinality check: **Not set**, **1:1**, **1:m**, **m:1** or **m:m**. The run fails when the data breaks it. |
| **MAINTAIN ORDER** | Polars' option for keeping the input order where supported: **Not set**, **None**, **Left**, **Right**, **Left then right** or **Right then left**. |

Supported join types are `inner`, `left`, `right`, `full`, `semi`, `anti`, and `cross`.

A key box lists columns once the inputs have been previewed, and is a text box before that. A key that is empty, or no longer among the input's columns ("Missing column (…)"), is outlined in red.

For bespoke joins, expression-based keys, or custom post-processing, use a normal [Polars](polars.md) node before or after the Edge Join.

## The COLUMNS tab

The **COLUMNS** tab chooses which of the joined columns the node passes on (see [Working with any node](index.md#working-with-any-node)).

## Example

To add competitor scores to your policies by `quote_id`:

1. Drag a connection from the `competitor_scores` node onto the connection that leaves `policies`. Haute inserts an Edge Join with `policies` as the **DOMINANT INPUT** and `competitor_scores` as the **JOINING INPUT**.
2. Leave **JOIN TYPE** on **Left**.
3. Under **Same-name keys**, check that **SAME-NAME KEY 1** is `quote_id`.

If the scores table calls the key `id` instead, choose **Paired base/join keys** and set **BASE KEY 1** to `quote_id` and **JOIN KEY 1** to `id`.

!!! note "Keep the join keys"
    The projection planner keeps the columns needed by downstream nodes **and** the join keys. Do not remove or rename a join key before this node. If a custom branch has an uncertain column contract, the join can become a conservative execution boundary; see [Execution Strategy](../execution-strategy.md) for how to read that result.

!!! warning "Errors"
    The node fails loudly when its shape is invalid: a missing dominant or joining input, two inputs in the same role, a join other than **Cross** without keys, or a Polars schema error such as a missing join column.

??? note "In the pipeline file"
    An Edge Join has no JSON sidecar. Its settings are arguments of the `@pipeline.edge_join` decorator in the pipeline's `.py` file, on a one-line declaration, and its inputs are ordinary `pipeline.connect(...)` calls whose `target_port` gives each input's role. The decorator performs the join when the file runs on its own:

    ```python
    @pipeline.edge_join(how="left", on=["quote_id"], suffix="_right")
    def join_scores(policies, competitor_scores): ...


    pipeline.connect("policies", "join_scores", target_port="base")
    pipeline.connect("competitor_scores", "join_scores", target_port="join")
    ```

    The role handles are important: `base` and `join` tell the parser, executor, preview, and code generator which incoming dataframe has which role. The graph is the source of truth for input roles, so the settings contain only the join itself.

    | Setting in the editor | Stored as |
    |---|---|
    | **DOMINANT INPUT** / **JOINING INPUT** | the `target_port` of each connection: `"base"` or `"join"` |
    | **JOIN TYPE** | `how`: `"inner"`, `"left"`, `"right"`, `"full"`, `"semi"`, `"anti"` or `"cross"` |
    | **SAME-NAME KEY** | `on`: the same-name join keys, for example `["quote_id"]` |
    | **BASE KEY** / **JOIN KEY** | `leftOn` / `rightOn`, written `left_on` / `right_on` in the decorator |
    | **SUFFIX** | `suffix` (defaults to `"_right"`) |
    | **COALESCE** | `coalesce`: `true` or `false` (absent when not set) |
    | **VALIDATE** | `validate`: `"1:1"`, `"1:m"`, `"m:1"` or `"m:m"` (absent when not set) |
    | **MAINTAIN ORDER** | `maintainOrder`, written `maintain_order` in the decorator: `"none"`, `"left"`, `"right"`, `"left_right"` or `"right_left"` |
    | **COLUMNS** tab | `selected_columns` |

    In the node's settings, same-name keys look like this:

    ```json
    {
      "how": "left",
      "on": ["quote_id"]
    }
    ```

    and paired keys like this:

    ```json
    {
      "how": "left",
      "leftOn": ["quote_id"],
      "rightOn": ["id"]
    }
    ```

    Cross joins do not use keys: `on`, `leftOn`, and `rightOn` must all be absent. Every other join type requires either a non-empty `on` value or both `leftOn` and `rightOn` with the same non-zero number of keys. Do not combine `on` with `leftOn`/`rightOn`.

    Input roles live only on the connections. A `baseInput`, `joinInput`, `base_input` or `join_input` setting or decorator argument is rejected: those removed role fields have no compatibility path.

**See also:** [Polars](polars.md) for custom dataframe logic and
[Execution Strategy](../execution-strategy.md) for planning diagnostics.
