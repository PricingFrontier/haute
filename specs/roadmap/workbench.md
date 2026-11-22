# Workbench roadmap

## Scope

The workbench inside Haute is specified in [the workbench
specification](../workbench/high-level.md): the form file and its tables, the Workbench
Input and Workbench Output, the Workbench view with its schema editor, its sheets, the
sample priced live and Preview, and the form on the save ledger. How a deployed pipeline
reads a quote in the workbench's shape is specified in [the deploy
specification](../deploy/high-level.md). The packages below are what neither covers: the
underwriter app, which carries the sheets and the pipeline to the host a project uses; the
delivery of a workbench project through CI, from a merge to `main` to that app; the schema
changes and environments that keep the quote store, the form's own tables in the user's
database, in step with the form, and the server databases it runs on; how underwriters
manage their quotes in the app;
exports from the app; test quotes analysts keep and price again; and the actions
that fill the sheets from databases and APIs.

`WB-25` is the app's sign-in, which waits on the Azure work.

`WB-38` comes from the 25 October 2026 codebase review (finding FWB-7): what it found
in the Workbench view's Allowed field.

`WB-46` to `WB-50` come from the 9 November 2026 codebase review. Each entry says whether
it was confirmed by running the code or read from the source; none was run against a
hosted app or a server database.

`WB-51` to `WB-53` were reproduced on 10 November 2026 through the editor's and the app's
routes. All three come from leads the October reviews left unverified; `WB-53` is a decision.

`WB-54` and `WB-55` come from the review plan's third sweep of failed writes on 11 November
2026. Both were reproduced on a SQLite quote store with a second process holding the file,
as a database browser or a backup does; no server database was used.

`WB-56` to `WB-58` come from the review plan's phase 4, which ran the underwriter app on
SQL Server 2022 and PostgreSQL 16 beside SQLite on 12 November 2026, in the containers CI
uses. The main session ran each again on the three databases. `WB-59` comes from the same
phase's carrying of projects made on Windows to Linux, and was run again on Windows.

`WB-63` to `WB-66` come from the review plan's phase 5, a reader from a second
model family (Codex) over the code changed since the 31 October audit. A
worker built each run and the main session ran each again on 14 November
2026.

`WB-70` to `WB-74` come from the review plan's phase 4, the quote store
on PostgreSQL 16 and SQL Server 2022 under repeated requests, lost
connections and hand-made schemas. A worker built each run and the main
session ran each again on 16 November 2026.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| WB-07 | Planned | P3 | The underwriter app runs on a Databricks App, on AWS or on an internal platform as it runs on Azure Container Apps, each signing underwriters in its own way. |
| WB-08 | Planned | P2 | A workbench project's CI tests the packaged app before any host sees it, and an export template's change starts the release. |
| WB-09 | Decision | P2 | An Import on a sheet, or a lookup beside a key cell, runs against a file, a database or an API and fills input cells with what comes back, through the server with no credential in the browser, while pricing stays as pure as it is. |
| WB-11 | Decision | P2 | An underwriter exports a final quote from the app in the formats the project needs, naming the release that priced it. |
| WB-12 | Planned | P3 | An analyst saves the quote typed in Preview as a test quote in `tests/quotes`, prices every test quote again from Preview as the pipeline changes and accepts what moved, and CI reports the test quotes whose outputs moved. |
| WB-13 | Planned | P3 | An underwriter records how a final quote ended, and archives or reassigns many quotes at once. |
| WB-25 | Decision | P3 | The underwriter app refuses to trust sign-in headers it was not put behind. |
| WB-38 | Decision | P3 | Allowed values typed into an older form are either refused by the form schema or canonical; the Allowed field stops calling a shape the server accepts "older". |
| WB-46 | Planned | P2 | A quote store that stops answering is refused within seconds, and a request waiting on it holds no lock others need. |
| WB-47 | Decision | P2 | A release is not built while a Workbench Output declares a column no frame fills with a fitting type. |
| WB-49 | Planned | P3 | The hosted check's two probes are retried like its health check, so one dropped request does not roll a good release back. |
| WB-50 | Planned | P3 | A save the store is bound to refuse is refused before the quote is priced. |
| WB-51 | Planned | P3 | A form request whose column name is not text is answered 422 naming it, never 500. |
| WB-52 | Planned | P3 | The form's save refuses a column named with a Python keyword, as the tables route and the editor do. |
| WB-53 | Decision | P2 | A pricing failure in the underwriter app gives the underwriter a fixed message and an id, and keeps the exception's text for the log. |
| WB-54 | Planned | P2 | The release route does not report the quotes open while the app's store cannot be written to. |
| WB-55 | Planned | P3 | A SQLite store held exclusively, damaged or deleted is said as such at start, naming its file. |
| WB-56 | Planned | P3 | On SQL Server a quote address with a trailing space names no quote, as on the other databases; no row is stored under a padded reference. |
| WB-57 | Planned | P3 | A search of the quote list finds the same quotes on SQLite, SQL Server and PostgreSQL, for accented letters in any case and for a word that holds `[`. |
| WB-58 | Planned | P3 | The quote list sorted by reference or by insured is in one stated order on every database. |
| WB-59 | Planned | P3 | A release built from one commit has one id, whether its checkout has LF or CRLF line endings. |
| WB-64 | Planned | P2 | The migration SQL for SQL Server keeps a column's NOT NULL when it changes the column's type. |
| WB-66 | Planned | P2 | A test quote's table rows are opened by their index values after a params list is reordered, and values equal as numbers are not listed as not opened. |
| WB-70 | Planned | P2 | A creation sent twice under one operation key answers both with the quote it made, never "already holds the reference". |
| WB-74 | Planned | P3 | A password corrected in `.env` is used by the running editor's next Refresh. |

## Planned improvements

### WB-07 — The underwriter app on other hosts
**Why:** The underwriter app runs on Azure Container Apps. `[workbench.app] host =
"azure-container-apps"` names the host whose sign-in fronts it; `haute app build` writes a
release of the sheets and the pipeline, with an id taken from what prices a quote, and, with
a pinned `[deploy.container] base_image`, the image's build context, which `--image` builds;
the app signs in the principal Azure's built-in authentication sets on each request and
refuses a request without one; and `haute app serve` runs the same app on an analyst's
machine. That host is the only value the setting takes, so a project on a Databricks App,
AWS or the company's own platform cannot run the app. A Databricks App runs a source bundle,
not an image, with a per-file size limit a model can pass, and signs users in through the
workspace's proxy; a load balancer's sign-in on AWS asserts the user in a token it signs,
which the app would have to verify; an internal platform's proxy asserts the user its own
way. The principal Azure asserts is trusted because the app is reachable only through
Azure's front, which strips those headers from what a client sends: a property of each
Container App's ingress that the project's IT team confirms, not one the app can check.

