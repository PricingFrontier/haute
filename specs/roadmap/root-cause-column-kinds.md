# Root cause: column kinds

## Scope

Whether a column's type is a float, an integer or a number, answered by
hand in many places.

This file is one pull request. It holds one package, `IOL-07`, which names
a cause that packages in other files share, and it is taken before them.
The pull request makes the shared mechanism the only path and adds the
test that keeps it so. It then checks each package listed below against
that package's own acceptance, removes the ones it has met, and removes
this file.

## Packages this resolves

"Resolves" means the package's acceptance should be met by this work
alone. "In part" means this work removes the shared cause and the package
keeps something of its own to do.

| Package | Roadmap | What it records | Expected |
|---|---|---|---|
| `RTG-07` | [rating](rating.md) | A Float16 factor's NaN and infinity take the default band | Resolves |
| `XPL-02` | [explore-eda](explore-eda.md) | A Float16 column is a float to the pivot and to the profile | Resolves |
| `OPM-05` | [optimiser](optimiser.md) | A Float16 analysis column is broken down as a number | Resolves |
| `FES-02` | [frontend-shared](frontend-shared.md) | A Float16 column is a number to the editor, as it is to the server | Resolves |

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| IOL-07 | Planned | P2 | Every reader of a column asks one module what kind its type is, and a column of any Polars type is handled as its kind or refused by name. |

## Planned improvements

### IOL-07 — One answer to what kind of column a type is
**Why:** Read on 16 November 2026. An earlier roadmap gave Haute one dtype
vocabulary, `src/haute/_polars_dtypes.py`, and Polars has its own answer to
whether a type is a float, an integer or a number, which `src/haute` calls
48 times in 22 files. Beside both, the question is answered again by hand.
Ten lines in seven files name Float32 and Float64 together as "the floats",
and none of them names Float16. The pivot service, the frame profile, the
Banding statistics, the optimiser's quotes and segments and the impact
report each hold their own list or function. The browser has two more
functions and six such lines in three files. Every list that left Float16
out was found as its own defect in its own area: `RTG-07`, `XPL-02`,
`OPM-05` and `FES-02`. The next type Polars adds will be found the same
way.

**Plan:** One module answers what kind a type is, float, integer, number,
date or time, text, from Polars' own classification, and the browser reads
the same answer from the generated contract. Remove the hand-kept lists and
functions. Add a test that fails when a line outside the module names two
or more float or integer types as a list.

**Acceptance:** One test takes every data type the installed Polars
defines, read from Polars so that an upgrade adds its new types, and sends
a column of each through every reader: the pivot, the profile, the Banding
statistics, a Rating lookup, the optimiser's analysis and the editor's
column lists. Each handles the column as its kind or refuses it by name.
The test for hand-kept lists passes. The acceptance of `RTG-07`, `XPL-02`,
`OPM-05` and `FES-02` is met.

**Dependencies:** None. Deliver before the packages its acceptance names.

**Evidence:** `src/haute/_polars_dtypes.py`;
`src/haute/routes/_pivot_service.py`; `src/haute/_frame_profile.py`;
`src/haute/routes/_optimiser_quotes.py`; `frontend/src/utils/polarsDtypes.ts`.
