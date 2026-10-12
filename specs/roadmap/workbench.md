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
the app saves quotes to; exports from the app; the test quotes analysts keep and re-price in
Preview; and the actions that fill the sheets from databases and APIs.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| WB-07 | Planned | P2 | An underwriter signs in to the project's hosted app, sees the deployed form's sheets and nothing of the builder, keys a quote in and is given the price of the pipeline packed into the same app. |
| WB-08 | Planned | P2 | A merge to `main` takes a workbench project's form, pipeline and app, tested together in CI, to staging and, after approval, to production on the project's host, Azure Container Apps first. |
| WB-09 | Decision | P2 | A Button on a sheet, or a lookup beside a key cell, runs an action the project declares against a database or an API and fills input cells with what comes back, through the server with no credential in the browser, while pricing stays as pure as it is. |
| WB-10 | Planned | P2 | An underwriter's quotes are saved to and reopened from the SQL instance the project's IT team provides, with the form and the release that priced each one, through the same store an analyst's machine runs on SQLite. |
| WB-11 | Decision | P2 | An underwriter exports a saved quote from the app in the formats the project needs, naming the release that priced it. |
| WB-12 | Planned | P3 | A test quote whose response runs past what Preview shows is saved, run and compared in full, as every other test quote is. |

## Planned improvements

### WB-07 — The underwriter app
**Why:** The sheets reach an underwriter only inside Haute's own server. Preview prices a
quote by patching the pipeline open in the editor with the form's tables and the typed quote
and previewing each Workbench Output port through the dev server's worker pool, so it needs
`haute serve`, the pipeline's source and the editor's graph, and it sits inside the
builder's chrome; the sheet canvas itself reads the builder's form and view stores and
carries the builder's selection and layout editing, so hiding the chrome would not make it
an underwriter's view. Underwriters have no Haute, and the builder (the nodes, the schema
editor, the sample) is not theirs to see. Pricing analysts build the rates and the sheets on
their own machines with `haute serve`; what underwriters use is an app that a merge to
`main` puts in front of them, and where it runs varies by project: Azure, AWS, a Databricks
App or the company's own platform. What a host has today: the container target generates a
FastAPI scoring app and a Dockerfile and builds and pushes the image, and no target rolls a
running service to it; the hosted Databricks factory puts the builder's server behind the
workspace proxy, which is not an underwriter's app; and a deploy carries the Workbench
Input's tables, not the form's pages, so the sheets an underwriter would see are not
versioned with the pipeline they price on.

**Plan:** The hosting decisions are recorded: one app for every host, Azure Container Apps
first; pricing inside the app; the host's infrastructure and sign-in provided by the
project's IT team. The app is a FastAPI app factory Haute maintains, holding the sheet
renderer without the builder's chrome, the deployed form's schema and pages, the pipeline's
scorer, the quote store of WB-10 and the exports of WB-11, with a small entry point
generated per project; the sample stays in the builder. Pricing runs in the app, one quote
per request, through the engine a deployed pipeline uses, and answers with the whole
response, never cut to a display limit as Preview's is, off the server's event loop with an
admission limit and a timeout, since one quote can hold a long schedule. A workbench project
deploys no separate scoring endpoint unless something else calls one and its configuration
says so. Form, sheets and pipeline are one artifact, so form v12 prices on pipeline v12 by
construction, and a browser holding an older form is refused rather than priced on a newer
contract. Packaging: a container image for Azure Container Apps, then AWS and internal
platforms; a source bundle for a Databricks App, which does not run images. Sign-in: the
host's own authentication fronts the app (Azure's built-in authentication first); the app
reads the principal the host asserts, verifying it where the host signs it, and is reachable
only through that front; sign-in built into the app is not part of the first increment. The
app's host is a setting beside the workbench's (for example `[workbench.app]`), apart from
`[deploy] target`, which keeps meaning a separately deployed scoring API. The slices: the
renderer drawn out of the builder's stores, with Preview kept on it; the app factory with
pricing inside and the release it reports; `haute app serve`, which runs the same app on an
analyst's machine against a local SQLite store, so the sheets underwriters will see are
checked before a merge; then the image for Azure Container Apps.

