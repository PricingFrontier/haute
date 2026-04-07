# Polars

Haute uses [Polars](https://pola.rs/) as its data engine. If you've worked with data in Excel, SAS, Emblem, or any other pricing platform, Polars fills the same role - it's the thing that holds your data and does the calculations. The difference is that it's open source, extremely fast, and designed for modern hardware.

---

## What Polars actually is

Polars is a dataframe library. A dataframe is a table - rows and columns, like a spreadsheet. When Haute loads your data, transforms it, joins it, filters it, or scores it through a model, Polars is doing that work underneath.

You don't need to write Polars code to use Haute. The visual editor handles that. But when you open a code node or look at the generated Python file, the expressions you see are Polars expressions. Understanding the basics helps you read what's happening, even if you never write it from scratch.

---

## Why Polars

Polars processes entire columns at once. When you apply a rating factor to a million rows, it doesn't loop through them one at a time. It applies the operation to the whole column in a single pass, using all your CPU cores in parallel.

This is why previewing data at any node in Haute feels instant. It's not a trick of the interface - the engine underneath is genuinely that fast.

Polars is also strict about types. A column of ages is always integers. A column of premiums is always decimals. A text value in a numeric column, a date formatted as a string, a missing value treated as zero - Polars catches these rather than silently allowing them through. In pricing work, where a subtle data error can propagate through an entire rating structure, this strictness is a feature.

---

## Lazy evaluation

When you build a pipeline in Haute, the transforms don't execute immediately. Instead, Polars builds a plan - a description of everything that needs to happen. It then optimises that plan before running it.

If your pipeline selects ten columns but only three are used downstream, Polars drops the other seven before it even reads them. If you filter rows early and join later, Polars pushes that filter as far upstream as possible so it processes less data at every step.

This is called lazy evaluation. You describe what you want; Polars figures out the fastest way to get there.

In practice, this means Haute's batch execution - processing a full dataset end to end - is significantly faster than running each step individually. The engine sees the whole pipeline and optimises it as a single unit, rather than treating each node as an isolated calculation.

You don't need to think about this when using Haute. It happens automatically. But it explains why batch runs are fast even on large datasets - the engine is doing less work, not more.

---

## Nothing is mutated

Every transform in the pipeline produces a new table. The input is never changed.

This sounds like a technical detail, but it has a practical consequence that matters: you can click any node and be confident you're seeing exactly what that node produced, unaffected by anything that happened after it.

In tools where data is modified in place, tracing a calculation backwards means mentally undoing every step. In Haute, each node's output is its own snapshot. Click it and you see it.

---

## How this connects to what you already know

If you're used to building rating structures in proprietary software, most of the concepts translate directly:

| What you know | What Polars calls it |
|---|---|
| A table or worksheet | A DataFrame |
| Filtering rows | `.filter()` |
| Adding or changing a column | `.with_columns()` |
| A lookup table / VLOOKUP | A join (`.join()`) |
| Sorting | `.sort()` |
| Selecting specific columns | `.select()` |
| Grouping and summarising | `.group_by().agg()` |

The syntax is different. The concepts are the same. Haute's visual editor means you rarely need to write these expressions yourself, but when you see them in the generated code or in a code node, this is what they mean.

---

## Memory and large datasets

Polars processes data in chunks when working with large files, so it doesn't need to load everything into memory at once. Haute's batch execution uses this streaming mode automatically. Combined with intermediate checkpoints - where Haute writes partial results to disk at strategic points in the pipeline - this means you can process datasets that are larger than your machine's available memory.

For preview, Haute caches each node's output based on a fingerprint of your pipeline's structure and configuration. Click between nodes and the data appears instantly - it's already been calculated. Change a node's configuration and the cache refreshes on the next run, but only the work needed for your current view is re-executed.

---

## Writing code in nodes

Most of the time, the visual editor writes the Polars code for you. But when you open a code node, you're writing Polars expressions directly.

Haute supports a shorthand that makes this easier. Instead of writing a full program, you can start with a `.` and chain operations directly:

```python
.filter(pl.col("vehicle_age") < 20)
.with_columns(
    (pl.col("base_premium") * pl.col("area_factor")).alias("adjusted_premium")
)
```

Haute wraps this around your input data automatically. You don't need to assign variables or write boilerplate - just describe the transformation.

For more involved logic, you can write full Python. Your project's `utility/` folder contains helper functions that are available in every code node without needing to import them. These are plain Python functions you can read, modify, and extend.

---

## How rating tables and banding work

Two of the most common operations in pricing - rating table lookups and banding - are handled by dedicated node types. Both use Polars under the hood, but you configure them through the visual editor rather than writing code.

**Rating tables** work like VLOOKUP. You define a table of factors and values, and Haute joins it to your data on the factor columns. The join is a standard Polars left join - every row in your data gets matched to the corresponding value in the lookup table. Rows that don't match get a default value. The lookup table is validated before the join runs: entries with NaN or infinite values are rejected, because a silent bad value in a rating table can corrupt an entire book of prices.

**Banding** maps continuous or categorical values into groups. For continuous variables (like age or sum insured), you define ranges with operators and boundaries. For categorical variables (like vehicle type), you define exact value mappings. Under the hood, continuous banding builds a chain of conditional expressions - the Polars equivalent of nested IF statements - and categorical banding uses strict value replacement. Both produce a new column with the banded result.

---

## Price tracing

When you click a cell in your output and trace it, Haute runs the full pipeline for that single row and records what happened at every node along the way. This is a separate execution path from preview or batch - it's optimised for showing you the journey of one value through the pipeline.

The first trace runs through the pipeline and caches the result. Every trace after that on the same pipeline pulls from cache - click a different row, a different column, and the answer appears instantly. The cache is keyed to your pipeline's structure, so it refreshes automatically when you change something.

---

## Memory estimation

Before training a model on a large dataset, Haute reads the file's metadata - row count, column count, file size - without loading any data. It uses this to estimate how much memory the full training run will need, accounting for the overhead of model training, intermediate joins, and data duplication.

If the estimate exceeds your machine's available memory, Haute tells you before you start and suggests a safe dataset size. This works on Windows, macOS, and Linux, and checks GPU memory as well if you're training on a GPU.