**Plan:** Each host is a value of `[workbench.app] host`, with the identity that host
asserts read, verified where the host signs it, and a request without it refused, as for
`azure-container-apps`; the release and the app stay the same. A Databricks App first, when
a project asks for one: the release packed as the App's source bundle, its `app.yaml`
starting the same entry point; models past the per-file limit held in storage the App reads,
checked against the digest the release records; and the user the workspace proxy asserts,
inside the trust boundary of hosted-databricks-app. Then AWS behind a load balancer's
sign-in, its token verified against the region's published keys, and an internal platform's
proxy, configured by the header it sets.

**Acceptance:** For each host added, a release built for it runs there from what `haute app
build` writes; an underwriter the host signed in prices a quote as on Azure Container Apps;
a request without the host's sign-in, or with an identity the host did not assert, is
refused; and a release built for one host refuses the identity another host's headers carry.

**Dependencies:** The host and its sign-in, provided by the project's IT team. On
Databricks, [hosted-databricks-app](../hosted-databricks-app/high-level.md) for the proxy's
trust boundary.

**Evidence:** `src/haute/_workbench_config.py::read_workbench_config` (the one host
`[workbench.app]` takes); `src/haute/_workbench_app.py::create_app` (the identity read by
host) and `::build_release` (the release and the image's build context);
`src/haute/deploy/_container.py::prepare_build_directory` (the image's Dockerfile).

### WB-08 — The workbench project's CI/CD
**Why:** A workbench project hosts no scoring API: its workflows validate the project and the
app's release on every pull request and on a push to `main` that changes a release input;
on that push they publish the app's image once, by digest, and roll the staging app to it,
and by hand, after approval, roll production to the image staging ran for that commit, each
rollout checked on the host and recorded on its Container App, and before production is
approved `haute app impact` prices a sample of the quotes production saved on both apps and
compares the fields the two releases share inside their response tables
([workbench](../workbench/high-level.md)). What the release flow promised and still lacks:
Nothing tests the packaged app before a host sees it: `haute app build` validates the
pipeline and scores the committed test quotes, but no step starts the app the image holds,
saves a quote through it or reopens one, against SQLite or against the server engine IT's
instance runs. The export templates of WB-11, once they exist, start no release
either. Pricing analysts work on their own machines: Save writes the save branch, Commit
makes a milestone, Push publishes it, a pull request to `main` is opened and merged on the
provider, and the merge is the release. A milestone Commit sweeps tracked files only, so a
file the workbench adds, such as the test quotes of WB-12, needs tracking from the start.

**Plan:** The behaviour lives in Haute's commands, as `haute app rollout` does, so the YAML
stays thin and alike across providers. The release triggers gain each release input as it
arrives, where its specification puts it: the export templates. On every pull request, and
again on the merge's build before it is published, the packaged app is tested in the CI
runner, against SQLite and once against the server engine IT's instance runs (a service
container), each a throwaway store of the branch's form made for the tests alone, since
SQLite proves nothing of that engine's types; CI rehearses no migration, whose SQL the
analysts write in the schema pane ([workbench](../workbench/high-level.md)) and run
themselves: the test quotes of WB-12 re-priced one request each against their accepted
outputs, a quote saved, reopened and, once WB-11's formats exist, exported through the app,
and the sheets driven in a browser.

**Acceptance:** A pull request whose packaged app cannot save and reopen a quote, against
SQLite or the server engine, fails before any host sees it, each test store made for the
tests alone and no migration rehearsed; a change confined to an export template starts the
release; no step of the workflow changes a hosted database's tables or a saved quote.

**Dependencies:** WB-11 (the export templates and the export the app's tests make, once
their formats are decided) and WB-12 (the test quotes).

**Evidence:** `src/haute/_scaffold.py::workbench_ci_files` (validate, publish, roll out and
impact, no packaged-app test);
`src/haute/_git_transactions.py::_residual_tracked_changes` (a milestone sweeps tracked
files only).

### WB-09 — Actions that fill the sheets
**Why:** Underwriters key in what other systems already hold: the broker and the assured
from a CRM, last year's layers from a policy database, a schedule of items from a
reference table. Today every cell on a sheet is typed. The ruling is that buttons populate
the workbench: nothing calls a database or an API at scoring time, so the test quotes, the
impact analysis and the dry-run deploy keep assuming pure scoring, a deployed pipeline
needs no warehouse, and a filled cell is a typed cell as far as pricing can tell. What
exists: one path for a sheet's values, the sample in Preview and the quote in the
underwriter app, through which a result can land exactly as typing does, so the rule marks
follow it and it is priced as typing is; a Databricks SQL reach on the server, which
validates a statement as a single SELECT, builds connection settings from the workspace
credentials and takes a service principal's, used today to snapshot tables; a
credential policy for the editor's data access, environment variables named in the docs
and never a value in a file; and the Import, a button on the sheets whose settings, kept
in the form, name its format (a CSV the underwriter picks, an Excel workbook's sheet and
header row, or a database's named connection, its query and the column giving each of
its parameters), the input table it fills, how it fills a many-row table (replace,
append or update by key) and the names its columns are read from, flagged on its frame
as a component is, and drawn disabled in Preview and the app since nothing runs it.
What does not exist: a driver for any database but Databricks, an outbound HTTP
pattern, a route that runs an Import, the file read in the browser, and a credential
model for a project whose sheets reach several systems.

**Plan:** Decision first, recorded here before the first slice. The auth model: how a
source's credentials are named and supplied (a variable per source, a secret scope on
Databricks, the CI provider's secrets for the generated workflows), and whose identity an
action runs under, the app's service principal or the signed-in underwriter's, which
decides whether a database's row-level permissions apply. The sources: Databricks SQL
first, then databases that are not Databricks, each a driver behind an optional extra as
the Databricks one is, with SELECT-only validation per dialect generalising the validator
that exists, and HTTP APIs with allow-listed hosts, a timeout and a mapping from the
response's JSON onto columns. Whether actions are declared alone (a statement or a
request with named parameters) or Python actions are allowed too. Where the pieces live:
the Import's settings beside the schema in the form file, as they are, since they name
tables and columns by id and version with the sheets; the connection its name stands
for in `connections.toml`, the file of named connections the editor and `haute app serve`
already read ([workbench](../workbench/high-level.md)), with the names
of its credential variables; the values in `.env`, never in a file the ledger pushes. Whether a press in Build writes the
sample, which Save then writes to disk, or actions run in Preview alone. Then the
slices: the connections of `connections.toml`, which an Import's connection names; a route
under the workbench router that takes the sheet's values, resolves the Import's
parameters, runs its query on the server with a row cap and returns rows in the
sample's shape, typed by the columns they fill; a CSV or a workbook the underwriter
picks read into the same shape; the Import enabled in Preview, its rows landing as
typing does, replacing, adding to or updating the table's rows as its settings say,
asking first before it replaces rows already typed; an HTTP request as another of its
formats; a lookup beside a key cell as the same action from a column property; and the
underwriter app running the same actions on its server.

