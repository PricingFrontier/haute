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
exports from the app; and test quotes analysts keep and price again.

`WB-25` is the app's sign-in, which waits on the Azure work.

`WB-38` comes from the 25 October 2026 codebase review (finding FWB-7): what it found
in the Workbench view's Allowed field.

`WB-50` comes from the 9 November 2026 codebase review. Its entry says whether it was
confirmed by running the code or read from the source; it was not run against a hosted app
or a server database.

`WB-51` and `WB-52` were reproduced on 10 November 2026 through the editor's routes. Both
come from leads the October reviews left unverified.

`WB-55` comes from the review plan's third sweep of failed writes on 11 November 2026. It
was reproduced on a SQLite quote store with a second process holding the file, as a
database browser or a backup does; no server database was used.

`WB-56` to `WB-58` come from the review plan's phase 4, which ran the underwriter app on
SQL Server 2022 and PostgreSQL 16 beside SQLite on 12 November 2026, in the containers CI
uses. The main session ran each again on the three databases. `WB-59` comes from the same
phase's carrying of projects made on Windows to Linux, and was run again on Windows.

`WB-74` comes from the review plan's phase 4, the quote store on PostgreSQL 16 and SQL
Server 2022 under repeated requests, lost connections and hand-made schemas. A worker
built the run and the main session ran it again on 16 November 2026.

`WB-78` is what the validations of 25 November 2026 left for later: a check that warns
without stopping Mark final.

## Priorities

**Start with:** `WB-50`

