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
- **Only commercial-pricing is converted to each new format.** No other project needs converting (`READ-20`).
- **Workbench columns and tables have no ids.** Their names identify them. Following a rename in the quote store's SQL is left to a later package (`READ-33`).
- **Sheets, sections and components have no random ids.** A sheet is identified by its file name, a section by its title, and a component by a number.
- **`main.py` keeps its function order.** A new node's function goes just above the first function that uses it.
- **Formats:**
  - rating tables are written as a grid;
  - constants are a name-to-value map, and constants that only formulas read become global constants;
  - schema keys on disk are snake_case;
  - generated workflows repeat nothing.

**Out of scope:** `haute init` rewrites `haute.toml` from scratch on a re-run, as decided on 1 November 2026, so editing that file in place is not planned.

All the packages left are **format changes**. The loader accepts only the current format ([specification governance](../README.md)), and `READ-20` says how commercial-pricing moves to each new one.

Each package states its formats in its owning specification before behaviour changes, and names the contract it replaces there. Golden tests are written from those specifications, not from the implementation's output.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| READ-20 | Planned | P2 | Each format change comes with a one-off script that converts commercial-pricing and Haute's fixtures, proven before and after. |
| READ-22 | Planned | P2 | A Polars node is stored only as code in `main.py`, and a hand edit or comment keeps the rest of its steps. |
| READ-28 | Planned | P3 | Constants that only formulas read become global constants. |
| READ-29 | Planned | P3 | Every key Haute's schemas own is snake_case on disk; keys the analyst names are untouched. |
| READ-33 | Deferred | P2 | A column or table renamed against the project's default branch keeps its saved values in the quote store. |
| READ-40 | Planned | P3 | Generated workflows declare each secret, setup and check once. |

## Planned improvements

### READ-20 — Each format change converts commercial-pricing with a one-off script
**Why:** The loader accepts only the current format. The canonical-only rule in the [specification governance](../README.md) forbids any production code that converts an old format: "Nothing migrates it". Each format change from `READ-22` onwards must still bring commercial-pricing across. The owner decided on 3 November 2026 that no other project needs converting, so the rule stays and Haute gets no `haute migrate` command.

**Plan:**
- Each format change's pull request carries a one-off conversion script. The script:
  - converts Haute's own fixtures in that pull request;
  - is run by the owner once on commercial-pricing, in a commit of its own;
  - is not kept in Haute's code or tests once the pull request merges.
- **File by file:** it converts each file still in the old format and leaves alone each file already in the new one. So it can run on a project in which some files are already converted. It refuses a file in neither format, naming it, and changes nothing.
- **A commercial-pricing branch cut before a conversion** runs the script on itself, commits, then merges `main`. If the merge comes first, running the script afterwards converts the files the branch added in the old format.
- A comparison against a revision older than a conversion, such as the schema pane's comparison with the default branch, is refused with a message naming the conversion. Today it would hand old bytes to the current parser.
- Any new file a conversion creates is in the deploy bundle.

**Acceptance:**
- Each conversion is proven on the reference pipeline, the workbench fixture and commercial-pricing:
  - the converted files load to the same values;
  - saving twice gives identical bytes;
  - against results recorded before the conversion, the sample prices the same, and every node's output columns and types match.
- A branch that added and edited old-format files keeps both edits after it is converted and merged, in either order.

**Dependencies:** None.

**Evidence:** `specs/README.md`; `src/haute/_workbench_folder.py::form_at_ref`.

### READ-22 — A Polars node is stored only as code in `main.py`
**Why:** Today the code in `main.py` runs, and `config/polars/<node>.json` holds the steps again as an expression tree. The steps are kept only while their rendering is the same syntax tree as the code, with the same comments. That causes several problems:
- the tree nests one level per term in a chain of one operator, so such a chain is capped at 13 terms (`MAX_EXPR_DEPTH = 12`);
- adding a term changes 39 lines of JSON;
- editing the JSON by hand discards the steps;
- editing the code by hand, or adding a comment to it, turns the whole node into code.

**Plan:**
- Drop `config/polars/*.json` and the `config=` argument on `@pipeline.polars`.
- **Rebuilding steps on load:**
  - Haute rebuilds the steps from the node's code with `lift_steps` in `src/haute/_polars_steps_lift.py`, whose canonical form the [structured-syntax boundary](../codegen/structured-syntax-boundary.md) records.
  - A run of statements it cannot recognise becomes one code step, and the statements around it stay steps.
- **Column settings** such as `selected_columns` and `column_renames` go into the decorator's keywords, as a code-only Polars node writes them today.
- **Unfinished steps:**
  - While any step of a node is unfinished, the node is saved as an incomplete transform is today:
    - its whole step list goes in the `.haute.json` sidecar under the node, the steps before and after the unfinished one included;
    - its code is today's placeholder, which raises;
    - the save warns.
  - So the node fails in the editor, `haute run`, a standalone run and a deploy alike, and never returns the steps before the unfinished one.
  - Once every step is complete, the steps move into code and leave the sidecar.
- **Comments:**
  - a comment belongs to the statement below it, and moves or goes with that step;
  - a comment at the end of the body stays at the end;
  - comments never turn a node into code.
