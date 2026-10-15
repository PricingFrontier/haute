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

`WB-22` to `WB-26` take on what the [review of 15 October 2026](workbench-review-2026-10-15.md)
found in what pull requests #303 to #309 built. Each names the review's findings it closes
(`CX-n`, `FB-n`, `TQ-n`, `SD-n`, `MS-n`), and the review holds their evidence. `WB-22`
comes first, merging the mechanisms written several times now that the others' behaviour
is fixed; `WB-23` prices Preview in one run of the pipeline; and `WB-24`, `WB-25` and
`WB-26` wait on decisions about the form's file, the app's sign-in and renewals, and
building the sheets without a pointer.

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
| WB-22 | Planned | P2 | Each concern has one mechanism: one way to drop a stale answer and take turns, one writer into the project, and the three largest view files split along their jobs. |
| WB-23 | Planned | P3 | Preview prices every table of the Workbench Output from one run of the pipeline, as the app prices a quote in one scoring call. |
| WB-24 | Decision | P3 | The editor learns of a change to the form's file on its own, and opens a merged form whose geometry is off so it can be repaired. |
| WB-25 | Decision | P3 | The underwriter app refuses to trust sign-in headers it was not put behind, and the server records a renewal's `is_renewal` itself. |
| WB-26 | Decision | P3 | A component is added to a sheet and resized from the keyboard, and a Table of output columns alone shows its rows, or says why it has none. |

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

### WB-22 — One mechanism per concern
**Why:** The [review](workbench-review-2026-10-15.md) found mechanisms written several
times, each copy slightly different; the review's defects sat where one copy was
forgotten or scoped to the wrong thing:
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
  with three revision ideas, two locking shapes and two path rules.
- **The form's save mechanics restate the pipeline's** stale detection, two turn queues,
  and the panel's floors beside the operations' (FB-25).
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
  and adopted by every store above, keeping each copy's behaviour as it now is.
- **One project writer.**
  - It takes a project-relative path, the revision the caller saw, the bytes or a staged
    file, and a ledger message.
  - It holds both locks, applies `ordinary_path_problem`, checks the branch and the
    revision, writes with one rename, captures, and answers
    `{revision, git_sha, identity_required, warnings}`.
  - The form, the test-quote file and project files are its callers, each answering the
    shared capture fields that the one capture route retries.
- **Shared helpers.**
  - The save helpers are shared with the pipeline's save: `isStaleRevision`.
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

**Dependencies:** None.

**Evidence:** `frontend/src/stores/useWorkbenchFormStore.ts::takeTurn`;
`frontend/src/stores/useWorkbenchPricingStore.ts`;
`frontend/src/stores/useWorkbenchPreviewStore.ts`; `frontend/src/app/useQuoteListStore.ts`;
`frontend/src/panels/FilesPanel.tsx`; `src/haute/routes/workbench.py::_write_test_quotes`;
`src/haute/routes/project_files.py::_publish_write`;
`src/haute/_quote_store/_book.py::QuoteBook`; `frontend/src/app/openRelease.ts`;
`src/haute/cli/_test_quotes.py::_histories`; `frontend/src/workbench/WidgetBody.tsx`.

### WB-23 — One preview for a Workbench Output's tables
**Why:** Preview prices the sample by previewing the Workbench Output once per output table
(review CX-13), and each preview runs the pipeline again: the preview route keys a preview
by its port, and the executor, which computes every table of the Workbench Output in one
run, answers one port's preview. Measured on 15 October 2026 on the Workbench Output of
`tests/test_workbench_output.py` (two tables), each table's preview cost a full run, about
30 ms warm and 120 ms cold, the second no cheaper than the first. A pipeline that takes
seconds is priced in that time for every table, on every pause in typing, where the app
prices a quote in one scoring call.

**Plan:**
- The preview route takes several ports of a multi-frame target and answers each one's
  preview from one run; the executor builds each port's preview from the frame bundle it
  already computes.
- `priceSample` asks for every table of the Workbench Output in one request.