**Acceptance:** An underwriter with no Haute installed, signed in through the host's
authentication, opens the app on Azure Container Apps, sees the deployed form's sheets and
nothing of the builder, keys a quote in and is given in full the tables the pipeline gives
for the same quote on the same pipeline version, which the builder's Preview shows up to its
display limits, with no request leaving the app to price it; a value that breaks its
column's rule is marked before pricing, as Preview marks it; a request without the host's
sign-in, or with an identity the host did not assert, is refused; no database or deploy
credential reaches the browser; `haute app serve` runs the same app on an analyst's machine
and prices the same quote the same way.

**Dependencies:** WB-10 for saving and reopening quotes and WB-11 for exports, neither
needed by the first slices. The host's infrastructure and authentication, provided by the
project's IT team. On Databricks,
[hosted-databricks-app](../hosted-databricks-app/high-level.md) for the proxy's trust
boundary; elsewhere, the container target ([deploy](../deploy/high-level.md)).

**Evidence:** `frontend/src/workbench/priceSample.ts::priceSample` (Preview's pricing
through the preview route); `frontend/src/workbench/SheetCanvas.tsx`,
`frontend/src/workbench/WidgetBody.tsx`, `frontend/src/workbench/PageTabs.tsx` (the
renderer, wired to the view's stores); `frontend/src/utils/sheetValues.ts::quoteFilled` and
`::pricedRows`; `src/haute/deploy/_container.py::prepare_build_directory`,
`::_generate_app_source` and `::deploy_to_platform_container` (the scoring app, built and
pushed, no service rolled); `src/haute/deploy/_scorer.py::score_graph` (the engine a
deployed pipeline prices with); `src/haute/hosted.py::create_app` and
`::PlatformProxyBoundary` (the builder behind a workspace proxy);
`src/haute/deploy/_utils.py::build_manifest` (what a deploy carries).

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
the app of WB-07, which underwriters use by hand, and nothing in the workflows builds, tests
or deploys it. Pricing analysts work on their own machines: Save writes the save branch,
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

**Dependencies:** WB-07 (the app), WB-10 (the store and its migrations), WB-11 (the export
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
exists: one path for a sheet's values, the sample while building and the quote in Preview,
through which a result can land exactly as typing does, so the rule marks follow it and
the price dims until Price is pressed again; a Databricks SQL reach on the server, which
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
column property; and the underwriter app of WB-07 running the same actions on its server.

**Acceptance:** With a source declared in `haute.toml` and an action in the form, pressing
its Button in Preview fills the bound input cells with the query's values, typed by their
columns and marked where a value breaks a rule, the price dims until Price is pressed,
and the pipeline is not called; the same action runs against a database that is not
Databricks, and an HTTP action fills cells from a response; no credential value appears
in the form file, in `haute.toml`, in an API response or in a log; a statement that is
not a single SELECT, a host not allowed, a result over the cap and a bound column no
longer in the schema are each refused naming the cause; a project with no actions behaves
exactly as now.

**Dependencies:** The decisions above, recorded here before the first slice. For
production, WB-07 (the app must run actions on its server) and, on Databricks,
[hosted-databricks-app](../hosted-databricks-app/high-level.md), whose secret handling
the sources follow. The Host, Origin and session checks of
[sandbox-security](../sandbox-security/high-level.md) apply to the new route as to any
other.

**Evidence:** `frontend/src/workbench/useSheetValues.ts::useSampleValues` and
`::usePreviewValues` (the one path for a sheet's values);
`frontend/src/stores/useWorkbenchPreviewStore.ts::priceQuote` (price on request);
`frontend/src/workbench/widgetKinds.ts` and
`frontend/src/utils/workbenchForm.ts::createWidget` and `::widgetProblems` (the two kinds
of component); `src/haute/_workbench_form.py::FormSpec` (the strict reader);
`src/haute/routes/workbench.py` (the workbench router);
`src/haute/_databricks_io.py::_validate_select_clause`, `::_connection_settings` and
`::_service_principal_credentials` (the Databricks SQL reach); `pyproject.toml` (the
Databricks extra); `docs/deployment/targets/databricks.md` (credentials as CI secrets
and the editor's data-access pair).

### WB-10 — The quote store
**Why:** Underwriters save the quotes they price and come back to them, and the business
keeps what was quoted and on which rates. The ruling is one store with two databases: on the
underwriter app, a SQL instance the project's IT team provides; on an analyst's machine and
in CI, a SQLite file, which the test quotes of WB-12 use. The store runs on SQLite
([workbench](../workbench/high-level.md)): quotes with numbered revisions written whole,
each keeping the form it was keyed into, a revision that names one its quote has moved past
refused, a saved quote's values carried onto the current form by the stable ids, and Haute's
numbered migrations, which the store refuses to open without. What the underwriter app needs
of it does not exist: a backend for the server engine IT's instance runs, the connection
configured per environment, the migrations run by a deploy under an identity of their own,
and the app's Save, Open and list of quotes.

**Plan:** Which engine, Azure SQL or Azure Database for PostgreSQL, is the IT team's to name
and is recorded here before the server backend's slice. The backend is that engine's driver
behind an optional extra, as the Databricks clients are, with the store's tests run against
SQLite and that engine alike (a service container in CI), and the form copies kept once
through that engine's own insert-if-absent. Who sees whose quotes, each underwriter their
own or a team's, is recorded before the app's list of quotes. The connection is configured
per environment by name, its value supplied by the host at runtime (a managed identity or a
secret) and never written to a file the ledger pushes. The deploy's migrate step applies the
migrations under an identity that owns the schema, while the app's own identity reads and
writes data only. A release's migrations keep the previous release's reads and writes
working (a column added as nullable or with a default, no constraint the previous release's
writes would break, nothing dropped), proved by running the previous release's store tests
against the schema the migrations leave, so the previous release can be started again after
a failed rollout; removing a column is a step of its own after the release that stops using
it. Then the app's Save and Open and the list of quotes on the sheets: a quote saved under
an older form opens on that form, read-only, and re-pricing it on the current form makes a
new revision, naming what no longer fits.

**Acceptance:** An underwriter saves a priced quote in the app, closes it, finds it in their
list and reopens it with its values, its prices and the release that priced them; a second
save makes a revision and leaves the first as it was; after a release removes one field and
renames another, a quote saved before it opens read-only with its original labels and
values, and re-pricing it names the column the current form no longer has; the same store
tests pass against SQLite and the server engine; the previous release's store tests pass
against the schema a candidate's migrations leave; no connection value appears in the form
file, `haute.toml`, an API response or a log; the app's identity cannot change the schema.

**Dependencies:** WB-07 (the app that saves). The SQL instance and its identities, provided
by the project's IT team, and the choice of engine above.

**Evidence:** `src/haute/_quote_store/_store.py::open_store`, `::migrate_store`,
`::QuoteStore` and `::carry_values` (the store on SQLite);
`src/haute/_quote_store/_tables.py::METADATA` (Haute's schema);
`src/haute/_quote_store/_store.py::_keep_form` (a form kept once, by SQLite's
insert-if-absent and otherwise a read first);
`frontend/src/stores/useWorkbenchPreviewStore.ts::priceQuote` (Preview's quote, held in the
browser alone); `src/haute/_workbench_config.py::read_workbench_config` (the workbench's
settings, with no store in them).

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

**Dependencies:** The decision above. WB-10 (the saved revision an export reads) and WB-07
(the app it runs in).

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
