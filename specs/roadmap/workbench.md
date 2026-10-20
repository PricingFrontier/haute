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

`WB-25` takes on what is left of the [review of 15 October
2026](workbench-review-2026-10-15.md) of what pull requests #303 to #309 built: the app's
sign-in, which waits on the Azure work. It names the review's findings it closes
(`FB-n`), and the review holds their evidence.

`WB-27` to `WB-31` take Show when past whole sheets, sections and components: a field, a
column and a cell row by row with conditions of their own, and conditions on rows passing
several tests, on other fields, on text within text, on dates apart and on the price. They
keep the rule that decides what shows now: everything starts shown and rounds only hide,
so a condition fails once what it tests hides, and there is no "none of", "at most",
"exactly" or "not" over a group, any of which could come to hold as hiding went on.
Decision taken 2026-10-20: the rounds stay. Reading a hidden value as empty and deciding in
dependency order, loops refused, would let a condition on a list read exactly what the
quote holds (WB-28's one edge) and allow those four; it is not planned. Rules named once
and used by many sheets, offered the same day, were not taken either.

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
| WB-25 | Decision | P3 | The underwriter app refuses to trust sign-in headers it was not put behind. |
| WB-27 | Planned | P2 | A Collection's field or a Table's column shows by conditions of its own, the Collection closing up around it and the quote leaving it out while it hides. |
| WB-28 | Planned | P2 | A Table's cell shows row by row by its own row's values, blank and passed over while it hides, its value kept and left out of the quote. |
| WB-29 | Planned | P2 | A condition on a many-row table tests the rows that pass several tests in the same row: any of them, every one, how many, or a total of a column over them. |
| WB-30 | Planned | P3 | A condition tests a field against another field, text within text, and a date against another date give or take a number of days. |
| WB-31 | Planned | P3 | What takes nothing typed shows by conditions on the price, applied after it so the price never decides what is priced. |

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

### WB-25 — The app's sign-in
**Why:** The app trusts the Easy Auth headers because `release.json` names the host, so a
container reached without Easy Auth in front signs in whoever names themselves, with edit
rights when no edit role is set (FB-9, from the
[review](workbench-review-2026-10-15.md)). The owner takes it up with the Azure work in the
next pull request. The review's FB-10 is decided as the app works now: the underwriter
ticks `is_renewal` while quoting (Renew opens the quote with it ticked), and the server
records the relation, not the column.

**Plan:** The decision first, recorded here before a slice: a startup check that the
host's sign-in is in front, or an explicit trust setting the operator sets, refusing the
misconfiguration either way.

**Acceptance:** Set by the decision.

**Dependencies:** None.

**Evidence:** `src/haute/_workbench_app.py::create_app` (the principal read from the
headers when the release names its host); `frontend/src/app/useOpenQuoteStore.ts::openNew`
(`asRenewal`, the browser's tick).

### WB-27 — A field or a column with its own Show when
**Why:** Only sheets, sections and components hide ([workbench](../workbench/high-level.md)),
and a hidden component leaves its space in its section empty, since components are placed
freely. A field that applies only while another is ticked needs a Collection of its own,
placed by hand. In the commercial-pricing project's `br_rating`, `eq_included` governs four
`eq_` fields, `flood_included` two, `dic_included` four, `rental_reimbursement_included` one
and `permission_to_occupy` the `occupancy_permitted_date`, and `cat_limit_combined` decides
between `combined_cat_limit` and `br_cat_limit` with `dic_cat_limit`: a dozen Collections
for one table, each leaving a gap when it hides.

**Plan:** Each field of a Collection and each column of a Table (a row of a transposed
Table) gets a Show when of its own, kept per field beside its look (`column_looks`), with
the conditions, groups and matches a component's takes; a field without one always shows.
A Collection closes up around a field that hides, its other fields flowing on through its
columns, and a Table drops the column. While building, a hidden field is faded with its eye
struck through, as a hidden component is, and can still be edited; in Preview and the app
it is not drawn. The rounds hide fields as they hide components, so hiding still only hides
more. A column is then left out of the quote only when no component that shows shows it
with its field shown, and a condition holds only while its column is so shown: `visibility`
and `sheetVisibility` take a field's conditions into `hidden` and into what a condition
reads. The rule `neverShown` keeps against a sheet's or a section's own components moves
to fields: a field's conditions may test other fields of its own component, which is the
point of the package, but never the field itself or one that waits on it. A field's Show
when is edited in its own group in the component's panel, where its look is set, and the
field carries an eye on the sheet.

**Acceptance:** In a Collection holding `eq_included` and the four `eq_` fields, each `eq_`
field shown while `eq_included` is ticked, the four show and hide with the box and the
Collection closes up, in the sheets, Preview and the app; their values are left out of the
quote while they hide; a Table's column hides whole, and a transposed Table's row; a field
waiting on itself is named as a problem; the server and the view are held to new cases in
the shared visibility fixture.

**Dependencies:** None.

**Evidence:** `src/haute/_workbench_visibility.py::_entered` (every field of a Table or a
Collection counts as shown with its component); `frontend/src/utils/sheetVisibility.ts::neverShown`
(a holder's own components, not its fields); `src/haute/_workbench_form.py::ColumnLook` (a
field's look, and no Show when); `frontend/src/workbench/sheet/FieldBoxes.tsx::FieldBoxes`
(a Collection draws every field it holds).

### WB-28 — Cells that show row by row
**Why:** In the commercial-pricing project's `perils`, each row's `deductible_type` decides
whether its `deductible_pc_tiv` or its `deductible_amount` applies, and a peril whose
`include_coverage` is not ticked needs neither a limit nor a deductible. A column's
conditions cannot follow a row's own values, so every row shows every box.

**Plan:** A Table column's Show when (WB-27) may test "this row's" columns of its own
table. While it holds such a test it decides cell by cell, else for the whole column. A
hidden cell is drawn blank, cannot be edited and is passed over by Tab and by paste; its
value stays in the sample, so the cell shows it again when its row's values change back,
and is left out of the quote: `hidden` holds cells as well as columns, and `sample_quote`
(`_record`) and `quotedSchema` leave a row's hidden cells out. A test of this row fails on
a cell hidden in the row, as a condition fails on a column that hides, so the rounds still
only hide more. One edge comes from keeping the rounds (Scope): a condition on a list (any
row, every row, a count) reads a row's values as typed whatever the sheets hide, so a cell
hidden in its row still counts there while the quote leaves it out. The column select of a
Table column's Show when lists this row's columns first; elsewhere none is offered, and one
written there by hand is named as a problem.

**Acceptance:** In the perils Table, with `deductible_amount` shown in rows whose
`deductible_type` is an amount and `deductible_pc_tiv` in rows whose type is a percentage,
each row shows the one box its type asks for, in the sheets, Preview and the app; a hidden
cell's value is kept, left out of its row in the quote and shown again when the type changes
back; Tab and paste pass hidden cells over; the server and the view are held to new cases in
the shared visibility fixture, and a quote's rows to the typed-quote fixture.

**Dependencies:** WB-27.

**Evidence:** `src/haute/_workbench_tables.py::sample_quote` (columns left out whole, for
every row); `src/haute/_workbench_tables.py::_record` (a row's values by column, none left out
alone); `frontend/src/utils/sheetVisibility.ts::quotedSchema` (the quote's columns, the same
in every row).

### WB-29 — Rows that pass several tests
**Why:** A condition on a many-row table tests one column in any row or in every row, or
counts the table's typed rows. The commercial-pricing project's `perils` holds a row per
peril, preset from `perils.csv`, each with `include_coverage`. "The EQ peril is included"
needs `peril` to equal EQ and `include_coverage` to be ticked in the same row, which two
conditions in any row cannot say, as two different rows can meet them; and "two or more
perils are included" cannot be said at all, since Number of rows in perils is always six,
each preset row holding its peril. A total cannot be tested either: a section for large
schedules shown while the jobsites' `hard_costs_limit` adds up past a figure.

**Plan:** A condition on a many-row table names its table, the tests a row must pass, all of
them in the same row, and which rows: any of them, every one (there being at least one), or
how many, by a count test and a whole number, counting every typed row while it has no
tests. A total takes a number column over the rows that pass, its sum, least, greatest or
average, and tests it as a number column's value is tested; the sum of no rows is 0, and
the others of no rows are empty, failing every comparison. Conditions on lists keep today's
rule (Scope): a row's values are read as typed, whatever the sheets hide, and the condition
holds only while each column it tests (for a count without tests, one of its table's
columns other than the index) is on a Table that shows, so hiding still only hides more.
`RowsCondition` and `RowCount` give way to this one shape; no form outside this branch
holds them yet. In the panel a list condition is a block: "[Any row | Every row | Number
of rows | Total of a column] of perils where", a test per line with its own add, then a
count's test and number or a total's column, kind, test and value. The eye reads it as
"Any row of perils where peril equals EQ and include coverage is ticked" and "Number of rows
of perils where include coverage is ticked is at least 2".

**Acceptance:** In the commercial-pricing form, a section shown while the EQ peril's row
has `include_coverage` ticked, one shown while at least two perils are included, and one
shown while the jobsites' hard costs total above a figure, each in the sheets, Preview and
the app; each fails once a column it tests hides; the server and the view are held to new
cases in the shared visibility fixture, the fixture's list cases written in the new shape.

**Dependencies:** None.

**Evidence:** `src/haute/_workbench_visibility.py::_holds` (one column in any or every row,
and a count of typed rows with no tests); `src/haute/_workbench_tables.py::preset_rows`
(each preset row holding its value, so typed); `src/haute/_workbench_form.py::RowsCondition`
and `RowCount`.

### WB-30 — Other values to test against
**Why:** A condition tests a column against values typed into the condition. It cannot
test one field against another (in the commercial-pricing project, `policy_limit` above
`mga_limit`), text within text (an insured whose name contains "Holdings"), or one date
against another (a project completing more than 365 days after it starts,
`project_completion_date` against `project_start_date`).

**Plan:** A condition's value may be another field of the same type instead of values
typed: a one-row table's column, or, in a test of a list's rows or of this row (WB-28,
WB-29), a column of the same row. A date compared with another date may add or take away a
whole number of days. Text gains contains, does not contain, starts with and ends with,
compared as equals compares text. A test against another field holds only while both
fields are on components that show, so hiding still only hides more, and reads the other
field's value as it reads its own. A date against today is left out: a saved quote would
then show other sheets, and so be priced otherwise, on another day; a date field on the
form stands in for it.

**Acceptance:** A section shown while `policy_limit` is above `mga_limit`, one while the
insured contains "Holdings", and one while the completion date is more than 365 days after
the start date, each failing while either field hides; the server and the view are held to
new cases in the shared visibility fixture.

**Dependencies:** WB-29 for another field in a list's tests, WB-28 for one in this row's;
none for the rest.

**Evidence:** `src/haute/_workbench_visibility.py::_passes` (each test against the
condition's own values); `src/haute/_workbench_form.py::Condition` (`values`, typed text
alone).

### WB-31 — Conditions on the price
**Why:** Conditions test input tables alone; one naming an output column is a problem
("which is in an output table"). In the commercial-pricing project the referral reasons
(`product_premium.referral_reasons`) matter only while `premium.referral_count` is above 0,
and a warning on rate adequacy only while `rate_adequacy` is below 1.

**Plan:** A condition may test an output column, but only in the Show when of what takes
nothing typed: a Heading, a Chart, a Collection or a Table of output columns alone, a field
of an output column (WB-27), or a section or a sheet holding only those. Hiding any of them
never changes the quote, so the price never decides what is priced: the rounds decide what
shows from the inputs, these conditions holding, and these conditions are then applied to
the price the sheets show, the last one while a new one is on its way, and before the first
every output read as empty. The server's `visibility` leaves them out, as they never change
`hidden`. An input component placed later in a section with such a condition, or such a
condition on what takes values, is named as a problem, not refused. An output table with
many rows is tested as a list is (WB-29).

**Acceptance:** A Table of referral reasons shown while `referral_count` is above 0,
hidden before the first price and after a price with none, in the sheets, Preview and the
app; an output condition on a section holding an input Collection named as a problem; the
quote the same whatever the output conditions say, held by cases on both sides.

**Dependencies:** WB-27 for fields of output columns, WB-29 for output tables with many rows.

**Evidence:** `frontend/src/utils/sheetVisibility.ts::conditionFault` (a column of an output
table named as a fault); `src/haute/_workbench_visibility.py::_tested_column` (input tables
alone); `src/haute/_workbench_tables.py::sample_quote` (the hidden columns the quote leaves
out).
