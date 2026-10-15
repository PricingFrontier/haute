# Workbench roadmap

## Scope

The workbench inside Haute is specified in [the workbench
specification](../workbench/high-level.md): the form file and its tables, the Workbench
Input and Workbench Output, the Workbench view with its schema editor, its sheets, the
sample priced live and Preview, and the form on the save ledger. How a deployed pipeline
reads a quote in the workbench's shape is specified in [the deploy
specification](../deploy/high-level.md). The packages below are what neither covers: the
underwriter app, which carries the sheets and the pipeline to the host a project uses; the
delivery of a workbench project through CI, from a merge to `main` to that app; the store
the app saves quotes to on the server; how underwriters manage their quotes in the app;
exports from the app; the test quotes analysts keep and re-price in Preview; and the actions
that fill the sheets from databases and APIs.

`WB-17` to `WB-22` fix what the [review of 15 October 2026](workbench-review-2026-10-15.md)
found in what pull requests #303 to #309 built. Each names the review's findings it closes
(`CX-n`, `FB-n`, `TQ-n`, `SD-n`, `MS-n`), and the review holds their evidence. They are
taken by priority, `WB-18` first: it closes the risk that a price shown is not the one the
pipeline gives, then the P2 packages in order, and `WB-22` merges the mechanisms written
several times once the others have fixed their behaviour.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| WB-07 | Planned | P3 | The underwriter app runs on a Databricks App, on AWS or on an internal platform as it runs on Azure Container Apps, each signing underwriters in its own way. |
| WB-08 | Planned | P2 | A merge to `main` takes a workbench project's form, pipeline and app, tested together in CI, to staging and, after approval, to production on the project's host, Azure Container Apps first. |
| WB-09 | Decision | P2 | A Button on a sheet, or a lookup beside a key cell, runs an action the project declares against a database or an API and fills input cells with what comes back, through the server with no credential in the browser, while pricing stays as pure as it is. |
| WB-10 | Planned | P2 | The quote store runs on the SQL instance the project's IT team provides, migrated by the deploy under an identity of its own, with the release before a candidate proved to work on the schema the candidate leaves. |
| WB-11 | Decision | P2 | An underwriter exports a saved quote from the app in the formats the project needs, naming the release that priced it. |
| WB-12 | Planned | P3 | A test quote whose response runs past what Preview shows is saved, run and compared in full, as every other test quote is. |
| WB-13 | Planned | P3 | An underwriter compares a quote's revisions, sees which quotes are about to lapse, records how a final quote ended, and archives or reassigns many quotes at once. |
| WB-17 | Planned | P2 | Test quotes behave in the panel and in git as quotes do in the app: the newest answer wins, one read per quote, drivers registered once and quietly, and the report compares a branch with its merge base. |
| WB-18 | Planned | P1 | What the sheets show is what is priced: a price is stale whenever its pipeline, data or source changes, a pipeline that does not read the sheets is refused, every value priced is visible on its cell, and one rule says whether a quote is typed. |
| WB-19 | Planned | P2 | The form and its Save say what is wrong and keep what was typed: readable refusals, unique ids, no silent no-op, recovery after a lost answer, and the node copies kept outside the undo history. |
| WB-20 | Planned | P2 | The underwriter app's quote life is exact: a derived quote opens clean and new, drafts and history follow what is on the sheet, failures say why, and the release id changes with what prices alone. |
| WB-21 | Planned | P2 | Building the sheets is exact and keyboard-complete: a drag lands where the pointer puts it, a gesture ends when the pointer does, the panels keep the selection, and the sheets read as laid out. |
| WB-22 | Planned | P2 | Each concern has one mechanism: one way to drop a stale answer and take turns, one writer into the project, and the three largest view files split along their jobs. |

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
**Why:** `haute init --workbench` writes the `[workbench]` table and a blank form and
generates the target's ordinary workflows: Validate (a dry-run deploy scoring the test
quotes, which also holds the Workbench nodes' copies of the tables to the form), Deploy →
Staging, Smoke Test Staging (each test quote a request of its own, held to its expected
output), Impact Analysis, approval, Deploy → Production, except that the workflows for Azure
Container Apps, AWS ECS and GCP Cloud Run leave the smoke and impact out, since those
targets build and push an image and update no service; the generic container target, which
also only builds and pushes, keeps them. A push to `main` that changes the pipeline, its
data, models or test quotes, `haute.toml`, the dependency files or, for a workbench project,
the form starts the deploy. What a workbench project releases is not a scoring endpoint but
the underwriter app, which `haute app build` writes and underwriters use by hand, and
nothing in the workflows builds, tests or deploys it. Pricing analysts work on their own machines: Save writes the save branch,
Commit makes a milestone, Push publishes it, a pull request to `main` is opened and merged
on the provider, and the merge is the release. Against that flow: impact sends batches of a
flat portfolio sample, which a pipeline reading one quote per request refuses for the batch
and could not read for the shape, and its report compares top-level numeric columns, so a
price inside a response table is never compared; production runs `haute deploy` again rather
than promoting what staging tested, and the image is tagged with the commit's short hash,
which a rebuild can point at other bytes; and the Azure Container Apps target pushes an
image and rolls no service to it. A milestone Commit sweeps tracked files only, so a file
the workbench adds, the test quotes of WB-12 or the store's migrations, needs tracking from
the start.