**Acceptance:**
- Preview prices a form with three output tables in one run of the pipeline, its answers
  equal to those of a preview per table.

Each has a test.

**Dependencies:** None.

**Evidence:** `frontend/src/workbench/priceSample.ts::priceSample` (a preview per table);
`src/haute/routes/pipeline.py::_preview_supersession_key` (a preview keyed by its port);
`src/haute/executor.py::execute_graph` (one port's preview from the bundle).

### WB-24 — The form's file changed or merged outside the editor
**Why:** Two gaps the [review](workbench-review-2026-10-15.md) found, each a choice for the
owner before a slice:
- The view reads the form again when the pipeline's document is adopted anew, so a change
  to `forms/form.json` alone, by hand or by a pull, shows only as a save refused as stale
  (FB-26).
- A merged or hand-edited file whose geometry is off (a component past its sheet's width
  or below its section) is refused when it is read, so it cannot be opened in the editor
  that would repair it (FB-27).

**Plan:** Decisions first, recorded here before a slice:
- FB-26: whether the file watcher announces a change to the form's file, as it does for
  the pipeline's, so the view reads it again (and marks it stale over unsaved edits).
- FB-27: whether a form whose geometry is off reads with each component kept inside its
  section and sheet, said in a notice and saved as moved, or stays refused with the place
  to fix by hand.

**Acceptance:** Set by each decision.

**Dependencies:** None.

**Evidence:** `frontend/src/stores/useWorkbenchFormStore.ts::sync` (run on an adoption
only); `src/haute/_workbench_form.py::Section` and `src/haute/_workbench_form.py::Page`
(geometry refused at read).

### WB-25 — The app's sign-in and its renewals
**Why:** Two choices the [review](workbench-review-2026-10-15.md) found for the owner before
a slice:
- The app trusts the Easy Auth headers because `release.json` names the host, so a
  container reached without Easy Auth in front signs in whoever names themselves, with
  edit rights when no edit role is set (FB-9).
- The browser alone ticks `is_renewal` on a renewal; the server records the relation but
  not the column (FB-10).

**Plan:** Decisions first, recorded here before a slice:
- FB-9: a startup check that the host's sign-in is in front, or an explicit trust setting
  the operator sets, refusing the misconfiguration either way.
- FB-10: whether the server ticks `is_renewal` on a quote created as a renewal, the
  browser's tick then shown rather than sent.

**Acceptance:** Set by each decision.

**Dependencies:** None.

**Evidence:** `src/haute/_workbench_app.py::create_app` (the principal read from the
headers when the release names its host); `frontend/src/app/useOpenQuoteStore.ts::openNew`
(`asRenewal`, the browser's tick).

### WB-26 — Building the sheets without a pointer, and Tables of outputs alone
**Why:** Two gaps the [review](workbench-review-2026-10-15.md) found, each a choice for the
owner before a slice:
- A component reaches the sheet only by dragging it out of the palette, and is resized
  only by its handles: the arrow keys move a selected component but nothing adds or
  resizes one (FB-40).
- A Table whose fields are all output columns draws a dash in each cell with no reason,
  where a Chart of the same columns plots the first output table's answer (FB-41).

**Plan:** Decisions first, recorded here before a slice:
- FB-40: whether Enter on a palette item adds it to the selected section, or the first,
  at the first place free, selected; and which keys resize the selected component on
  the grid, as the arrow keys move it.
- FB-41: whether a Table of outputs alone takes its rows from the first output table's
  answer, the others matched by key, as a Chart does, or names in its place why it has
  no rows.

**Acceptance:** Set by each decision.

**Dependencies:** None.

**Evidence:** `frontend/src/workbench/WorkbenchPalette.tsx` (items dragged, no key adds
one); `frontend/src/workbench/useWorkbenchShortcuts.ts` (the arrow keys nudge, none
resize); `frontend/src/workbench/WidgetBody.tsx` (a value not held drawn as a dash);
`frontend/src/utils/sheetChart.ts::sheetChartData` (a Chart's rows from the first output
table's answer).