- **Chains:**
  - a chain of one operator becomes one step with a list of terms, so the 13-term cap goes;
  - grouping and the order of evaluation are kept: floats are never reassociated, and `sum_horizontal` is never substituted, since it treats missing values differently.
- **Step ids** come from each step's kind and position on load. The assistant's step edits keep their stale-revision protection, so ids from an older load are refused.
- **The formula box** shows each formula from the rebuilt step, so Haute needs no second implementation of the formula language.
- **Converting a project:**
  - the JSON is deleted once the code rebuilds to the same steps;
  - a node that doesn't is reported, its code stays, and its unrecognised statements become code steps;
  - a node with an unfinished step moves its step list into the sidecar.
- Replace the stepped-transform section of the [pipeline-config specification](../pipeline-config/high-level.md).

**Acceptance:**
- Loading and saving a node's code is byte-stable.
- A hand edit to one statement keeps the other steps, and a comment keeps the steps.
- A 20-term sum is one step.
- An unfinished middle step, with complete steps before and after it, round-trips. The node fails in the editor, `haute run`, a standalone run and a dry-run deploy.
- The column settings round-trip.
- Two unrecognised statements next to each other load as one code step.
- Tests for grouped arithmetic and for arithmetic with missing values give the same results as today.
- The formula box round-trips each formula.
- The reference pipeline gives every node the same output columns, types and sample values before and after its conversion.

**Dependencies:** `READ-20`.

**Evidence:** `src/haute/_config_builder.py::_same_program`; `src/haute/_polars_steps.py::MAX_EXPR_DEPTH`;
`src/haute/_codegen_builders.py`; `frontend/src/panels/editors/polarsSteps/formula.ts::additive`;
`src/haute/assistant/_catalog.py`.

### READ-28 — Constants that only formulas read become global constants
**Why:** A Constant node's sidecar is a map from name to value ([pipeline-config](../pipeline-config/high-level.md)). In the investigated pipeline, constants that only formulas read reach them through a cross join. That costs four nodes, four `connect()` calls, two sidecar entries and two positions.

**Plan:**
- **Promotion to global constants:** the conversion promotes a constant node, turning its constants into global constants and removing the node and its join, only when its structure allows it. All of these must hold:
  - the node's one consumer is a single cross join, with no keys, that adds its columns to every row of one stepped Polars node's input. A keyed join can leave a row without the constant or drop it, so it never qualifies;
  - within that Polars node, every reference to a constant column is a column reference in a formula step outside any group-by or aggregation, where a column and a scalar behave alike;
  - no step assigns to a constant column, so every reference reads the original value;
  - no code step comes after the join, since Haute cannot tell what a code step reads;
  - no constant column reaches the Polars node's output: a later step selects it out or drops it;
  - each constant's name is free among the global constants, and is a valid name there.

  Otherwise the conversion leaves the node and reports which condition failed. The global constant keeps the value's runtime type: a numeric text becomes a number, as it runs today, and text stays text.
- After a promotion, the sample must give every node the same output columns, types and values. This verifies a rewrite the structure allowed; it never decides eligibility.
- Constants that banding or rating steps read stay in their node.
- Trace already shows a global constant's value in a formula.

**Acceptance:**
- Conversion tests leave the node, naming the failed condition, for each of these:
  - a name collision;
  - a constant column reaching the node's output;
  - two consumers;
  - a keyed left or inner join, even when every sampled row matches;
  - a reference inside an aggregation;
  - a code step after the join;
  - a step that overwrites a constant column only on rows the sample doesn't hold, so the sample alone would agree.
- A text constant is promoted as text.
- On the reference pipeline, only formula-only nodes are removed, and it prices the same.
- Trace shows the values.

**Dependencies:** `READ-20`.

**Evidence:** `src/haute/_node_apply.py`; `src/haute/_config_io.py::global_constants_json`;
`src/haute/_expression_parser.py::_InlineGlobalConstants`.

### READ-29 — Every key Haute's schemas own is snake_case
**Why:** Configs mix camelCase keys (`outputColumn`, `defaultValue`, `onMissing`, `inputMapping`, `maintainOrder`) with snake_case keys (`output_column` on Model Score, `selected_columns`), while `main.py` uses snake_case throughout.

**Plan:**
- Rename every key Haute's schemas own to snake_case:
  - in node configs, the sidecar and the form folder;
  - in the browser's config types, which carry configs as stored;
  - in the assistant's catalogue.
- The specification lists the full map of old to new keys.
- Keys the analyst names never change: column names, categorical levels, source names in `by_source`, constant names, sheet and table names.
- The conversion for this change renames every listed key in one pass, so each file changes once for it.
- Coordinate with `PCFG-R07` (typed config models) and `API-R03` (the generated browser contract), which own those boundaries.

**Acceptance:**
- No schema-owned camelCase key remains in a saved file or schema.
- A camelCase column name, categorical level and source name survive conversion and a save unchanged.
- The assistant's catalogue test and the browser contract match.

**Dependencies:** `READ-20`; `PCFG-R07` in the [pipeline-config roadmap](pipeline-config.md); `API-R03` in the [server API roadmap](server-api.md).

**Evidence:** `src/haute/_config_io.py::node_config_json`; `src/haute/_banding_config.py`;
`src/haute/assistant/_catalog.py`.

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