**Plan:** The fork is at `haute init --workbench`, which generates a workbench project's
workflows; the behaviour lives in Haute's commands, which read the workbench settings, so
the YAML stays thin and alike across providers, and a refresh command regenerates a
project's workflows without rewriting its pipeline or form, as `haute init --force` would.
The deploy triggers gain each release input as it arrives, where its specification puts it:
the app's entry point and the export templates; the store's migrations ship with Haute, so
the lockfile already carries them. The app's delivery builds each release once. On every
pull request, Validate and the packaged app tested in the CI runner before any host sees it,
against SQLite and once against the server engine IT's instance runs (a service container),
since SQLite proves neither that engine's migrations nor its types: the test quotes of WB-12
re-priced one request each against their accepted outputs, a quote saved, reopened and, once
WB-11's formats exist, exported through the app, and the sheets driven in a browser. On the
merge to `main` alone, never on a push of the save branch: the merge commit built once, the
packaged app's tests run again on that build, the image published to the registry and its
digest recorded; the staging database migrated and the staging app rolled to that digest,
never to a tag; a hosted check that the expected release runs, that a request without
sign-in is refused and that the app reaches its database; impact re-pricing a bounded sample
of saved quotes from the store, through a read-only identity, on the candidate and the
production app, comparing within each quote's response tables (a premium inside a table, a
schedule's rows matched by key) and naming a quote whose shape no longer compares; for a
first release, with no production app or no saved quotes, impact reports that there is no
baseline and the candidate answers for the committed test quotes alone, while a production
app that fails fails the check; approval; then production migrated and rolled to the same
digest, checked the same way, and the release that runs recorded. Rollouts take a
maintenance window: the old revision stops, the migrations run, the new revision starts, and
no traffic is split; a new revision that fails to start or fails its check after the
migrations is replaced by the previous release, started again on the migrated schema, which
the migrations of WB-10 keep it able to read and write, and the run fails. The host step is
small, and the project's IT team provides what it deploys onto (the Container App, the
registry, the identities and the database): it publishes, rolls the environment to the
release, waits until the release runs and reports it, and creates nothing. Azure Container
Apps first; other hosts follow with the same commands.

**Acceptance:** A project initialised by `haute init --workbench` for Azure Container Apps
and merged to `main` runs its generated workflow to a staging app and, after approval, to
production, with the digest CI tested deployed to both, a re-run of the workflow included, a
maintenance window around each rollout and the release that runs recorded; a change confined
to an export template starts the workflow; the packaged app passes the same tests against
SQLite and the server engine before any host sees it; a push of the save branch releases
nothing; a request to the hosted app without sign-in is refused; impact reports a change to
a premium inside a response table; the first release, onto an empty database with no
production app, reports that there is no baseline and still waits for approval; a candidate
that fails after the migrations leaves the previous release running on the migrated schema
and the run failed.

