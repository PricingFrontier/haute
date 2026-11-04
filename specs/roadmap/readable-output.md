# Readable output roadmap

## Scope

These packages cover what Haute writes into a project's repository, read as a diff:
- `main.py` and its `.haute.json` sidecar;
- the node configs under `config/` and the global constants;
- the Workbench form folder (`workbench/`) and the test quotes;
- the files `haute init` scaffolds.

The aim is that a reviewer who never opens the editor can follow a pull request, and that an analyst can develop in code. One change in the editor should make a small diff that says what changed, and a hand edit to a file should survive the next load and save.

The packages come from an investigation of [commercial-pricing pull request #1](https://github.com/PricingFrontier/commercial-pricing/pull/1) on 3 November 2026. It found:
- **Polars nodes are stored twice.**
  - The body in `main.py` is what runs, and an expression tree in `config/polars/<node>.json` holds the steps again.
  - The steps are kept only while their rendering is the same syntax tree as the body, with the same comments.
  - Adding one term to a 12-term sum changes 1 line of Python and 39 lines of JSON.
- **Six JSON writers follow different rules.** A prototype printer rewrote the pull request's 41 JSON files with no change to what they hold: under a 100-column limit they went from 2,990 lines to 1,652. The one printer Haute now writes them with (see [pipeline-config](../pipeline-config/high-level.md), JSON files Haute writes) took commercial-pricing's node configs from 562 lines to 463.
- **A rating table takes 5 lines per cell.**
- **Inserting a node into `main.py` moves unrelated functions.**
- **Random ids fill the Workbench form folder**, such as `c_818cc52f3c8b78a9`, `t_d7d9abf03a7cf621` and `collection_nj2i`. The built-in `quote_details` columns are the exception: their ids are fixed names such as `qd_insured`.

The owner decided the following on 3 November 2026:
- **Polars code lives only in `main.py`.** Haute rebuilds as many steps as it can from the code. Statements it cannot recognise become a code step, and the rest of the node stays as steps.
- **Only commercial-pricing is converted to each new format.** No other project needs converting.
- **Workbench columns and tables have no ids.** Their names identify them. Following a rename in the quote store's SQL is left to a later package (`READ-33`).
- **Sheets, sections and components have no random ids.** A sheet is identified by its file name, a section by its title, and a component by a number.
- **`main.py` keeps its function order.** A new node's function goes just above the first function that uses it.
- **Formats:**
  - rating tables are written as a grid;
  - constants are a name-to-value map, and constants that only formulas read become global constants;
  - schema keys on disk are snake_case;
  - generated workflows repeat nothing.

**Out of scope:** `haute init` rewrites `haute.toml` from scratch on a re-run, as decided on 1 November 2026, so editing that file in place is not planned.

All the packages left are **format changes**. The loader accepts only the current format ([specification governance](../README.md)). Each change carries a one-off script in `scripts/one_off/` that converts Haute's fixtures in its pull request and, run once by the owner in a commit of its own, commercial-pricing. It converts each file still in the old form and leaves the rest, so it runs on a branch part-converted, and it is proven on commercial-pricing: the converted files load to the same values, a second save changes nothing, and every node's configuration matches what main's Haute reads from the original. The script is removed once it has run.

Each package states its formats in its owning specification before behaviour changes, and names the contract it replaces there. Golden tests are written from those specifications, not from the implementation's output.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| READ-33 | Deferred | P2 | A column or table renamed against the project's default branch keeps its saved values in the quote store. |
| READ-40 | Planned | P3 | Generated workflows declare each secret, setup and check once. |

## Planned improvements

### READ-33 — A column or table renamed against the default branch keeps its values
**Why:** Workbench tables and columns are known by their names, so a column renamed in the form reads as removed and added when compared with the default branch. The store keeps the old SQL column and its values through saves, but the renamed column starts empty. The schema pane already pairs a renamed column with its column when comparing against a database.

**Plan:** If reopened, let the analyst pair a removed column with an added one when comparing against the default branch too, as the pane does against a database. The SQL then renames the column, or the table, so saved quotes keep their values in it.

**Acceptance:** A column renamed and paired keeps every saved value, on SQLite and on the hosted engines.

**Dependencies:** The owner's decision to reopen it.

**Evidence:** `src/haute/_quote_store/_migrate.py::_plan_columns`.

### READ-40 — Generated workflows declare each secret, setup and check once
**Why:** In the generated workflows:
- `python-version` appears seven times, though uv reads `.python-version`;
- the four `AZURE_*` secrets are repeated in three steps;
- `release.yml` copies the checks in `ci.yml`, and `ci.yml` repeats its setup in four jobs.

**Plan:**
- Declare the secrets once per job.
- Drop `python-version` where uv reads it.
- Make `ci.yml` a reusable workflow that `release.yml` calls, so the checks are written once.
- Keep every behaviour the workflows have today:
  - publishing by image digest;
  - staging rollouts one at a time;
  - production gated on staging;
  - the reports kept as artifacts.
- Shell plumbing such as `tail -n 1` and `tee publish.log` stays until Haute's commands give their results directly. `WB-08` owns those commands for a workbench project.
- `DOC-02` updates the CI pages to match.

**Acceptance:**
- Golden tests of the generated workflows.
- A structural test: publishing needs every check, and production needs staging.
- A generated project's CI on GitHub stops before publishing when a check fails.

**Dependencies:** `WB-08` in the [workbench roadmap](workbench.md); `DOC-02` in the [documentation roadmap](docs.md).

**Evidence:** `src/haute/_scaffold.py::_github_ci_yml`; `src/haute/_scaffold.py::_github_deploy_yml`;
`src/haute/_scaffold.py::_github_secrets_env`.

