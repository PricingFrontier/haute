# Root cause: the quote store check

## Scope

A quote store that differs from what Haute expects and still passes the
check.

This file is one pull request. It holds one package, `WB-75`, which names
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
| `WB-72` | [workbench](workbench.md) | The store check reads what a foreign key refers to | Resolves |
| `WB-73` | [workbench](workbench.md) | The store check requires the unique operation key | Resolves |
| `WB-64` | [workbench](workbench.md) | The migration SQL for SQL Server leaves a column's NOT NULL as it says it does | In part |
| `WB-65` | [workbench](workbench.md) | A single-precision column is named as a difference for a Decimal field | In part |

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| WB-75 | Planned | P2 | Every column, key and constraint Haute's own SQL creates is one the store check reports when it is missing or altered. |

## Planned improvements

### WB-75 — The store check is proven against every line of Haute's own SQL
**Why:** Read on 16 November 2026. The check that a quote store matches
what Haute expects compares the database's catalogue with the expected
tables in code written by hand: columns, primary keys, and each foreign
key's table and constrained columns. Whatever that code does not read, a
store can lack and still pass, and each such gap has been found one at a
time: a missing unique constraint (`WB-73`), a foreign key's referred
columns and schema (`WB-72`), a NOT NULL the migration SQL does not leave
as it says (`WB-64`), and a column's precision (`WB-65`). An earlier
roadmap delivered the store on PostgreSQL and SQL Server and its changes
as SQL; nothing ties what that SQL creates to what the check reads.

**Plan:** A test takes Haute's own SQL for each database and, for every
column, type, nullability, default, key and constraint it creates, makes a
store with that one thing removed or altered. It requires the check to
report each store as different, naming the thing. What the SQL creates and
the check cannot see fails the test, so the check is extended, or the line
is shown not to matter and listed with its reason.

**Acceptance:** The test runs on SQLite, PostgreSQL and SQL Server and
passes with no altered store accepted, apart from a short list of lines
each with its stated reason. A line added to the SQL without a matching
check fails it. The acceptance of `WB-72` and `WB-73` is met.

**Dependencies:** None. Deliver before `WB-72` and `WB-73`.

**Evidence:** `src/haute/_quote_store/_catalogue.py`;
`src/haute/_quote_store/_book.py`.
