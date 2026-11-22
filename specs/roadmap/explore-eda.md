# Explore and EDA roadmap

## Scope

The Explore node's reports, as [the Explore and EDA
specification](../explore-eda/high-level.md) describes them: what the
numbers in a pivot are.

`XPL-03` comes from the review plan's phase 5, a reader from a second
model family (Codex) over the code changed since the 31 October audit. A
worker built each run and the main session ran each again on 14 November
2026.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| XPL-03 | Planned | P2 | A pivot by a nanosecond timestamp shows every group: rows that differ below a microsecond are no longer merged out of the rows while kept in the Total. |

## Planned improvements

### XPL-03 — A pivot by a timestamp keeps rows that differ below a microsecond
**Why:** Reproduced on 14 November 2026 through the editor's routes. A
Parquet file written by pandas holds a timestamp column in nanoseconds,
pandas' default, with values 100 at 09:00:00.000000100, 200 at
09:00:00.000000200 and 300 at 09:00:01. Polars groups them into three. A
pivot with the timestamp as its row field shows two rows, 09:00:00 with
200 and 09:00:01 with 300, and a Total of 600: the 100 is in the total and
in no row. The list of members to filter by offers 09:00:00 twice, and a
pivot filtered to either shows nothing. The values are read into Python
datetimes, which hold microseconds, so the two keys become one and the
second replaces the first.

**Plan:** Key the rows and the members by the column's own value, not a
Python datetime, and show the full fraction.

**Acceptance:** The pivot above shows three rows that sum to its Total,
three members, and each member's filter shows its row.

**Dependencies:** None.

**Evidence:** `src/haute/routes/_pivot_service.py`;
`specs/explore-eda/low-level.md`.