**Acceptance:** With a source declared in `haute.toml` and an Import in the form, pressing
it in Preview fills the bound input cells with the query's values, typed by their
columns and marked where a value breaks a rule, and priced as typed values are, the
action itself never calling the pipeline; the same action runs against a database that is not
Databricks, and an HTTP action fills cells from a response; no credential value appears
in the form file, in `haute.toml`, in an API response or in a log; a statement that is
not a single SELECT, a host not allowed, a result over the cap and a bound column no
longer in the schema are each refused naming the cause; a project with no actions behaves
exactly as now.

**Dependencies:** The decisions above, recorded here before the first slice. For
production, the underwriter app, which runs the actions on its server, and, on Databricks,
[hosted-databricks-app](../hosted-databricks-app/high-level.md), whose secret handling
the sources follow. The Host, Origin and session checks of
[sandbox-security](../sandbox-security/high-level.md) apply to the new route as to any
other.

**Evidence:** `frontend/src/workbench/useSheetValues.ts::useSampleValues` and
`::useQuoteValues` (the one path for a sheet's values);
`frontend/src/stores/useWorkbenchPricingStore.ts::priceForm` (the sample priced as it is typed);
`src/haute/_workbench_form.py::ImportWidget` (the Import's settings, read strictly);
`frontend/src/workbench/panel/ImportSettings.tsx` (where they are chosen);
`frontend/src/utils/workbenchForm.ts::importProblems` (what the view flags on it);
`src/haute/routes/workbench.py` (the workbench router);
`src/haute/_databricks_io.py::_validate_select_clause`, `::_resolve_connection` and
`::_service_principal_credentials` (the Databricks SQL reach); `pyproject.toml` (the
Databricks extra); `docs/deployment/targets/databricks.md` (credentials as CI secrets
and the editor's data-access pair).

### WB-11 — Exports from the app
**Why:** Underwriters take what they need out of the app themselves: a quote's tables for a
spreadsheet, a file another system imports, a document for a broker. Nothing in Haute
exports a quote; the sheets show it and Preview's results stay in the browser. The
Export, a button on the sheets, holds what an export would be, kept in the form: the
tables, a CSV of one or a workbook of a sheet each under a file name made of the quote's
details, headed by names or labels, its values raw or formatted, or a database's named
connection and the tables each is written to; it is drawn disabled in the app, and in
Preview but for an Excel workbook of cells (below).

**Decided (12 November 2026):** the first format is an Excel workbook of cells: chosen
fields of the one-row tables, each written into a cell of a sheet, `Slip!B3` say, of a
workbook Haute makes, its values typed and formatted as their columns' formats show them,
each cell naming another for its field's label if it wants one, `Slip!A3` say, the label
the schema gives the field; and tables written as blocks, each a table's columns chosen
and ordered, its rows under a header of their labels, their names or none from the
block's top-left cell, `Slip!A12` say, nothing written under a block in its columns,
whatever rows a quote gives it. A column or a table the schema loses stays in the
export as a problem rather than dropping out of it.
Its first run is Preview's: pressing the Export downloads the workbook of the sample as
it was last priced. Still to come, each its own slice: the app's run of it (below); a
template, the analyst's own workbook filled in place of one Haute makes; a cell for each
row of a table with a row for each value; and cells named in a template in place of an
address.

**Plan:** Decision first, recorded here before the first slice: which formats the first
release needs (the quote's tables as CSV or a spreadsheet, an import file for another
system, a document from a template), whether the Export on the sheets is how the app
exports or the quote's bar offers exports beside it, and whether a quote exported again
later reproduces the template it was first exported with or uses the current one; the
first format is decided above. Then exports of a final
quote, which the store's stages gate them behind, never of a draft, so every export is of a quote
that does not change while it is final and names the release that priced it; run on the
app's server for the signed-in underwriter and recorded in the quote's activity log. The
Export's database target is decided here too: once the store is the form's own tables in the
user's database, writing a quote's tables to that database again adds nothing, so
the lean is to take the target out unless a project names a destination of its own.
Templates and mappings, where a format needs them, live in the
project beside the form and ship in the release, so a change to one starts the workflow of
WB-08 and is tested there against the test quotes of WB-12.

**Acceptance:** An underwriter exports a final quote in each format decided, and the file
holds the quote's values and prices and names the release that priced them; a draft is not
exported; each export is recorded in the quote's activity log; a change to a template starts
the workflow, and the template's output for the test quotes is checked in CI.

**Dependencies:** The decision above.

**Evidence:** `src/haute/_workbench_form.py::ExportWidget` (the Export's settings, read
strictly); `frontend/src/workbench/panel/ExportSettings.tsx` (where they are chosen);
`src/haute/_workbench_export.py` (an Excel workbook of cells written, and Preview's
route to it, `POST /api/workbench/export`);
`frontend/src/utils/sheetValues.ts::pricedRows` (the priced rows the sheets
show); `frontend/src/workbench/useSheetValues.ts` (the one path for a sheet's values);
`src/haute/_workbench_tables.py::quote_schema` (a quote's columns as typed).

### WB-12 — Test quotes in Preview
**Why:** Analysts need quotes they keep and price again as the pipeline changes, so a change
to the pricing shows on the quotes they know. A first version was taken out on 23 October
2026, to be built out again later. Its quotes were saved from the sheets in Preview, run in
bulk, accepted and deleted from a panel there, and kept in a SQLite file committed with the
project, which git diffed as text and merged quote by quote. Deploy's check and the smoke
test priced them again, and a pull request's changes to them were reported on it. Its
commits are `352823c4d`, `d4249d583`, `448aee189`, `42a7309b3` and `5ccc51fff`, and
`c44884922` took it out. Its
pricing went through the preview route, which answers at most 10,000 rows of a table, so a
quote whose many-row table ran past that could not be kept.

**Plan:** Decided with the owner on 1 November 2026: test quotes are kept in the
project's test-quote folder, `[test_quotes] dir`, `tests/quotes` unless it names another,
where deploy's test quotes already live; each is saved from the quote typed in Preview; in a
workbench project CI reports a test quote whose outputs moved and fails only one that does
not price; an automated project's dry run and deploy keep failing on a moved output. The
rest follows from those.

*The file.* One JSON file per test quote, `<name>.json`, holding the one-row array deploy's
test quotes take, `[{"input": ..., "expected": ...}]`, so the dry run, deploy's check,
`haute app build` and WB-08's packaged-app tests read one format. `input` is the effective
request Preview prices: the request the sheets build (`quote_request`), with the identity
Preview stands in for an unsaved quote (`quote_reference` `PREVIEW`, `quote_option` 1)
written into its `quote_details`, so the file prices as it stands wherever it is read.
`expected` is the Workbench Output's answer in the price route's shape: every table, one-row
tables included, a list of rows by table name, values JSON-safe as the route sends them
(dates as ISO text, a non-finite number as null, a document pruned as the route prunes it).
Every pricing of a test quote, in Preview and in CI, renders its answer through that one
normalisation before it compares. A name is letters, digits, spaces, hyphens and underscores,
up to 100 characters; a name that matches an existing file's ignoring case is refused, and
the file's whole path is checked as any project write's is (inside the folder, no link,
junction or hard link). Saving under a new name refuses one that matches an existing file
ignoring case; replacing a test quote targets the file the analyst picked from the list,
under its own spelling, after asking. A file in the folder that holds anything else (several rows, an
input-only smoke case, another Input's request) is not a Preview test quote: Preview lists
it as one it cannot open, and CI prices it as deploy does today.

*Replaying, not rebuilding.* A test quote is priced from its stored `input`, never rebuilt
from the sheets, so Preview and CI price the same request and accepting new outputs keeps
them with the request that produced them. Opening one onto the sheets is a view: each
table's and column's value is put back by name as the sheets hold it, as the quote store
puts a saved quote back (true and false as a ticked or empty box, a number as plain text, a
date as ISO text), preset rows placed by the column's values; a value whose table
or column the form no longer has, or that a column's type or options no longer take, is
listed as not opened. Changing the sheets and saving under the same name replaces the test
quote with the new request and its pricing, after asking.

*Pricing whole.* A test quote is priced by a route of its own on the editor's server through
the scorer the app prices with (`score_graph` on the project as saved, resolved as
`haute app build` resolves it, the project saved first when it has unsaved changes), its
response whole rather than cut at the preview route's 10,000
rows; a quote past the request body limit, or one that runs out of memory or time, is a
failure named as such, never a truncated answer. Pricing all runs one quote at a time and
can be cancelled.

*Moved.* One comparison, used by Preview and CI alike, compares `expected` with the new
answer on the outputs of the form as it stands: a one-row table column by column; a
many-row table's rows matched by its key (its index, the column giving its rows, or, for a
table of outputs alone, the outputs marked as keys), else by position
when the counts match; a number moved when it differs by more than a millionth of a percent
of the expected value, or by more than the row's `tolerance_pct` when it gives one; a field
only one side holds named as added or removed. The outcomes are unchanged, moved (each
field and row named, the expected and new values), no longer compares (a key held twice or
empty, counts by position that differ) and failed (it did not price). An automated project's
dry run and deploy keep their strict comparison.

*Saving, accepting, deleting.* Each is a project write as the editor's others are, serialised
against saves and branch switches and refused on a branch other than the one the run was
made on. Each names the revision (a digest of the whole file) it was decided on, checked
inside the write: Save writes a new file atomically; replacing, Accept and Delete act only
while the file is still that revision, Accept also only while the run's pipeline and form
are those on disk, so an edit from another tab or outside the editor is never overwritten;
Accept all accepts each quote that still matches and names any that did not. Each lands on the save ledger as a save does (captured for
the milestone Commit, not merely staged), reporting a write that succeeded but was not
captured, and asking for the git identity first, as Save does.

*CI.* Pricing test quotes answers structured results, each quote ok, moved (with its
differences), no longer compares, or failed, instead of the dry run's one error status.
In a workbench project `haute app build` passes with moved and no-longer-comparing quotes,
prints each with its fields, writes `test_quotes_report.md` in the working directory whatever
the outcome, and fails when a quote failed to price or a file is not a test quote; a
workbench project with no test quotes builds. The generated validate jobs keep the report
as an artifact whether the job passes or fails, `haute ci update` writing them again, and
WB-08's packaged-app tests apply the same rule. Workbench `haute init` writes no starter
test quote; the folder appears with the first save.

Not decided yet: opening a quote from a quote store's tables into Preview, which would then
be saved as any typed quote is. A real quote carries customer data, the insured and broker in
`quote_details` and whatever its input tables hold, into git once saved; whether it is
blanked, which environments it is read from, or left to the analyst is the owner's to decide
first.

**Acceptance:** An analyst saves the quote typed in Preview as a test quote, and it appears
in `tests/quotes` as `<name>.json`, one row in deploy's test-quote shape whose `input` prices
as saved, captured on the save ledger; opening it puts its values back on the sheets and
names any it cannot; after a change to the pipeline, pricing all of them again marks the one
whose output moved and accepting it rewrites its `expected`, refused when the file changed
since it was priced; a quote whose schedule runs past 10,000 rows is saved, priced again,
compared and accepted whole; a workbench pull request whose test quote's output moved passes
its validate job, the kept report naming the quote and the field, while one whose test
quote fails to price fails it; an automated project's dry run still fails on a moved output.

**Dependencies:** None.

**Evidence:** The commits above (the first version); `src/haute/deploy/_validators.py`
(`score_test_quotes`, deploy's test-quote rows `{"input", "expected", "tolerance_pct"}`
and the expected-output comparison that fails the dry run);
`src/haute/deploy/_config.py::test_quote_files` and `_test_quotes_dir` (`[test_quotes]
dir`, `tests/quotes` by default); `src/haute/_workbench_app.py::build_release` (the release
validated and its test quotes priced as a deploy does); `src/haute/_workbench_tables.py`
(`quote_request`, the request the sheets build); `src/haute/_workbench_impact.py`
(`compare_quote`, the comparison of two answers' tables);
`src/haute/_workbench_input.py` (the stand-in identity Preview prices an unsaved quote
with); `src/haute/_workbench_app.py` (`price_values`, the app's whole pricing through
`score_graph`, `priced_tables` and `finite_document`); `src/haute/routes/_helpers.py` and
`src/haute/routes/_save_pipeline.py` (project writes serialised against saves and branch
switches, and the save ledger's capture); `src/haute/cli/_init_cmd.py` (the starter test
quote `haute init` writes today).

### WB-13 — Managing quotes in the underwriter app
**Why:** The underwriter app opens on its quotes ([workbench](../workbench/high-level.md)):
the Quotes page lists them by who they are with, by stage and by archive, searched and
ordered, from their quote details, Haute's `quote_details` table, whose row each revision
keeps; a quote has a reference, numbered revisions each saved priced or unpriced and
priced again on the server, a handover note, an assignee, a
final revision reopened with a reason, an archive, and the duplicates, renewals and
alternatives made from it, with a history of who did what; a reader sees every quote and
changes none. What underwriters still keep elsewhere is the rest of a quote's commercial
life: nothing records whether a final quote was bound, declined or not taken up, so the
lists cannot tell open work from closed; and archiving or reassigning many quotes is done
one at a time. The store keeps no revisions, so comparing two of them is not part of this
package.

**Plan:** Decisions first, recorded here before each slice: which outcomes a quote can close
with, and whether a project names its own. Then, each usable on `haute app serve`:
outcomes, recorded on a final quote in its activity log with who and when, and listed apart
from the open quotes; and archiving and reassigning a selection of quotes at once, each
checked against its version, a quote moved on since named without undoing the rest. Exports
stay WB-11's.
A quote's validity left with `quote_details.valid_until`, taken out on 2026-10-24 as no
project used it; a project that needs it keeps a date of its own.

**Acceptance:** An outcome recorded on a final quote is listed with who recorded it and
when, and the quote leaves the open lists; a selection of quotes is archived or reassigned
at once, a quote moved on since refused by name and the rest changed.

**Dependencies:** The decisions above.

**Evidence:** `src/haute/_quote_store/_book.py::QuoteBook` (the quotes, their changes and
lists, without outcomes); `frontend/src/app/QuotesPage.tsx::QuotesPage` (one quote changed
at a time).

### WB-25 — The app's sign-in
**Why:** The app trusts the Easy Auth headers because `release.json` names the host, so a
container reached without Easy Auth in front signs in whoever names themselves, with edit
rights when no edit role is set. The owner takes it up with the Azure work in the next pull
request.

**Plan:** The decision first, recorded here before a slice: a startup check that the
host's sign-in is in front, or an explicit trust setting the operator sets, refusing the
misconfiguration either way.

**Acceptance:** Set by the decision.

**Dependencies:** None.

**Evidence:** `src/haute/_workbench_app.py::create_app` (the principal read from the
headers when the release names its host).

### WB-38 — Allowed values typed into an older form
**Why:** The Allowed field carries a branch for "values typed in, as an older form holds
them" (`TYPED_IN`), a shape the builder can no longer produce and `schemaProblems` names
as a problem, while the server's `FormSpec` still accepts `options` with
`options_source` null. Under the canonical-only policy historical input gets no special
status: either the server refuses the shape and the branch goes, or the shape is
canonical and should not be called "older".

**Plan:** Decide. Either `FormSpec` refuses `options` without `options_source` for input
columns, and `TYPED_IN`, its test and the problem text are removed; or both stay and the
specification and the field stop calling the shape older.

**Acceptance:** One of the two, with the workbench specification saying which.

**Dependencies:** None.

**Evidence:** `frontend/src/workbench/schema/AllowedField.tsx::TYPED_IN`,
`src/haute/_workbench_form.py::FormSpec`, `frontend/src/utils/workbenchForm.ts`.

### WB-46 — The quote store's connections and the gate have deadlines
**Why:** Read from the source. The server-database engine is created with a
pre-ping only: no connect timeout, no statement timeout and the default pool,
and the URL builder adds none. A sweep of the quote store, the workbench
modules and the database IO for a connect, login, statement or pool timeout
found none. The driver's default is to wait as long as the operating system
does, which is minutes for a host that drops packets. The store's gate runs
its opener (connect, catalogue read, privilege probe) while holding its lock,
and every quote route, the price route and the release route take that lock
on a worker thread. So when the database stops answering, each request parks
a worker thread behind the gate, or on a statement with no deadline, instead
of getting the 503 "cannot reach its quote store" the module promises; after
the refusal's ten seconds the next request blocks them all again. An
exhausted pool surfaces as SQLAlchemy's timeout, which the quote routes
answer as a 500.

**Plan:** Give each provider an explicit connect timeout and statement
timeout, and the pool a bounded wait that is answered as the 503. Have the
gate hand waiters the check already in flight instead of the lock, so one
slow open delays one answer.

**Acceptance:** With the database made to accept connections and never
answer, a quote read is answered 503 within the configured deadline, twenty
concurrent requests are all answered within it, and no worker thread is left
waiting. A statement that outlives its deadline is cancelled and reported as
the store being unreachable.

**Dependencies:** None.

**Evidence:** `src/haute/_quote_store/_providers.py::make_server_engine`;
`src/haute/_quote_store/_providers.py::server_url`;
`src/haute/_workbench_app_store.py::StoreGate`;
`src/haute/_workbench_app_quotes.py::_call`.

### WB-47 — A release with a Workbench Output column nothing fills
**Why:** Read from the source. While an analyst edits, a declared output
column the connected frame lacks, or holds with a type that does not fit, is
filled with nulls, and a table nothing is connected to likewise; the
specification says none of these stops a run and the panel flags each. No
check looks on the way to a release: the deploy's form validation tests
identity clashes, key collisions and unresolved references only, and the
functions that judge whether a frame's column fits are used by trace alone.
Building a release treats a test quote whose answer moved, a price that
became null included, as a change to report, not a failure, and a project
need not hold a test quote that covers the column. So a rename or a type
change upstream ships an app that answers a blank price; only a column
marked required stops such a quote, and only at Mark final.

**Plan:** Decide whether a release may be built in that state. The
recommended rule: refuse a release whose Workbench Output declares a column
no connected frame fills with a fitting type, naming the table, the column
and the node, unless the form marks the column as allowed to be empty. The
deploy's dry run already holds the schemas the check needs.

**Acceptance:** With an output column renamed upstream, building a release
stops and names the table, the column and the Workbench Output; restoring
the name, or marking the column as allowed to be empty, lets it build. The
editor's behaviour while editing is unchanged.

**Dependencies:** The decision above; it changes what a release accepts.

**Evidence:** `src/haute/_workbench_output.py::_filled`;
`src/haute/_workbench_output.py::_unconnected`;
`src/haute/deploy/_validators.py::_workbench_form_errors`;
`src/haute/_workbench_app.py::build_release`;
`src/haute/_quote_store/_book.py::_missing`; `specs/workbench/high-level.md`.

### WB-49 — The hosted check retries its probes
**Why:** Read from the source. After a rollout, the hosted check polls the
app's health with retries, then sends two more requests once each: one
forging a signed-in principal, which the app must refuse, and one signed in,
which must be answered. A request that gets no answer, or a 5xx, counts as
"not refused", so a single dropped request raises "The app answered a
request that forged a signed-in principal (no answer: ...)" and the rollout
puts the previous release back. Failing closed is right; the wording is
wrong for a request that was never answered, and one dropped packet undoes a
good release.

**Plan:** Retry a probe that got no answer or a 5xx within the health
check's deadline. Word a probe that never got an answer as that, separately
from one the app answered wrongly.

**Acceptance:** With the first forged-principal request dropped and the
second refused, the rollout completes. With every attempt unanswered, the
rollout rolls back and says the app did not answer, not that it accepted a
forged principal.

**Dependencies:** None.

**Evidence:** `src/haute/_workbench_rollout.py::hosted_check`;
`src/haute/_workbench_rollout.py::_refused`;
`src/haute/_workbench_rollout.py::rollout`.

### WB-50 — A save that cannot land is refused before pricing
**Why:** Read from the source. Saving a quote reads the quote, prices the
values (up to the price timeout, in one of four pricing places) and only
then calls the store, whose compare-and-swap refuses a save over a version
that moved on, a final quote or an archived one. The quote just read already
holds its version, its stage and whether it is archived, so the refusal is
known before the pricing starts. An underwriter saving over a quote a
colleague just changed waits for a price that is then thrown away, and holds
a pricing place while they wait.

**Plan:** Check the version, the stage and the archive flag from the quote
just read and refuse before pricing. Keep the store's own check, which still
decides a race between the read and the write.

**Acceptance:** A save over a stale version is refused without a pricing
call, asserted by a counter on the pricing function; a save that races a
concurrent change after the read is still refused by the store.

**Dependencies:** None.

**Evidence:** `src/haute/_workbench_app_quotes.py::save_quote`;
`src/haute/_quote_store/_book.py::QuoteBook`.

### WB-51 — A form whose column name is not text is refused, not answered 500
**Why:** Reproduced. `PUT /api/workbench/form` with a `quote_details` table
whose column carries a list or an object as its `name` answers HTTP 500,
"Operation failed. Check the server logs for details."; the log holds
`TypeError: unhashable type: 'list'`. The form brings an earlier Haute's
`quote_details` columns up to date before the model checks anything: it
looks each name up in a set of retired names and builds a set of the names,
and neither works on a value that cannot be hashed. A `TypeError` raised
there is not turned into a validation answer. The same body with an unknown
key on the column is answered 422 naming it, and the same column in the form
file on disk is refused when the form loads, with a 409 naming the file and
the column. Every route whose body carries a form runs the same step; only
the save was called. The editor never builds such a body, so this reaches a
hand-made or damaged request. The 28 October audit recorded the lead for a
column's `id`, which the model now refuses by name.

**Plan:** Leave a table whose column names are not all text for the model's
own check, as the step already leaves a column that is not an object.

**Acceptance:** The two bodies are answered 422 naming the column's name,
and nothing is written. A test sends each JSON type as a column's name to
each route whose body carries a form and expects no 500.

**Dependencies:** None. Haute has no users, so the step that upgrades an
earlier form could instead be removed without a migration.

**Evidence:** `src/haute/_workbench_form.py::FormSpec._brings_quote_details_up_to_date`;
`src/haute/routes/workbench.py::put_workbench_form`.

### WB-52 — The form's save refuses a column name the pipeline cannot use
**Why:** Reproduced. Saving a form with a column named `class` or `None`
succeeds: `PUT /api/workbench/form` answers 200 and writes the form. The
tables route then refuses the form that was just saved:
`GET /api/workbench/tables` answers 422, "The Workbench Input's table
'policy' has a column 'class', which cannot name a frame's column: a column's
name is letters, digits and underscores, not starting with a digit and not a
Python keyword." The save holds a column's name to the identifier pattern
alone. The tables route and the editor's own check also refuse a Python
keyword. So the server accepts and stores a form its own pipeline cannot
read, and the refusal comes later from another route. `class`, `in`, `is`,
`as` and `or` are all names a form's author might choose. The 25 October
audit recorded the difference without running it.

**Plan:** Hold a column's name to the one rule, `is_frame_label`, when the
form is saved, with the tables route's wording.

**Acceptance:** A save with a column named `class` is refused naming the
table, the column and the rule, and the form on disk is unchanged. A test
runs one list of names through the save, the tables route and the editor's
`columnNameProblem` and expects one verdict from all three.

**Dependencies:** None.

**Evidence:** `src/haute/_workbench_folder.py::folder_name_problems`;
`src/haute/_workbench_tables.py::check_tables`;
`src/haute/_graph_utils.py::is_frame_label`;
`frontend/src/utils/workbenchForm.ts::columnNameProblem`.

### WB-53 — What a failed pricing tells the underwriter (a decision)
**Why:** Reproduced. When the pipeline fails on a quote, the underwriter app
answers HTTP 500 with the exception's own text after "The pipeline failed on
this quote:". A node that raised "connection failed: password=hunter2
token=sk-live-123 at C:/Users/analyst/secrets/db.ini" was answered with all of
it; a missing file with its absolute path; a missing column with Polars' message
and its resolved plan; a `KeyError` with the bare key. The workbench
specification prescribes this answer and says it is how the deployed pricing API
answers one. It no longer is: the deploy specification says a 500 "never carries
the exception's text, which can hold file paths and internal names", and the
deployed API answers a fixed message with a request id that the log carries. The
app's own test asserts the exception's text. The app's readers are underwriters,
a wider audience than the API's clients, and the app has no sign-in yet
(`WB-25`). `haute app impact` reads the same answer to name why each quote
failed, so the reason has one reader who needs it: the analyst. The 28 October
audit recorded the lead without a payload.

**Plan:** Decide what an underwriter is shown, and where the analyst then reads
the reason. The review's proposal: answer as the deployed API does, with a fixed
sentence that says the pipeline failed on this quote and asks the underwriter to
pass on the id shown; put that id and the exception's text in the log; and give
the impact command the reason by a way an underwriter's browser does not have.
Keep the 422 answers as they are: they name what the underwriter can correct.
Then make the two specifications say the same.

**Acceptance:** Each of the five failures above is answered 500 with the fixed
sentence and an id, and with none of the exception's text, path or key; the log
holds the exception under that id. A quote a contract error refuses is still
answered 422 with its message. `haute app impact` still names why a quote
failed. The app's test and the impact command's test are changed to match.

**Dependencies:** The decision. `WB-25` settles who can reach the app at all.

**Evidence:** `src/haute/_workbench_app.py::create_app` builds the answer in its
`price_values`; `tests/test_workbench_app.py::test_a_pipeline_failing_on_the_quote_answers_why`;
`tests/test_workbench_impact.py::test_each_quote_outcome_is_named_and_a_staging_failure_fails_the_run`;
`src/haute/deploy/_container.py` generates the deployed API's `_internal_error`,
the answer to match;
`specs/workbench/low-level.md`; `specs/deploy/high-level.md`.

### WB-54 — The release route reports a store the app cannot write to
**Why:** Reproduced on 11 November 2026 on a SQLite quote store, with
nothing patched. With the store's file read-only when the app starts,
`GET /api/app/release` reports the quotes open, so the page opens them, and
every save, note and change is then refused: the app answers each in words
(the store's file is read-only, the change not saved, the quote's values
kept), but the page offered a write it could not make. The store's check
reads the tables and never tries a write.

**Plan:** Have the store's check find a store it cannot write to (for
SQLite, a read-only file or folder) and have the release route report the
quotes closed for that reason, naming it.

**Acceptance:** With the SQLite store's file read-only when the app starts,
`GET /api/app/release` does not report the quotes open, and says why.

**Dependencies:** None.

**Evidence:** `src/haute/_workbench_app_store.py`;
`src/haute/_quote_store/_store.py`; `specs/workbench/high-level.md`.

### WB-55 — A SQLite store held exclusively, damaged or deleted is called what it is
**Why:** Reproduced on 11 November 2026, with nothing patched. A store file
cut to half its length is now said to be damaged at every start, but not
which file. While a second program holds the file exclusively, the app
answers after 14 seconds: "The quote store's database is in 'database is
locked' mode and could not be put back to a rollback journal: close
whatever else has it open and try again." The lock's error text sits where a
journal mode's name belongs. The sweep saw a store file deleted between runs
come back empty and be refused as a mismatch, with nothing saying that the
file was gone.

**Plan:** Name the damaged file. Tell an exclusive lock apart from a journal
mode the store cannot leave, and say that another program holds the file.
Say that the file is gone before making an empty one.

**Acceptance:** With the file cut short the app says at start that the
store's file is damaged, and names it. With an exclusive lock held, the
answer names the lock, not a journal mode. With the file deleted, the app
says that it is gone.

**Dependencies:** None.

**Evidence:** `src/haute/_quote_store/_store.py`;
`src/haute/_workbench_app_store.py`; `src/haute/_failure_answers.py`.

### WB-56 — A quote's address with a trailing space names no quote on SQL Server
**Why:** Reproduced on 12 November 2026 on SQLite, SQL Server 2022 and
PostgreSQL 16, in the containers CI uses. With quotes `ABC-1` and `Ré-1` in
the store, the address of `ABC-1` with a space after the reference
(`/api/app/quotes/ABC-1%20/1`) answers 404 on SQLite and on PostgreSQL. On
SQL Server it opens `ABC-1`, its archive route archives `ABC-1`, and its
activity route lists that quote's changes. A priced save sent to `Ré-1`
with the space answers 200 for `Ré-1` and stores the sheet's row in
`policy_details` under the reference with the space.

SQL Server's `=` ignores trailing spaces, also under the binary collation
the store gives a reference, and the routes hand the address's reference to
the store as it came. The specification keeps a reference trimmed and says
that "two are one reference only when they are the same". An underwriter
does not type such an address; a link pasted with a space, or a program
that calls the app, sends one. The row under the padded reference is what a
report that reads the store outside SQL Server would then fail to match.

**Plan:** Read a reference from an address as the store keeps one: a
reference that is not in its kept form names no quote, on every database,
before any statement compares it.

**Acceptance:** On each of the three databases the address with the space
answers 404 from every route that takes a reference (open, save, final,
amend, archive, restore, assign, note, activity), and no row is stored
under a padded reference. The store's server tests run it.

**Dependencies:** None.

**Evidence:** `src/haute/_workbench_app_quotes.py`;
`src/haute/_quote_store/_book.py`; `src/haute/_quote_store/_schema.py`;
`specs/workbench/high-level.md`; `tests/_store_servers.py`.

### WB-57 — The quote list finds the same quotes on every database
**Why:** Reproduced on 12 November 2026 on the three databases. The
specification has a list match "every word searched for against the
reference, quote name, insured or broker whatever their case". With one
quote whose insured is "ZÜRICH RE", a search for `zürich` or for `ZÜRICH`
finds it on SQL Server and on PostgreSQL and finds nothing on SQLite, where
`re` does find it. SQLite's `lower()` folds the 26 unaccented letters only,
and the searched word is lowered in Python before it is compared with the
column lowered by the database. The same two-sided rule means `İzmir`
finds "İzmir Sigorta" on no database, and `ΟΔΟΣ` finds "ΟΔΟΣ Ltd" on SQL
Server alone.

On SQL Server a searched word that holds `[` is read as a pattern: `[x]`
also finds "x co", and `[` alone finds nothing, where the other two
databases find "[x] co" for both. The escape applied to a searched word
covers `\`, `%` and `_` and leaves `[` out.

SQLite is the store `haute app serve` uses on an analyst's machine, so a
search tried there on an accented name fails where the hosted app answers.

**Plan:** Fold the case of the word and of the column by one rule on every
database, either both in the database or with a folded column the store
writes, and escape `[` in a searched word on SQL Server.

**Acceptance:** A search for each of `zürich`, `ZÜRICH`, `İzmir`, `ΟΔΟΣ`,
`[x]` and `[` returns the same quotes on SQLite, SQL Server and PostgreSQL,
and a quote is found by any word of its insured in any case. The store's
server tests run these searches.

**Dependencies:** None.

**Evidence:** `src/haute/_quote_store/_book.py`;
`specs/workbench/high-level.md`; `tests/_store_servers.py`.

### WB-58 — The quote list is in one order on every database
**Why:** Reproduced on 12 November 2026 on the three databases. The
specification orders a list "by reference from A to Z with its options in
order, or by insured". Eight quotes `b-1`, `B-2`, `a-3`, `A-4`, `é-5`,
`Z-6`, `10-7` and `2-8`, listed by reference, come back from SQLite and SQL
Server as `10-7`, `2-8`, `A-4`, `B-2`, `Z-6`, `a-3`, `b-1`, `é-5`, every
capital before every small letter, and from PostgreSQL as `10-7`, `2-8`,
`a-3`, `A-4`, `b-1`, `B-2`, `é-5`, `Z-6`. Nine insureds listed by insured
come back in three orders, one for each database: `_x` is first on SQLite
and SQL Server and after `É` on PostgreSQL, and `Z` is before `É` on SQLite
and after it on the two servers.

The reference column has the store's exact collation on SQL Server alone;
PostgreSQL orders it by the database's own collation. The insured is
ordered by its lower case under each database's default collation, and
SQLite lowers unaccented letters only. Within one database the pages are
sound: with quotes added and archived between pages, none is repeated or
skipped.

**Plan:** State the order in the specification, where the plain reading of
"A to Z" is PostgreSQL's, and have each database produce it, by a named
collation on the two ordered columns or by a sort key the store writes.

**Acceptance:** The eight references and the nine insureds above come back
in one order on the three databases, the order the specification states,
and the store's server tests check it.

**Dependencies:** None.

**Evidence:** `src/haute/_quote_store/_book.py`;
`src/haute/_quote_store/_schema.py`; `specs/workbench/high-level.md`.

### WB-59 — A release has one id whatever line endings its checkout has
**Why:** Reproduced on 13 November 2026 with the real executable. For one
workbench project at one commit, `haute app build` gives the release the id
`d69d65737100` when the helper files under `rating/utility` are checked out
with LF line endings and `54482649bc3f` when they are checked out with
CRLF. The plan's sweep saw the same two ids for a Windows clone and a
Linux clone of that commit. The id's own description promises that "the
same project builds the same id wherever it is checked out". It is a hash
that takes the bytes of each utility file and of each artifact as they lie
on disk.

Git for Windows turns LF into CRLF on checkout by default, wherever no
`.gitattributes` says otherwise. `haute init` writes a `.gitattributes`
that keeps `*.py` and `*.json` at LF, and in the sweep's run with it the
ids agree. A project without that file differs: one made before the file
was written, one that lost it, a shipped example. The id is what tells one
release from another, "a retrained model, an edited helper"; here it tells
an analyst's build from the CI's build of the same commit. The automated
deploy records digests of the same files in its bundle's manifest, and the
sweep saw them differ the same way.

**Plan:** Hash the text files that go into the id, and into the bundle's
digests, with their line endings normalised. Keep the bytes as they are
for binary artifacts.

**Acceptance:** `haute app build` gives one id for one commit from a
checkout with LF and from one with CRLF, on Windows and on Linux, and a
changed helper still changes the id. The bundle's digests of text files
agree the same way.

**Dependencies:** None.

**Evidence:** `src/haute/_workbench_app.py::_release_id`;
`src/haute/_workbench_app.py::_utility_hashes`;
`src/haute/deploy/_request_rows.py::record_digests`;
`src/haute/_hashing.py::content_hash`;
`src/haute/_scaffold_files.py`.

### WB-64 — The migration SQL for SQL Server leaves a column's NOT NULL as it says it does
**Why:** Reproduced on 14 November 2026. A column of the store was made
`BIGINT NOT NULL` by its owner, and the form declares it Decimal. The
migration SQL Haute writes says "Not changed here: policy_details.exposure
takes no null, unlike the store's." and then, on SQL Server, runs
`ALTER COLUMN exposure FLOAT(53) NULL`. After it the column takes nulls.
On PostgreSQL the statement changes the type only and the column still
refuses them.

**Plan:** Write the column's present nullability into the SQL Server
statement that changes its type.

**Acceptance:** After the SQL above the column on SQL Server is a float
that takes no null.

**Dependencies:** None.

**Evidence:** `src/haute/_quote_store/_migrate.py`;
`specs/workbench/high-level.md`.

### WB-66 — A test quote's rows are opened by their values after a list of codes is reordered
**Why:** Reproduced on 14 November 2026 through the designer's routes. A
table's index takes its allowed values from a params file holding `01`
and `02`. A test quote is saved with 100 against the first code and 200
against the second. The file is reordered to `02`, `01` and the quote is
opened: the sheet shows `02` with 100 and `01` with 200. Both codes are
listed as not opened, yet their amounts are kept, paired by position. The
specification has "preset rows placed by their values". With the
file unchanged the codes are still listed as not opened, and so is a
limit of 1000 whose allowed value is written `1000.00`: the saved number
is compared as text with the file's spelling.

**Plan:** Compare a saved value with an allowed value as the column's
type, and place the saved rows by the index value. Leave a row out whole
when its index value is no longer allowed.

**Acceptance:** With the file unchanged nothing is listed as not opened.
After the reorder `01` shows 100 and `02` shows 200.

**Dependencies:** None.

**Evidence:** `src/haute/_workbench_test_quotes.py`;
`src/haute/_workbench_tables.py`; `specs/workbench/high-level.md`.

### WB-70 — A creation repeated under its key is answered with the quote it made
**Why:** Reproduced on 16 November 2026 with two instances of the
underwriter app on one quote store. Both were sent the same creation, with
the same operation key and reference, a few milliseconds apart, as a page
or a proxy does when the first answer is slow. In 100 rounds the second
was answered 409 "K-000004/1 already holds the reference K-000004: open
it, or create an alternative of it" 37 times on SQL Server with read
committed snapshot off and 5 times on PostgreSQL; never on SQLite, and
not in this run on SQL Server with it on, where a worker saw 5% to 9%.
One quote is stored each time. The specification says a request repeated
under its key answers what the first did. The replay check runs before
the insert; when the first copy commits in between, the insert finds the
reference taken, and that refusal is not the one whose handler checks the
key again. The underwriter is told their own new quote is someone else's.

**Plan:** Check the operation key again before answering that a
reference is taken, and answer with the quote the key made.

**Acceptance:** 100 rounds of the pair above answer 200 twice with the
one quote on PostgreSQL and on SQL Server with either setting.

**Dependencies:** None.

**Evidence:** `src/haute/_quote_store/_book.py`;
`specs/workbench/high-level.md`.

### WB-74 — A password corrected in `.env` is used without restarting the editor
**Why:** Reproduced on 16 November 2026 with the editor's real server
and a PostgreSQL quote store whose password comes only from the project's
`.env`. With a mistyped value, Refresh of the database schema answers 409
"could not be reached, or it refused the sign-in". After `.env` was
corrected, Refresh in the same editor answers the same 409; an editor
started after the fix answers 200. The specification says the variables
are read afresh from `.env` under the process's own environment. They
are, but the editor copied `.env` into its own environment when it
started, so the old value wins every time.

**Plan:** Keep the values the editor itself loaded from `.env` apart
from the environment it was started with, so a re-read file replaces
them.

**Acceptance:** In the journey above Refresh answers 200 after the fix,
with no restart. A variable set outside `.env` still wins.

**Dependencies:** None.

**Evidence:** `src/haute/server.py`; `src/haute/_connections.py`;
`specs/workbench/low-level.md`.
