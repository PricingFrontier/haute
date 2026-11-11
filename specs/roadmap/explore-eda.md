# Explore and EDA roadmap

## Scope

The Explore node's reports, as [the Explore and EDA
specification](../explore-eda/high-level.md) describes them: what the
numbers in a pivot are. The package below comes from the review plan's sweep
of 11 November 2026 that computed answers a second way: every cell of
fourteen pivots was compared with plain Polars, and the integer sums with
Python's own integers. The main session ran it again on another file.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| XPL-01 | Planned | P1 | A pivot's Sum of an integer column is the sum of its rows whatever the column's width: a total past a 32-bit or 64-bit integer's range is shown in full, not wrapped round. |

## Planned improvements

### XPL-01 — A pivot's Sum of an integer column is the sum of its rows
**Why:** Reproduced on 11 November 2026 through the routes. An Explore
pivot was run over 30,000 policies whose premium is held in pence as a
32-bit integer, as a Parquet file written from a database or by Spark often
holds it: Rows by region, Values the Sum of `premium_pence`.

| Region | Sum shown | The rows sum to |
|---|---:|---:|
| East | 757,091,436 | 757,091,436 |
| North | 744,257,050 | 744,257,050 |
| South | 745,334,779 | 745,334,779 |
| West | 754,164,136 | 754,164,136 |
| Grand total | -1,294,119,895 | 3,000,847,401 |

Each region is right and the Grand total is a negative number: the four
sums on the screen add up to 3,000,847,401. The result carries no warning.
The Average on the same row is right, and so is the Sum of a 16-bit column
beside it. A 32-bit integer holds at most 2,147,483,647, about 21 million
pounds in pence, and a sum past it wraps round. The sweep saw the same for
an unsigned 32-bit column, 1,704,124,416 shown for 1,200,000,000,000, and
for a 64-bit one, a negative total for a sum past its range.

The pivot sums a column in the column's own type. Polars widens 8-bit and
16-bit integers before it sums them and leaves the wider ones as they are,
so plain Polars gives the same wrong total, and a comparison with Polars
passes it; the sweep caught it against Python's integers. The pivot is
Haute's own arithmetic, and its specification takes care of the other end
of the same number: it "renders integers beyond JavaScript's exact range
(|value| > 2^53 − 1) as canonical decimal strings so a JS client cannot
silently round them".

**Plan:** Sum an integer column in a type its total cannot leave: 64 bits
for the columns narrower than that, and 128 bits for a 64-bit column, shown
through the decimal string the specification already gives a large integer.
Where a wider type cannot be had, refuse the Value naming the column rather
than show a wrapped number.

**Acceptance:** A test builds a pivot over a 32-bit column, an unsigned
32-bit column and a 64-bit column whose totals each pass the column's
range. Every cell and grand total equals the sum of the rows as Python's
integers give it, the 64-bit one as a decimal string.

**Dependencies:** None.

**Evidence:** `src/haute/routes/_pivot_service.py::_aggregation_expression`;
`src/haute/routes/_pivot_service.py::_normalise_cell`;
`specs/explore-eda/low-level.md`.
