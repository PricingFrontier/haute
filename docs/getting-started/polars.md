# Polars

Haute uses [Polars](https://pola.rs/) as its data engine. If you've worked with data in Excel, SAS, Emblem, or any other pricing platform, Polars fills the same role - it's the thing that holds your data and does the calculations. The difference is that it's open source, extremely fast, and designed for modern hardware.

---

## What Polars actually is

Polars is a dataframe library. A dataframe is a table - rows and columns, like a spreadsheet. When Haute loads your data, transforms it, joins it, filters it, or scores it through a model, Polars is doing that work underneath.

You don't need to write Polars code to use Haute. The visual editor handles that: you build each transformation from steps named in plain English, and Haute turns them into Polars code. When you look at the generated code beside the steps, or at the generated Python file, the expressions you see are Polars expressions. Understanding the basics helps you read what's happening, even if you never write it from scratch.

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

The syntax is different. The concepts are the same. Haute's visual editor means you rarely need to write these expressions yourself, but when you see them in the generated code, this is what they mean.

---

## Memory and large datasets

Polars processes data in chunks when working with large files, so it doesn't need to load everything into memory at once. Haute's batch execution uses this streaming mode automatically when it writes to a file format that supports it; a database output, or a file format that can only be written in one go, loads the result into memory before writing it. At a few points in the pipeline - for example where a node feeds a join or several other nodes - Haute writes the intermediate result to its shared snapshot store and continues from there. Together, this means you can process datasets that are larger than your machine's available memory. The number of rows in each chunk is the **Chunk rows** setting in Pipeline settings (500,000 by default); lower it if a wide dataset runs out of memory.

For preview, Haute caches each node's output based on a fingerprint of your pipeline's structure and configuration. Click between nodes and the data appears instantly - it's already been calculated. Change a node's configuration and the cache refreshes on the next run, but only the work needed for your current view is re-executed.

---

## Steps and code in nodes

Most of the time, you don't write Polars code at all. The Polars node, and the **Polars** tab of the Data Input, Load File, Rating Step, Model Scoring and Expander nodes (the **Polars Code** pane on an Explore node), build their transformation from **steps** named in plain English: **Filter rows**, **Add column**, **Keep columns**, **Drop columns**, **Rename columns**, **Change types**, **Sort rows**, **Remove duplicates**, **Group and aggregate**, **Join another input**, **Append inputs**, **Fill missing values**, **Limit rows**, **Define variable**, **Pivot to columns** and **Unpivot to rows**. Each step becomes Polars code, and the **Generated code** panel under the steps shows the code they produce. You can search the **Add step** menu by a step's name or by its Polars method - typing `with_columns` finds **Add column**.

When no step does what you need, add a **Free code** step and write Python statements that assign their result to `df`, the current table:

```python
df = df.filter(pl.col("vehicle_age") < 20).with_columns(
    (pl.col("base_premium") * pl.col("area_factor")).alias("adjusted_premium")
)
```

**Switch to code** turns a node's steps into editable code for good: the steps are removed and the generated code becomes editable, and there is no way back to steps. Code always assigns its result to `df`; do not end it with `return`.

For more involved logic, you can write your own Python functions. `haute init` creates a `rating/utility/` folder for them, and the toolbar's **Utility** panel lets you edit its files or create new ones. A utility's functions are available in your code once its import is listed in the toolbar's **Imports** panel, for example `from utility.features import *`. A file you create in the Utility panel gets its import line added automatically; the starter `features.py` does not, so add its import yourself.

---

## How rating tables and banding work

Two of the most common operations in pricing - rating table lookups and banding - are handled by dedicated node types. Both use Polars under the hood, but you configure them through the visual editor rather than writing code.

**Rating tables** work like VLOOKUP. You define a table of factors and values, and Haute joins it to your data on the factor columns. The join is a standard Polars left join - every row in your data gets matched to the corresponding value in the lookup table. Rows that don't match take the table's default value (1.0 for a new table, unless you change it); if you clear the default, a row with no match stops the run with an error that names the missing keys. The lookup table is validated before the join runs: entries with NaN or infinite values are rejected, because a silent bad value in a rating table can corrupt an entire book of prices.

**Banding** maps numeric, date or categorical values into groups. **Numeric** banding (for age, sum insured or a policy start date) uses breakpoints: each has an upper boundary and a band name, and the boundaries are numbers, dates (`YYYY-MM-DD`) or dates and times - one kind per factor. **Generate even bands** fills in evenly spaced breakpoints for you, in calendar steps for a date column. **Categorical** banding (for vehicle type) maps exact values to groups. Under the hood, numeric banding builds a chain of conditional expressions - the Polars equivalent of nested IF statements - and categorical banding uses strict value replacement. Both produce a new column with the banded result.

---

## Price tracing

When you click a cell in a preview and trace it, Haute explains that value from the same execution the preview used. It finds the clicked row in each upstream node's output and shows what happened to it at every step, so the trace always shows the data you see in the preview. It is not a separate calculation for one row.

The trace panel opens on the clicked node's card and hides steps that only pass the row through; you can reveal them. Each card shows what its step did - the columns it added or changed and the formula evaluated on your row - with extra detail for Banding, Rating Step, Model Scoring, Apply Optimisation, Expander and Source Switch nodes. Derivation rows show how each input the value depends on was calculated. A chain of multiplications and additions is drawn as a waterfall, and a GLM or CatBoost model shows a ladder of contributions up to its linear predictor or raw score, then the prediction. Every card and derivation row links to its step and to its node on the canvas.

The first trace runs through the pipeline and caches the result. Every trace after that on the same pipeline pulls from cache - click a different row, a different column, and the answer appears instantly. The cache is keyed to your pipeline's structure, so it refreshes automatically when you change something.

---

## Memory estimation

Before training a model on a large dataset, Haute reads the file's metadata - row count, column count, file size - without loading any data. It uses this to estimate how much memory the full training run will need, accounting for the overhead of model training, intermediate joins, and data duplication.

If the estimate exceeds your machine's available memory, Haute trains on as many rows as fit and tells you it did ("Dataset downsampled to ... rows to fit in available RAM"); a smaller row limit you set yourself still wins. When the source sizes cannot be read from metadata, Haute reports the estimate as unavailable rather than guessing. This works on Windows, macOS, and Linux, and checks GPU memory as well if you're training on a GPU.
