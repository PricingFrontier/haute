# Rating roadmap

## Scope

Banding and Rating Step, as [the rating specification](../rating/high-level.md)
describes them: how a rule's or an entry's value is matched to a column's
values, and what a mismatch tells the analyst. The packages below come from
the review plan's sweep of 10 November 2026, which ran every node type on a
fixed set of awkward values, and were reproduced on that day through the
node's preview.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| RTG-01 | Planned | P2 | A categorical Banding rule written `true`, `True` or `1` for a Boolean column matches it or is refused; it no longer sends every row to the default in silence. |
| RTG-02 | Planned | P3 | A Rating Step entry whose key is not a value of its factor's type is refused naming the table, the factor and the key. |

## Planned improvements

### RTG-01 — A Banding rule that can never match its column is not silent
**Why:** Reproduced. A categorical Banding keys each rule by Python's text
for its value and matches it against the column's text as Polars writes it.
For a Boolean column the two differ: a rule written `true` in the settings
file becomes the key `True`, and the column reads `true`. With rules for
`true` and `false` on a Boolean column, every row took the default, the
status was ok and nothing was said. The text `"true"` matched; `"True"` and
`1` did not, again in silence. A Rating Step keys its entries through the
column's own type, which the specification calls one dtype-faithful key
form shared everywhere, and it matched `true`, `"true"` and `1` alike. In
the sweep, Banding rules keyed by integers, decimals, dates and text all
matched; a Boolean is the type where the two forms part. A settings file
written by hand holds `true`, not `"true"`.

**Plan:** Key a categorical rule through the form a Rating Step entry
takes, by the column's type. Refuse a rule whose value is not a value of
that type, naming the factor and the rule, as an unreadable breakpoint is
refused.

**Acceptance:** On a Boolean column, rules written `true` and `false`,
`"true"` and `"false"`, and `1` and `0` each band the rows as the same keys
rate them in a Rating Step. A rule written `"maybe"` on a Boolean column,
or `"abc"` on an integer column, is refused naming the output column and
the rule's value. A test runs one table of column types and spellings
through both nodes and expects the same matches.

**Dependencies:** None.

**Evidence:** `src/haute/_rating.py::banding_categorical_claims` keys a rule
by `str(value)`; `src/haute/_rating.py::normalise_rating_key` is the form to
share; `specs/rating/high-level.md`.

### RTG-02 — A key of the wrong type is refused by table, factor and key
**Why:** Reproduced. A Rating Step entry keyed by text on a factor column
of another type fails in Polars' own words. `"NE"` on an integer column:
"conversion from `str` to `i64` failed in column 'area' for 2 out of 2
values", followed by the expression `col("area").strict_cast(Int64)`. On a
Boolean column, `"True"` included: "incomplete mapping specified for
`replace_strict`. Hint: Pass a `default` value to set unmapped values." On
a datetime column: "could not find an appropriate format to parse dates,
please define a format". None names the table or says that the entry's key
is not a value the column can hold, and the Boolean hint points at a
setting the node does not have. A Banding's refusal of the same kind of
mistake is nearly right: "Banding output 'period' has date breakpoints, but
its column is Int64" names the output and the type and leaves out the
column, which the specification says it names.

**Plan:** Check a table's entry keys against each factor column's type
before the lookup is built. Refuse naming the table's output column, the
factor, the first keys that do not fit and the type the column has. Add
the column's name to the Banding refusal.

**Acceptance:** Each of the three cases above is refused in a message that
names the table, the factor column, a key and the column's type, and holds
no Polars expression or hint. The Banding refusal names the input column as
well as the output.

**Dependencies:** None.

**Evidence:** `src/haute/_rating.py::_rating_key_expr`;
`src/haute/_rating.py::banding_comparison_expr` words the Banding refusal;
`specs/rating/high-level.md`.