**Dependencies:** WB-10 (the store and its migrations), WB-11 (the export
the app's tests make, once its formats are decided) and WB-12 (the test quotes). The Azure
resources and identities, provided by the project's IT team. Shared ground with
[BUG-23](bugs.md#bug-23--the-impact-steps-portfolio-sample-is-in-the-repository) (impact's
input) and [BUG-24](bugs.md#bug-24--container-deploys-log-in-to-their-registry) (the
registry login before a push).

**Evidence:** `src/haute/_scaffold.py::github_deploy_yml`, `::github_deploy_prod_yml`,
`::gitlab_ci_yml`, `::azure_devops_yml` and `::_build_only_notice` (the generated workflows,
their triggers, production's second deploy, and the smoke and impact left out for Azure
Container Apps, AWS ECS and GCP Cloud Run); `src/haute/cli/_init_cmd.py::handle_init`
(`--workbench` writes the table, the blank form and the form's deploy trigger, and nothing
of an app); `src/haute/deploy/_impact.py::score_endpoint_batched` and `::build_report`
(batches, and top-level numeric columns compared); `src/haute/cli/_impact.py::handle_impact`
(a first deploy reported as such); `src/haute/_workbench_input.py::workbench_request_frames`
(one quote per request); `src/haute/deploy/_validators.py::score_test_quotes` (each case a
request of its own); `src/haute/deploy/_container.py::build_and_push_image` and
`::deploy_to_platform_container` (a short-hash tag, built and pushed only);
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
credentials and takes a service principal's, used today to snapshot tables; and a
credential policy for the editor's data access, environment variables named in the docs
and never a value in a file. What does not exist: a driver for any database but
Databricks, an outbound HTTP pattern, a place to declare an action and bind it to cells,
a component on the sheets that is neither a Table nor a Collection, and a credential model
for a project whose sheets reach several systems.

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
the action's bindings beside the schema in the form file, since they name tables and
columns by id and version with the sheets; the source's connection and the names of its
credential variables in `haute.toml`; the values in the environment, never in a file the
ledger pushes. Whether a many-row result replaces the table's rows or appends to them.
Whether a press in Build writes the sample, which Save then writes to disk, or actions
run in Preview alone. Then the slices: the action model, read strictly and written
canonically as the form is, with parameters bound to input columns or constants and a
result mapped onto one input table's columns, typed by the column; the sources table of
`haute.toml`; a route under the workbench router that takes the sheet's values, resolves
the bindings, runs the source on the server with a row cap and returns rows in the
sample's shape; the Button component, the first component that is neither a Table nor a
Collection, so the kinds, the factory, the problems check and the strict reader
generalise with it, and a bound column that leaves the schema is flagged on the Button as
a missing column is on a component; a lookup beside a key cell as the same action from a
column property; and the underwriter app running the same actions on its server.

**Acceptance:** With a source declared in `haute.toml` and an action in the form, pressing
its Button in Preview fills the bound input cells with the query's values, typed by their
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
`frontend/src/workbench/widgetKinds.ts` and
`frontend/src/utils/workbenchForm.ts::createWidget` and `::widgetProblems` (the two kinds
of component); `src/haute/_workbench_form.py::FormSpec` (the strict reader);
`src/haute/routes/workbench.py` (the workbench router);
`src/haute/_databricks_io.py::_validate_select_clause`, `::_connection_settings` and
`::_service_principal_credentials` (the Databricks SQL reach); `pyproject.toml` (the
Databricks extra); `docs/deployment/targets/databricks.md` (credentials as CI secrets
and the editor's data-access pair).

### WB-10 — The quote store
**Why:** Underwriters' quotes are kept on a SQL instance the project's IT team provides,
apart from the app, and the same store runs on SQLite on an analyst's machine and in CI
([workbench](../workbench/high-level.md)): quotes with numbered revisions written whole,
each keeping the form it was keyed into, and Haute's numbered migrations, which the store
refuses to open a database without. Nothing of the server side exists: no backend for the
engine IT's instance runs, no step in a deploy that migrates the instance before a release
starts on it, and no proof that the release before a candidate still reads and writes the
schema the candidate's migrations leave, which rolling a failed release back depends on.

**Plan:** Which engine, Azure SQL or Azure Database for PostgreSQL, is the IT team's to name
and is recorded here before the backend's slice. The backend is that engine's driver behind
an optional extra, as the Databricks clients are, signing in with the app's managed identity
where the engine allows it, with the store's tests run against SQLite and that engine alike
(a service container in CI), and the form copies kept once through that engine's own
insert-if-absent. The deploy's migrate step runs `haute app migrate` under an identity that
owns the schema, before the release rolls out, while the app's own identity reads and writes
data only. A release's migrations keep the previous release's reads and writes working (a
column or table added, nullable or with a default, no constraint the previous release's
writes would break, nothing dropped), and each records the oldest Haute that can still use
the database; that is proved by running the previous release's store tests against the
schema the migrations leave, so the previous release can be started again after a failed
rollout; removing a column is a step of its own after the release that stops using it.

**Acceptance:** The same store tests pass against SQLite and the server engine; a deploy
migrates IT's instance under the schema's identity before the release starts, and the app's
identity cannot change the schema; the previous release's store tests pass against the
schema a candidate's migrations leave, and the previous release starts on it; no connection
value appears in the form file, `haute.toml`, an API response or a log.

**Dependencies:** The SQL instance and its identities, provided by the project's IT team,
and the choice of engine above. WB-08 (the deploy that runs the migrate step).

**Evidence:** `src/haute/_quote_store/_store.py::open_store`, `::migrate_store` and
`::QuoteStore` (the store on SQLite); `src/haute/_quote_store/_tables.py::METADATA` (Haute's
schema); `src/haute/_quote_store/_store.py::_keep_form` (a form kept once, by SQLite's
insert-if-absent and otherwise a read first).

### WB-11 — Exports from the app
**Why:** Underwriters take what they need out of the app themselves: a quote's tables for a
spreadsheet, a file another system imports, a document for a broker. Nothing in Haute
exports a quote; the sheets show it and Preview's results stay in the browser.

**Plan:** Decision first, recorded here before the first slice: which formats the first
release needs (the quote's tables as CSV or a spreadsheet, an import file for another
system, a document from a template), and whether a quote exported again later reproduces the
template it was first exported with or uses the current one. Then exports of a saved quote's
revision, never of an unsaved draft passed off as priced, so every export names the revision
and the release that priced it; run on the app's server for the signed-in underwriter and
recorded on the quote. Templates and mappings, where a format needs them, live in the
project beside the form and ship in the release, so a change to one starts the workflow of
WB-08 and is tested there against the test quotes of WB-12.

**Acceptance:** An underwriter exports a saved quote in each format decided, and the file
holds the quote's values and prices and names the release that priced them; an unsaved quote
is not exported as priced; each export is recorded against its quote; a change to a template
starts the workflow, and the template's output for the test quotes is checked in CI.

**Dependencies:** The decision above.

**Evidence:** `frontend/src/utils/sheetValues.ts::pricedRows` (the priced rows the sheets
show); `frontend/src/workbench/useSheetValues.ts` (the one path for a sheet's values);
`src/haute/_workbench_tables.py::quote_schema` (a quote's columns as typed).

### WB-12 — Test quotes in Preview
**Why:** Analysts keep test quotes in Preview ([workbench](../workbench/high-level.md)):
saved from the sheets, opened back onto them, run in bulk on a pipeline fixed when the run
starts, accepted and deleted from a panel, kept in a SQLite file committed with the project
that git diffs as text and merges quote by quote, and priced again by deploy's check and the
smoke, with a report of each pull request's changes to them. A test quote keeps its response
whole only while Preview shows it whole: its pricing goes through the preview route, which
answers at most 10,000 rows of a table, fewer for a wide one, and a response cut short is
refused rather than kept cut. A quote whose many-row table runs past that cannot be a test
quote.

**Plan:** A pricing for test quotes that answers the Workbench Output's response whole for
the editor's graph, apart from the preview route's limits: the patched graph run as the dry
run runs a deployed pipeline, on the worker pool with its admission limits, answering the
response document a deployed pipeline gives. The panel's saves and runs use it; Preview
keeps the preview route for what it shows.

**Acceptance:** A test quote whose many-row table holds more rows than Preview shows is
saved, run and compared in full, a change in a row past Preview's limit listed as any other
is; a response too large for the worker's limits is refused naming its size; Preview itself
prices as it does now.

**Dependencies:** None.

**Evidence:** `frontend/src/workbench/priceSample.ts::priceSample` (the preview route's row
limit, and a table cut short refused); `src/haute/executor.py::_preview_row_limit_for_width`
(Preview's cell limit); `src/haute/_workbench_output.py::workbench_response` (the response
whole, as a deployed pipeline gives it);
`src/haute/deploy/_validators.py::score_test_quotes` (the dry run's pricing of each quote
alone).

### WB-13 — Managing quotes in the underwriter app
**Why:** The underwriter app opens on its quotes ([workbench](../workbench/high-level.md)):
the Quotes page lists them by who they are with, by stage and by archive, searched and
ordered, from their quote details, Haute's `quote_details` table, whose row each revision
keeps; a quote has a reference, numbered revisions each saved priced or unpriced and
priced again on the server, a handover note, an assignee, a
final revision reopened with a reason, an archive, and the duplicates, renewals and
alternatives made from it, with a history of who did what; a reader sees every quote and
changes none. What underwriters still keep elsewhere is the rest of a quote's commercial
life: two revisions cannot be compared side by side; nothing reads a quote's validity
date, so nothing says which offers are about to lapse; nothing records whether a final quote was
bound, declined or not taken up, so the lists cannot tell open work from closed; and
archiving or reassigning many quotes is done one at a time.

**Plan:** Decisions first, recorded here before each slice: which outcomes a quote can close
with, and whether a project names its own; and how near the end of a quote's validity, its
`quote_details.valid_until`, the Quotes page warns. Then, each usable on `haute app serve`:
a comparison of two revisions, values and prices side by side, the rows of a many-row table
matched by their keys rather than their places; a quote's validity, read from its
`valid_until`, with an Expiring soon list;
outcomes, recorded on a final quote with who and when and listed apart from the open quotes;
and archiving and reassigning a selection of quotes at once, each checked against its
version, a quote moved on since named without undoing the rest. Exports stay WB-11's.

**Acceptance:** Two revisions of a quote are compared with each changed value and price
named, the rows of a many-row table matched by their keys; a quote whose validity ends
within the warning is listed under Expiring soon, and one past it says so; an outcome
recorded on a final quote is listed with who recorded it and when, and the quote leaves the
open lists; a selection of quotes is archived or reassigned at once, a quote moved on since
refused by name and the rest changed.

**Dependencies:** The decisions above.

**Evidence:** `src/haute/_quote_store/_book.py::QuoteBook` (the quotes, their changes and
lists, without outcomes or validity); `src/haute/deploy/_validators.py::response_changes` (a
comparison by place, not by key); `frontend/src/app/QuotesPage.tsx::QuotesPage` (one quote
changed at a time).

### WB-17 — Test quotes in the panel and in git
**Why:**
- The test-quote panel's list and opens are applied in whatever order they arrive, with no
  check of the document they were asked for. A late open replaces the sample opened after
  it, and an open made before a branch switch writes values carried onto the old form into
  the new one (review CX-6).
- Opening a quote, and answering any write, reads every quote's summary to find one, so a
  run of N quotes reads N² summaries (CX-9).
- Every read registers the git drivers again through three `git config` calls, about 7
  seconds of a 30-quote run, and a registration that fails is not reported (FB-54).
- The drivers run as `python -m haute.cli._test_quotes`, which imports the module twice and
  prints a RuntimeWarning on every diff and merge (FB-55).
- The GitLab report job compares the merge request's head with the target's tip, not their
  merge base, so test quotes accepted on the target since the branch point read as this
  branch's (MS-1).
- Three smaller gaps (FB-57):
  - A conflict in the file, a clone without the drivers, and a stale interpreter path all
    keep ours safely, but none of them says how to resolve.
  - `haute init --workbench` writes the file without its `.gitattributes` line, which the
    first read then writes.
  - The GitHub report job's comment fails on a fork's pull request.

**Plan:**
- The store's `generation` ticket also covers `load` and `openQuote`, and an open is
  applied only while the form it was asked for is still the one shown.
- `QuoteStore.summary(quote_id)`, the one summary query with a `where`, serves the open
  and every write.
- The drivers are checked once per process for each project and interpreter. A failed
  registration is reported as a warning on the write's capture.
- The drivers run from an entry module the CLI package does not import.
- The report compares with `git merge-base HEAD <base>`, and the GitLab job fetches the
  full history.
- The driver's conflict message names the resolution: take a side with
  `git checkout --ours` or `--theirs`, then run and accept the quote in Preview.
- `haute init --workbench` registers the drivers when it is inside a repository.
- The GitHub job comments only on a pull request from the same repository.

**Acceptance:**
- Two opens answered in reverse order leave the later one on the sheets.
- A run of 30 quotes makes one registration check.
- `git diff` of the file prints no warning.
- A GitLab report on a branch behind its target lists only the branch's own changes.

Each has a test.

**Dependencies:** None.

**Evidence:** `frontend/src/stores/useWorkbenchTestQuotesStore.ts::openQuote` (no ticket);
`src/haute/routes/workbench.py::_written` and
`src/haute/routes/workbench.py::open_test_quote` (every summary read for one);
`src/haute/_test_quotes.py::ensure_test_quote_git` (three `git config` calls per read);
`src/haute/cli/_test_quotes.py::merge_files`; `src/haute/cli/_test_quotes.py::report` (the
base read at its tip); `src/haute/_scaffold.py::gitlab_ci_yml`.

### WB-18 — What the sheets show is what is priced
**Why:**
- **The pricing basis leaves the pipeline out.** Preview prices again when the form's
  pricing basis changes or Preview shows again, and judges a price stale by the same basis;
  the pipeline, the data source and the data files are not part of it.
  - The Assistant, a branch switched to one with the same form, a data file replaced
    through Files, and an edit on disk all change what the sample prices to while Preview
    stays shown. The old price stays undimmed, and a pricing under way when they change is
    kept as current (review CX-1).
  - Test-quote runs judge staleness by the pipeline and the source as well, but not by data
    files.
- **A pipeline that does not read the sheets is priced anyway.** Preview patches the
  top-level workbench nodes and checks only that a Workbench Output exists. A Quote Input
  left beside a Workbench Output, which the singleton rules allow, or a Workbench Input
  inside a submodel, never receives the typed values: every value typed gives the same
  premium, undimmed (FB-13).
- **Values are priced but not shown.**
  - A held value that a params file no longer allows draws as a blank select while it is
    priced; renewing or carrying a quote reaches this (CX-4).
  - A tick or text left in a column since retyped draws as empty and unmarked, while the
    server refuses the pricing naming that cell (FB-16).
- **"Is anything typed" is decided five ways:**
  - Preview's rule, with hidden columns out;
  - the app's pricing, with hidden and app-set columns out;
  - the app's Save and save body, with app-set columns out but hidden values counted;
  - the test-quote save's rule;
  - the server's, which counts the defaults the specification says are not typing.

  A value left only in a hidden column enables Save, which the server refuses with 422
  (CX-7, SD-1). Preview and the app also price through two loops with different
  late-answer rules (FB-12).
- **Smaller defects in reading what was typed:**
  - Date cells and date conditions commit on every keystroke; after a pause, a year typed
    digit by digit is priced as year 2 (FB-31).
  - A press on a select may hold the drawn sections until the next click, because
    Chromium's select menu takes the press's release (FB-30, to confirm in Chrome first).
  - Integer text is typed through `float`, so a value past 2^53 changes silently (CX-25).
  - The schema editor reads numeric allowed values with `Number()` rather than the
    typed-number rule (CX-8).
  - The params reader chooses Windows-1252 from a Polars error message, so a header that is
    not UTF-8 is offered mangled and saved so (FB-15).
  - The typed-number and filled rules have no shared fixture, where visibility and preset
    rows have one (FB-18).
  - Tracing a Workbench Output fed straight from a Workbench Input fails, because the
    redirect drops the source port (CX-28).
  - Editor pricing is one preview job per output table (CX-13).

**Plan:**
- One editor pricing context made of the document generation, the graph's fingerprint and
  submodels, the data source, and an epoch moved by every write of a data file. It is part
  of Preview's basis, its scheduling and its answer check, and of the test quotes' run
  basis.
- `priceSample` refuses a pipeline without a top-level Workbench Input, naming what to
  replace or move.
- A select holding a value outside its options shows that value as a marked extra option,
  and `cellProblem` marks a value not of its column's type.
- One `quoteEligible(form, values, locked)` rule (visibility, preset rows, app-set columns,
  defaults) is used by Preview, the app's pricing, its Save and save body, and the test
  quotes. The server's check leaves defaults out the same way.
- Both stores share one pricing loop, each keeping only what it prices and through what.
- Date cells and conditions go through `CommittedTextField type="date"`.
- There is no hold for a press on a select, once FB-30 is confirmed in Chrome.
- Integral text is parsed with `int`, and numeric options are checked with
  `parseTypedNumber`.
- The params reader chooses its encoding from the bytes, and refuses a header holding
  U+FFFD.
- A `typedText` fixture is read by both test suites.
- The trace redirect carries the source port.
- A preview operation for several ports, if CX-13's measurement warrants it.

**Acceptance:**
- After the Assistant edits the pipeline, a branch switch, or a data file replaced,
  Preview's outputs dim and are priced again.
- A pipeline with a top-level Quote Input and a Workbench Output says why it cannot be
  priced.
- A disallowed or mistyped held value is visible on its cell.
- Save is off exactly when the server would refuse with "Type the quote first".
- One date typed is one edit and one pricing.

Each has a test that fails today, and the fixture pins the typed-number rule on both sides.

**Dependencies:** None.

**Evidence:** `frontend/src/stores/useWorkbenchPricingStore.ts::priceNow` (a late answer
dropped only when the pricer changes);
`frontend/src/stores/useWorkbenchTestQuotesStore.ts::currentBasis` (the test quotes' wider
basis); `frontend/src/workbench/priceSample.ts::priceSample` (the Output checked, the Input
not); `src/haute/_graph_shape.py::SINGLETON_NODE_GROUPS` (either request input allowed);
`frontend/src/utils/sheetValues.ts::cellProblem`;
`frontend/src/utils/sheetValues.ts::quoteTyped`;
`frontend/src/stores/useWorkbenchPreviewStore.ts::priceOnce` (the app's typed rule and
loop); `src/haute/_workbench_app_quotes.py::_prepared` (the server's typed rule);
`src/haute/_workbench_tables.py::_typed`;
`frontend/src/workbench/steadyPreview.ts::useHeldWhilePressed`;
`frontend/src/utils/workbenchForm.ts::schemaProblems`;
`src/haute/_workbench_params.py::_read` (the encoding chosen from an error message).

### WB-19 — The form and its Save
**Why:**
- **Unreadable refusals.** A request body the form routes refuse is answered with
  FastAPI's default 422, which repeats the whole form in pydantic's `input`, and the
  browser shows that list as its JSON text. A table the analyst names `quote_details`,
  which the editor flags only as a duplicate label while the server refuses it, turns Save
  into a 5 KB toast and fails Preview's pricing the same way (review FB-19).
- **Repeated ids.** Nothing refuses a repeated id in a form, and the editor's removals act
  on every match. A hand-edited or merged file holding a copied block draws two components
  with one id, and Delete removes both (FB-22).
- **Saves that fail quietly or lose typing.**
  - A Save queued behind a read can end with nothing saved and nothing said (FB-21).
  - A save that landed but lost its answer leaves the next save refused as stale, and
    Reload loses what was typed since, though the file holds exactly what was sent (FB-20).
- **A refit mistaken for a read.** The view refits the zoom when the history looks like a
  form read anew, and a save after an undo to the bottom of the stack looks the same
  (FB-23).
- **Locking.** The form save checks and writes outside the repository lock a branch switch
  holds, while the test-quote writes hold it throughout (CX-24).
- **The node copies and the undo history.**
  - A copy update from a fetch is an undo step, so an Undo leaves the copy behind until the
    next save (FB-17).
  - An Undo inside a save's wait for pending commits can save a stale Workbench Output
    copy, which deploy then refuses (CX-10).
- **Mirrors and generated types.**
  - The mirrored constants and limits are pinned on one side or neither: the blank sheet's
    width and section height, Rows 1-50, Columns 1-12, and decimal places (FB-24).
  - `WorkbenchTestQuoteChange`'s values are generated as objects, and the client shadows
    the type with a cast (CX-12).
- **Specification and decisions.**
  - Two low-level invariants describe the retired Build and Preview model (SD-2).
  - The form learns of a change on disk only when the pipeline file changes too (FB-26).
  - A merged file whose geometry is off cannot be opened in the editor that would repair
    it (FB-27).

**Plan:**
- **Readable refusals.**
  - A `RequestValidationError` handler keeps FastAPI's list but leaves out `input` and
    `ctx`.
  - `apiErrorMessage` reads a list detail's first place and message.
  - `schemaProblems` names a table called `quote_details` in the server's own words.
- **Unique ids.** A `FormSpec` validator refuses repeated page, section, component, table
  and column ids, naming the id, and `newId` is checked against the ids in use.
- **Saves.**
  - `save` says why when its turn finds the form being read.
  - On a stale refusal, the store reads the file and takes its revision when the file holds
    exactly what was sent.
- **Refit on a read alone.** The form store counts its reads, and the view refits on a read
  alone.
- **Locking.** `repository_mutation` is held across the form save's check, write and
  capture.
- **The node copies.** Copies applied from a fetch are committed without a history entry.
  They are checked again after pending commits settle and before the pipeline is saved.
- **Mirrors and generated types.**
  - The mirrored constants and limits are pinned by the existing cross-language test, with
    decimal places through `PIVOT_DECIMAL_PLACES_MAX`.
  - The contract generator maps an untyped schema to `unknown`.
- **Specification and decisions.**
  - The low-level invariants are rewritten for Build's new quote, editor Preview's sample
    and the app's quote.
  - FB-26 and FB-27 are decided with the owner before a slice.

**Acceptance:**
- Naming a table `quote_details` shows the server's sentence in the schema editor and in
  the toast.
- A form with a repeated id is refused at read and at save, naming the id.
- A save after a lost answer is adopted when the file matches what was sent.
- An undo never leaves a node copy behind.
- The limits test fails when one side's limit changes.

Each has a test that fails today.

**Dependencies:** None.

**Evidence:** `src/haute/routes/_error_handlers.py` (no validation-error handler);
`frontend/src/api/errors.ts::apiErrorMessage` (a list detail shown as text);
`src/haute/_workbench_form.py::FormSpec` and
`src/haute/_workbench_form.py::_check_quote_details`;
`frontend/src/utils/workbenchForm.ts::newId`;
`frontend/src/utils/workbenchForm.ts::removeWidget` (every match removed);
`frontend/src/stores/useWorkbenchFormStore.ts::flush`;
`frontend/src/stores/useWorkbenchViewStore.ts` (the refit inferred from the history);
`src/haute/routes/workbench.py::_save_form`;
`frontend/src/hooks/useWorkbenchTables.ts::useWorkbenchTables`;
`frontend/src/App.tsx::saveProjectNow`; `src/haute/schemas.py::WorkbenchTestQuoteChange`;
`frontend/src/api/workbench.ts::compareTestQuote` (the cast).

### WB-20 — The underwriter app's quote life
**Why:**
- **Derived quotes.**
  - A Duplicate, Renew or Create alternative opens "Unsaved": its baseline is the empty
    quote, not the carried values, so the leave guard asks, a draft is kept, and the next
    derivation offers the carried values back as unsaved changes (review FB-1).
  - It also carries its source's reference, stage, assignee and archived flag until its
    first save, showing them and pricing with them (FB-2).
- **Drafts.**
  - A value typed back to the saved one leaves the old draft, which is offered again after
    a reload (CX-19).
  - Archive then Restore offers back the values already on the sheet (FB-4).
  - A restored draft drops cells the sheets no longer have without saying so, and its
    `formRevision` has no reader (FB-5).
- **Ordering.**
  - Two history loads for one quote land in completion order, and an older one removes the
    newest revision (CX-31).
  - Undo on an archive reloads the list it was made from, replacing the list now shown
    (CX-16).
- **Words.**
  - Mark final's dialog says revision N is pinned, where the server pins N+1 (FB-8).
  - A pricing refused because the page is for another release shows as a pricing failure,
    and the Reload notice comes only after a save (FB-11).
  - A node failing on the typed quote answers "Internal Server Error", where the container
    answers the reason (FB-52).
  - `haute app build`, `serve` and `migrate` print tracebacks for faults their handlers do
    not catch (FB-53).
  - A malformed release raises a raw `TypeError`, `ValidationError` or `KeyError` (CX-29).
- **The release.**
  - Its id moves with a node's canvas position, so every open page must reload, and it
    does not move with the pinned runtime (FB-56).
  - Pricing releases its admission before the response is rendered, then converts the
    response on the event loop (CX-26).
- **Tests and decisions.**
  - The test "a release without a quote store prices without its packages" never prices
    (TQ-4).
  - The app trusts the Easy Auth headers because the release names its host (FB-9).
  - The browser alone ticks `is_renewal` (FB-10).

**Plan:**
- **Derived quotes.**
  - A derived quote opens with its baseline at the values opened and without the app-set
    details (`withAppDetails(values, {})`).
  - The carry route strips the app-set details unless it carries the same quote onto newer
    sheets.
- **Drafts.** A draft is reconciled when the sheet returns to clean, offered only when it
  differs from the sheet, and its restore names what had no place.
- **Ordering.** History loads and list reloads go through a ticket scoped to the quote and
  the list shown.
- **Words.**
  - The dialog says what the server does, or the final revision is read from
    `from_revision`.
  - The price route's 409 shows the Reload notice.
  - A pipeline failure is answered with its message.
  - The CLI handlers catch `HauteError` and SQLAlchemy's errors as `click.ClickException`.
  - `read_release` checks its files where it reads them.
- **The release.**
  - The release id hashes the graph without positions, as `graph_fingerprint` does, plus
    the pinned dependencies.
  - Admission is kept through rendering, and the conversion runs in the worker.
- **Tests and decisions.**
  - The storeless test sends a priced request.
  - FB-9 and FB-10 are recorded as decisions with the owner before a slice.

**Acceptance:**
- A duplicate opens clean, holds no reference until it is saved, and leaves no draft.
- A draft typed back to its saved values is gone after a reload.
- History after a save shows that save.
- Undo on an archive refreshes the list shown.
- A node failing on a quote shows its reason in the app.
- Moving a node and building again gives the same release id.

Each has a test that fails today.

**Dependencies:** None.

**Evidence:** `frontend/src/app/useOpenQuoteStore.ts::openNew` (the baseline left at the
empty quote); `frontend/src/app/useOpenQuoteStore.ts::offerDraft`;
`frontend/src/app/useOpenQuoteStore.ts::loadHistory`; `frontend/src/app/QuotesPage.tsx`
(the archive's Undo); `frontend/src/app/quoteValues.ts::onSheets` (cells dropped unsaid);
`src/haute/_workbench_app_quotes.py::quote_router` (the carry route);
`src/haute/_workbench_app.py::_release_id`; `src/haute/_workbench_app.py::read_release`;
`src/haute/cli/_app.py::handle_app_migrate`;
`tests/test_workbench_app.py::test_a_release_without_a_quote_store_prices_without_its_packages`.

### WB-21 — Building the sheets
**Why:**
- **The 8-pixel creep.** A move is snapped twice: on the sheet, then against its section's
  top. Whenever a section above has a height off the 8-pixel grid (a typed Height, or Fit
  after a pixel nudge), a sideways drag moves a component 8 pixels down (review FB-29).
- **Gestures.**
  - Gestures follow client coordinates alone, so a wheel scroll during a drag leaves the
    component behind the pointer.
  - A release outside the window leaves the gesture following the pointer, and
    `startCreate` drops its stop callback (CX-17).
  - A side panel unmounted mid-resize leaves the body's cursor and text selection set
    (CX-18).
- **Panels, focus and keys.**
  - The component's properties panel is not keyed by the component, so a rejected value
    shows on the next one (CX-20).
  - A failure listing the params folder leaves an empty, disabled selector with no reason
    (CX-21).
  - A press on the viewport's scrollbar deselects the component and closes its panel
    (FB-32).
  - Moving a tab before any sheet was chosen clears the selection (FB-33).
  - Alt+Down on a schema column loses the focus (FB-34).
  - Undo, redo and fit are ignored while a checkbox has the focus (FB-35).
- **Accessibility and wording.**
  - While building, the section bars come after every component in reading order.
  - Required cells carry no `aria-required`, and grid headers no `scope`.
  - The tabs announce as tabs but have no arrow keys.
  - A clicked component is selected but not focused (FB-36).
  - Tooltips carry instructions, which the owner's rule puts in `docs/` (FB-37).
- **Performance.** Each drag step clones and serialises the whole form and redraws every
  component, and nothing on the sheet is memoised (FB-38).
- **For a decision.**
  - There is no keyboard way to add or resize a component (FB-40).
  - A Table of output columns alone shows dashes with no reason (FB-41).
- **Small things (FB-42):** pane state kept for deleted components, a floating-point zoom,
  `aria-pressed` on the bars, and the Display box without `aria-modal`.

**Plan:**
- **One snap.** A move is snapped once, in its section's coordinates, for drags and drops
  alike.
- **Gestures.**
  - Gestures work in sheet coordinates taken from the live sheet, so they follow the
    scroll.
  - A gesture ends on blur, on lost capture, and on a move with no button pressed.
  - The side panel uses one stop routine for every way a resize ends.
- **Panels, focus and keys.**
  - The component's panel gets a `key`.
  - The params listing's other failures are shown, with a retry.
  - Presses outside the viewport's client box are ignored.
  - A tab move pins only the page.
  - One `useRefocusAfterMove` hook serves the four lists that move by Alt+arrows.
  - Whether a target is a text field is decided by the input's type.
- **Accessibility and wording.**
  - The section bars are drawn first in their sections.
  - Required cells get `aria-required`, and grid headers `scope="col"`.
  - The tabs move by the arrow keys.
  - The selected frame takes the focus.
  - Tooltips name things rather than instruct.
- **Performance.**
  - `dirty` is computed when a gesture ends.
  - The value hooks compute only the live set.
  - `SheetWidget` is memoised with its fields.
  - `edit` shares what a layout change leaves alone.
- **Decisions.** FB-40 and FB-41 are decided with the owner.

**Acceptance:**
- With a section of height 300 above, a drag lands where the pointer puts it.
- A drag ended outside the window stops.
- The sheet reads section by section with Tab and with a screen reader.
- A drag on a form with fifty components and a fifty-row table redraws only what moved.

Each has a test.

**Dependencies:** `FSH-R04` for the shared refocus hook and form controls.

**Evidence:** `frontend/src/workbench/sheetInteractions.ts::startTransform` (a snap on the
sheet, then in the section); `frontend/src/utils/sheetGeometry.ts::moveRect`;
`frontend/src/haute-ui/SidePanel.tsx::SidePanel` (styles restored on mouse-up alone);
`frontend/src/workbench/PropertiesPanel.tsx` (the component panel without a key);
`frontend/src/workbench/SchemaEditor.tsx` (the params listing's catch);
`frontend/src/workbench/SheetCanvas.tsx` (a press on the scrollbar deselects);
`frontend/src/workbench/PageTabs.tsx`; `frontend/src/hooks/keyboardTargets.ts::isTextField`;
`frontend/src/workbench/SectionHeaders.tsx`; `frontend/src/workbench/SheetWidget.tsx`;
`frontend/src/utils/workbenchForm.ts::edit`;
`frontend/src/stores/useWorkbenchFormStore.ts::setFormRaw`.

### WB-22 — One mechanism per concern
**Why:** The [review](workbench-review-2026-10-15.md) found mechanisms written several
times, each copy slightly different; the defects in `WB-17` to `WB-21` sit where one copy
was forgotten or scoped to the wrong thing:
- **Stale answers and turn-taking**, hand-rolled about ten times:
  - the form store's turns;
  - the pricing store's run and pricer identity;
  - the preview store's `asked`;
  - the test quotes' `generation`;
  - the open quote's `opening`;
  - the list store's `asked`;
  - `refreshTables`' fetch numbers;
  - `useWorkbenchTables`' generations;
  - the Files panel's request string;
  - App's `projectSaveTurns`;
  - `AllowedField`'s ticket.
- **Three writers into the project** (the form save, the test-quote writes, project files),
  with three revision ideas, two locking shapes, two path rules and one capture retry.
- **The form's save mechanics restate the pipeline's** stale detection, the uncaptured
  rule, two turn queues, and the panel's floors beside the operations' (FB-25).
- **Smaller copies:**
  - the app-set details applied twice on the server;
  - the form installed into its store by two `setState` calls with different fields;
  - `version` repeating `latest`;
  - `_read_quotes` and `_histories` each copying and migrating a file.
- **The three largest view files each do several jobs:** `WidgetBody.tsx` (five
  components and a colour resolver), `PropertiesPanel.tsx` (six panels and a dialog) and
  `SchemaEditor.tsx` (the table block, the column row, the details, the allowed-values
  reader and the default field).

**Plan:**
- **Two primitives in `frontend/src/utils`:** `serial()`, a turn queue, and
  `latest(scope)`, a ticket that stays current while its scope does (the document
  generation, the quote and its version, the folder, the list query). They are tested once
  and adopted by every store above, after `WB-17` to `WB-21` have fixed each copy's
  behaviour.
- **One project writer.**
  - It takes a project-relative path, the revision the caller saw, the bytes or a staged
    file, and a ledger message.
  - It holds both locks, applies `ordinary_path_problem`, checks the branch and the
    revision, writes with one rename, captures, and answers
    `{revision, git_sha, identity_required, warnings}`.
  - The form, the test-quote file and project files are its callers, with one capture
    retry over paths (with `BUG-34`).
- **Shared helpers.**
  - The save helpers are shared with the pipeline's save: `isStaleRevision` and the
    uncaptured rule.
  - One `installForm`.
  - The details helpers live in one place on each side.
  - One helper opens a migrated copy of a test-quote file.
- **Split view files.** `workbench/sheet/`, `workbench/panel/` and `workbench/schema/`, as
  the review's target shape lays them out, with behaviour unchanged.

**Acceptance:**
- No store keeps a ticket or a queue of its own.
- The three writers share one module and one test of its refusals.
- The three view files are split with their tests passing unchanged.

Behaviour is unchanged: the affected tests pass before and after.

**Dependencies:** `WB-17` to `WB-21` first, so their fixes land before the copies are
merged; `FSH-R04`; `BUG-34` for the capture retry.

**Evidence:** `frontend/src/stores/useWorkbenchFormStore.ts::takeTurn`;
`frontend/src/stores/useWorkbenchPricingStore.ts`;
`frontend/src/stores/useWorkbenchPreviewStore.ts`; `frontend/src/app/useQuoteListStore.ts`;
`frontend/src/panels/FilesPanel.tsx`; `src/haute/routes/workbench.py::_write_test_quotes`;
`src/haute/routes/project_files.py::_publish_write`;
`src/haute/_quote_store/_book.py::QuoteBook`; `frontend/src/app/openRelease.ts`;
`src/haute/cli/_test_quotes.py::_histories`; `frontend/src/workbench/WidgetBody.tsx`.
