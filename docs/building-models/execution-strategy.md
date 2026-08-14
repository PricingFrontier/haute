# Execution Strategy

Haute plans how to run a Polars pipeline before it collects data. The plan keeps
work lazy and narrow where that is safe, and makes an expensive or unsupported
step visible rather than quietly changing how your model runs.

## What the strategy means

The strategy shown for a run uses one of these outcomes:

- **Projected**: Haute knows the columns needed downstream and reads only that
  useful subset where the source supports it.
- **Schema all-except**: a node needs every column except a known set, such as
  training features after excluding the target, weight, and metadata columns.
- **Admitted eager**: the operation must collect data in memory, but Haute has
  an available estimate that fits the admitted memory headroom.
- **Streaming boundary**: Haute can continue processing rows in a bounded
  stream, but cannot safely project through this point.
- **Materialisation boundary**: the operation needs a complete in-memory
  result. It is admitted only when its estimate fits the available headroom.
- **Rejected**: Haute will not run the shape for this profile because it cannot
  establish a safe bounded strategy.
- **Not planned**: this surface does not yet use execution planning. It is not
  evidence that an eager or full-width run is safe.

## Column contracts and boundaries

A **column contract** is an explicit promise about which columns a node reads
or produces. Contracts let Haute project earlier sources safely. If code does
not provide a contract that Haute can prove, it retains the columns
conservatively rather than guessing.

Common causes of a boundary are custom Polars code whose column use cannot be
proven, a fan-in whose inputs need different columns, joins (including their
join keys), and operations that require all rows before producing a result.
Move simple column selection and filtering upstream, declare the columns a
custom node needs, or split opaque work into a smaller dedicated branch when a
boundary is unexpectedly costly.

### Group-by operations

Haute never generically chunks a group-by. A group-by is admitted only for the
`preview_eager`, explicit `explore_analysis` cache-materialisation, or `deploy_live`
profile when an available estimate fits the admitted headroom. Otherwise Haute
returns a typed rejection; it does not approximate a partial aggregation or silently
send it through a generic chunk runner. For large grouped work, reduce the input
first, change the operation, or run it in an environment where the admitted estimate
fits.

## Reading diagnostics

The compact profile identifies the strategy. For a boundary or rejection, read
the **blocking node** and **operator** first, then the **estimated cost** and
the proposed **remediation**. For example, an estimate that exceeds headroom
usually calls for narrowing columns, filtering rows earlier, or changing the
execution surface.

The raw details are bounded diagnostic data for support and deeper inspection:
they include the planning profile, reasons, and available estimates without
dumping an unbounded graph or dataframe. A counter that a backend cannot
provide is reported as unavailable or `null`; it is never presented as zero.

## Related guides

- [Polars](nodes/polars.md) for custom transformations and joins.
- [Edge Join](nodes/edge-join.md) for a visible canvas join.
- [Model Training](nodes/model-training.md) for training feature selection.