| Package | State | Priority | Outcome |
|---|---|---:|---|
| WB-07 | Planned | P3 | The underwriter app runs on a Databricks App, on AWS or on an internal platform as it runs on Azure Container Apps, each signing underwriters in its own way. |
| WB-08 | Planned | P3 | An export template's change starts a workbench project's release, and CI drives the packaged app's sheets in a browser. |
| WB-11 | Planned | P3 | An Export fills the analyst's own workbook template, writes a cell for each row of a table, and names its cells as a template names them. |
| WB-12 | Planned | P3 | An analyst saves the quote typed in Preview as a test quote in `tests/quotes`, prices every test quote again from Preview as the pipeline changes and accepts what moved, and CI reports the test quotes whose outputs moved. |
| WB-13 | Planned | P3 | An underwriter records how a final quote ended, and archives or reassigns many quotes at once. |
| WB-25 | Planned | P3 | The underwriter app refuses to trust sign-in headers it was not put behind. |
| WB-38 | Decision | P3 | Allowed values typed into an older form are either refused by the form schema or canonical; the Allowed field stops calling a shape the server accepts "older". |
| WB-50 | Planned | P3 | A save the store is bound to refuse is refused before the quote is priced. |
| WB-51 | Planned | P3 | A form request whose column name is not text is answered 422 naming it, never 500. |
| WB-52 | Planned | P3 | The form's save refuses a column named with a Python keyword, as the tables route and the editor do. |
| WB-55 | Planned | P3 | A SQLite store held exclusively, damaged or deleted is said as such at start, naming its file. |
| WB-56 | Planned | P3 | On SQL Server a quote address with a trailing space names no quote, as on the other databases; no row is stored under a padded reference. |
| WB-57 | Planned | P3 | A search of the quote list finds the same quotes on SQLite, SQL Server and PostgreSQL, for accented letters in any case and for a word that holds `[`. |
| WB-58 | Planned | P3 | The quote list sorted by reference or by insured is in one stated order on every database. |
| WB-59 | Planned | P3 | A release built from one commit has one id, whether its checkout has LF or CRLF line endings. |
| WB-74 | Planned | P3 | A password corrected in `.env` is used by the running editor's next Refresh. |
| WB-77 | Planned | P3 | A PostgreSQL quote store whose server keeps the connection open but never answers is refused within the statement deadline, as one that stops acknowledging is. |
| WB-78 | Planned | P3 | A validation can be a warning: shown failing, without stopping Mark final. |

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
**Why:** A workbench project's workflows validate the project and the app's release on every
pull request and on a push to `main` that changes a release input, test the packaged app
there (`haute app test`: the release served on a store made for the run alone, on SQLite and
on the server engine production's store runs, each test quote made, priced, saved, opened
again, marked final and exported through the app's routes), publish the app's image once and
roll staging out to it, and roll production out by hand
([workbench](../workbench/high-level.md)). What the release flow still lacks: the export
templates of WB-11, once they exist, start no release; and no step drives the sheets
themselves in a browser, so a page that does not load or draw is found by an underwriter.

**Plan:** The release triggers gain the export templates where their specification puts
them, as they arrive. The packaged app's test gains a browser run of the sheets: the
release's page loaded against the app `haute app test` serves, a test quote typed into it
and saved, as the editor's own browser tests drive it.

**Acceptance:** A change confined to an export template starts the release; a pull request
whose packaged app's page cannot open a quote or save one fails before any host sees it.

**Dependencies:** WB-11 (the export templates).

**Evidence:** `src/haute/_scaffold.py::workbench_ci_files` (the trigger paths and the
validate jobs); `src/haute/_workbench_app_test.py` (the packaged app's test).

### WB-11 — Exports from templates
**Why:** The underwriter app exports a final quote as the Excel workbook of cells the
analyst lays out in the Export's settings, Haute making the workbook, the release that
priced the quote named in it and the export recorded in the quote's activity; Preview runs
the same Export on the sample ([workbench](../workbench/high-level.md)). An Export writes
files alone: a saved quote is in the store's database already (decided 22 November 2026).
What the decided first format left for slices of their own: a template, the analyst's own
workbook filled in place of one Haute makes; a cell for each row of a table, with a row for
each value; and cells named in a template in place of an address. The CSV and the workbook
of a sheet per table are laid out and drawn disabled.

**Plan:** Each a slice. A template lives in the project beside the form and ships in the
release, so a change to one starts the workflow of WB-08 and is tested there against the
test quotes; an export of a template keeps the template the release ships, so a quote
exported again later uses the current one. Then the cell for each row, and the named cells,
both reading the template's own names.

**Acceptance:** An Export naming a template fills it from a final quote in the app and from
the sample in Preview, its other sheets and formatting kept; a change to the template
starts the release, and the template's output for the test quotes is checked in CI.

**Dependencies:** WB-08 for the release trigger and the check in CI.

**Evidence:** `src/haute/_workbench_export.py` (`quote_export` and `preview_export`, an Excel
workbook of cells written); `src/haute/_workbench_form.py::ExportWidget` (the Export's
settings, read strictly); `frontend/src/workbench/panel/ExportSettings.tsx` (where they are
chosen).

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

**Plan:** Decided with the owner on 22 November 2026: an explicit trust setting that
`haute app rollout` sets. The app trusts the host's sign-in headers only while an
environment variable on the Container App says the host's sign-in stands in front of it;
`haute app rollout` sets it after reading the Container App's authentication and finding
it enabled and requiring a sign-in for every request, and refuses the rollout otherwise.
Without the variable every request but the health check is refused 401, naming it. `haute
app serve`, which signs the analyst in itself, is unchanged.

**Acceptance:** A release started without the variable refuses a request carrying Easy
Auth's headers (401, naming the variable); `haute app rollout` onto a Container App whose
authentication is off, or lets a request through unsigned, stops before the image is
rolled out, saying so, and onto one that requires sign-in sets the variable and the
hosted check passes; `haute app serve` signs the analyst in as before.

**Dependencies:** None.

**Evidence:** `src/haute/_workbench_app.py::create_app` (the principal read from the
headers when the release names its host); `src/haute/_workbench_rollout.py` (the Container
App's authentication read as a rollout runs, `_client_id`).

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

### WB-77 — A PostgreSQL server that keeps the connection and never answers
**Why:** Read from the source and libpq's documentation on 22 November 2026. A
PostgreSQL connection's deadlines are libpq's
connect timeout (a server that never finishes the sign-in), the TCP user timeout
(data the host never acknowledges) and the server's own statement timeout. None
bounds a statement on an established connection whose peer acknowledges what is
sent and never replies: a server process stuck where its statement timeout cannot
fire, or a proxy that keeps the socket and stops relaying. psycopg has no
client-side read deadline, so the worker thread waits until the operating system
closes the socket. A pooler's own wait queue (PgBouncer's `query_wait_timeout`)
bounds the common case of a pooled server too busy to take the statement. SQL
Server's query timeout is the driver's, client-side, so it has no such gap.

**Plan:** A client-side deadline on each PostgreSQL statement and on the pool's
ping: past the statement timeout and a grace, the connection is cancelled, then its
socket shut, so the statement fails as unreachable. One watchdog serving every
connection, not a thread per statement.

**Acceptance:** Through a proxy that relays the sign-in and then stops relaying, a
quote read is answered 503 within the statement deadline and its grace, and the
connection is not handed out again.

**Dependencies:** None.

**Evidence:** `src/haute/_quote_store/_providers.py::deadline_hook`;
`src/haute/_quote_store/_providers.py::make_server_engine`.

### WB-78 — A validation that warns
**Why:** Decided with the validations on 25 November 2026. Every check stops Mark final:
a required column without a value, a value outside its range or allowed values, and a
validation, a True/false output the pipeline fills, that does not hold true. Some of a
project's conditions are for an underwriter to weigh, not for the form to enforce: a
premium far from the last one, a rate under the technical rate. Today those either stop
the quote or are not validations at all.

**Plan:** To be designed. The questions it has to answer: whether a level is a
validation's alone or a required column's, a range's and allowed values' too; whether
passing over a warning takes a reason, and where that is recorded; and how the toolbar's
Validation button tells a warning from what stops the quote.

**Acceptance:** A quote failing only warnings can be marked final, and one failing a check
that stops is refused as now.

**Dependencies:** None.

**Evidence:** `src/haute/_workbench_checks.py::quote_checks`;
`src/haute/_quote_store/_book.py` (`QuoteBook.finalise`);
`frontend/src/workbench/ValidationButton.tsx`.
