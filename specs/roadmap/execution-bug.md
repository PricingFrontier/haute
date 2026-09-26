# Execution bug roadmap

## Scope

The execution engine's node output boundary, where a node's own column shaping
(`selected_columns` and `column_renames`) meets that node's output column contract.
Current behaviour is specified in the
[execution engine low-level specification](../execution-engine/low-level.md), in its
display-walk description and its paragraph on `selected_columns`. Contract derivation for
individual node types, the input-side contract check, and the parse-time `contract=`
annotation are out of scope except where the chosen fix must keep them consistent.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| EXB-01 | Decision | P2 | A node can deselect or rename a column it creates without failing its own output contract. |

## Planned improvements

### EXB-01 — A node's own column shaping no longer fails its own output contract
**Why:** Any node that deselects or renames a column it creates fails at run time with
an error that says the opposite of what happened.

*How it was found.* Migrating the `motor-pricing-demo` project to `main` (`c1428a1c`),
every run of its pipeline stops at the Optimiser Apply node `apply_optimiser`, whose
settings keep `selected_columns: ["quote_id", "optimal_premium"]`:

```
ContractColumnsMissingError: 'apply_optimiser' did not create the column
'__optimiser_version__', which its contract says it outputs.
```

The node did create the column; the author deselected it. haute as it stood before the
node-declaration format change (`3320c15e`) fails identically on the same pipeline, so
this is long-standing, not a regression of that change.

*Mechanism.* The walker's `_shape_output` takes the frame the node's builder returned,
applies the node's own `selected_columns` and then `column_renames`, and only then calls
`_check_output`, which asserts the node's output contract against the shaped columns.
The contract is the node type's builder contract: for Optimiser Apply,
`_optimiser_apply_columns` promises `version_column` (default `__optimiser_version__`) and
any `optimised_value_column` whenever an artifact source is configured. That contract
describes what the builder creates, while the check sees what is left after the author's
shaping. `_assert_outputs_satisfy_contract` requires every promised column to be present,
so a created column the author deselected or renamed is reported as never created.

*It is general.* Nothing here is specific to Optimiser Apply. A minimal pipeline on
`main`, a Polars source of `quote_id` and `premium` feeding a Scenario Expander (whose
builder contract promises `price_adjustment` and `scenario_index`), gives:

| Scenario Expander settings | Result |
|---|---|
| no shaping | runs; `quote_id`, `premium`, `scenario_index`, `price_adjustment` |
| `selected_columns` omits `price_adjustment` | `ContractColumnsMissingError`: 'grid' did not create the column 'price_adjustment' |
| `column_renames` maps `price_adjustment` to `adj` | the same error, naming `price_adjustment` |
| `selected_columns` omits only the input column `premium` | runs; `quote_id`, `scenario_index`, `price_adjustment` |

Every node type whose builder contract names the columns it creates is exposed: Model
Score's output column, Scenario Expander's step and value columns, Optimiser Apply's
version and optimised-value columns, and any other type that declares produced columns.

*Reach.* The Columns tab lets an author untick any output column of any node, and it
writes `selected_columns`, so this is reachable in ordinary editor use: unticking a
column a node creates breaks that node. `_shape_output` is the only caller of the output
check, and every execution goes through the shared walker, so the editor preview,
`haute run`, output sinks and deployed scoring (`execute_lazy_graph` uses the same
walker) fail the same way. Preview and `haute run` were observed; the sink and scorer
paths are inferred from the shared walker and were not run. A contract mismatch is
re-raised even while a preview records per-node failures, so it aborts the whole run
rather than marking one node.

*Specification gap.* The execution engine specification fixes the order (call the node,
apply `selected_columns`/`column_renames`, check output columns against the contract).
Separately, it calls `selected_columns` the single shared post-call filter, says a
column the code creates can be selected, and says a stale selection is simply absent
rather than fatal. It never says what happens when the author's own selection or renames
remove a column the contract promises: the order it gives makes that fatal, and the
error message then states something false.

*Workaround until fixed.* Keep every created column selected. For the demo, adding
`__optimiser_version__` to `apply_optimiser`'s `selected_columns` makes the pipeline run
end to end on `main`: all 22 nodes for the `live` source (with the 10k quote file) and
all 22 for `nb_batch` (with `HAUTE_PREVIEW_MEMORY_LIMIT_MB=24000`, since the full batch
expands to 1.1 million scenario rows). The workaround adds a column the author did not
want, which is why the bug needs fixing rather than documenting.

**Plan:** Decide what a node's output contract describes, then make the one check agree.

- *Option A (recommended): the contract describes what the node's builder creates.*
  Check it against the builder's frame, before the node's own selection and renames;
  downstream nodes keep checking their inputs against the shaped frame they receive.
  `_shape_output` already holds the unshaped frame (it keeps it in `unshaped_frames` for
  every node that shapes), so the change is local: when `_shapes_output` is true, assert
  the contract against the unshaped frame's columns (one schema resolution, only for
  nodes that shape) and keep describing, caching and passing on the shaped columns as
  now. A consumer that needs a deselected column then fails at the consumer with the
  input-side message ("needs the column 'x', which is not in its input"), which names the
  node that actually lacks the column. The parse-time annotation and codegen's
  `contract=` already describe builder output, so they stay consistent without change.
- *Option B: the contract describes the node's shaped output.* Narrow the builder's
  promised columns by the node's selection and map them through its renames before
  checking. The contract then equals what downstream sees, but the same narrowing must be
  applied wherever contracts are derived or compared (the parse-time annotation check,
  codegen's `contract=` annotations, projection planning) to stay consistent: a wider
  change for the same user-visible fix.

Either way the check itself stays: a builder that fails to create a column it promised
must still fail, and no fallback may skip the check for nodes that shape their output.
The execution engine low-level specification is updated first: the display-walk order,
and the `selected_columns` paragraph gains that a node may deselect or rename columns it
creates.

**Acceptance:**
- A walker regression, for both display and lazy walks: a Scenario Expander whose
  `selected_columns` omits `price_adjustment` executes and its output lacks the column;
  with `column_renames` mapping it to `adj`, the output has `adj`.
- A consumer that needs the deselected column fails with the input-side
  `ContractColumnsMissingError` naming the consumer.
- A builder that does not create a promised column still fails with the output-side
  message.
- `motor-pricing-demo` runs both sources end to end on `main` with `apply_optimiser`'s
  selection unchanged (`quote_id`, `optimal_premium`).
- The execution engine specification is updated and `tests/test_docs_accuracy.py` passes.

**Dependencies:** The choice between options A and B.

**Evidence:** `src/haute/_graph_walker.py::_Walk._shape_output` (shapes, then checks); `src/haute/_graph_walker.py::_Walk._check_output`; `src/haute/_execute_lazy.py::_assert_outputs_satisfy_contract`; `src/haute/_execute_lazy.py::_apply_selected_columns`; `src/haute/_execute_lazy.py::_shapes_output`; `src/haute/_builders.py::_optimiser_apply_columns`; `src/haute/_builders.py::_scenario_expander_columns`; `src/haute/execution.py::execute_lazy_graph` (the deploy scorer's entry into the same walker).
