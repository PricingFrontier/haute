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

`WB-38` comes from the [25 October 2026 codebase
review](codebase-review-2026-10-25.md): what the review found in the Workbench view's
Allowed field.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| WB-07 | Planned | P3 | The underwriter app runs on a Databricks App, on AWS or on an internal platform as it runs on Azure Container Apps, each signing underwriters in its own way. |
| WB-08 | Planned | P2 | A merge to `main` takes a workbench project's form, pipeline and app, tested together in CI, to staging and, after approval, to production on the project's host, Azure Container Apps first. |
| WB-09 | Decision | P2 | An Import on a sheet, or a lookup beside a key cell, runs against a file, a database or an API and fills input cells with what comes back, through the server with no credential in the browser, while pricing stays as pure as it is. |
| WB-11 | Decision | P2 | An underwriter exports a final quote from the app in the formats the project needs, naming the release that priced it. |
| WB-12 | Decision | P3 | Analysts keep test quotes and price them again as the pipeline changes, built again after the first version was taken out. |
| WB-13 | Planned | P3 | An underwriter records how a final quote ended, and archives or reassigns many quotes at once. |
| WB-25 | Decision | P3 | The underwriter app refuses to trust sign-in headers it was not put behind. |
| WB-38 | Decision | P3 | Allowed values typed into an older form are either refused by the form schema or canonical; the Allowed field stops calling a shape the server accepts "older". |
| WB-44 | Planned | P2 | The people who own a hosted quote store's tables change them in SQL, starting from SQL Haute writes from the form's change; the app starts whatever its tables hold but opens and prices no quote, new or saved, naming each difference, until they fit its release's form. |

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
quotes, which also holds the form the Workbench nodes take their tables from to the pipeline), Deploy →
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
the workbench adds, such as the test quotes of WB-12, needs tracking from the start.

**Plan:** The fork is at `haute init --workbench`, which generates a workbench project's
workflows; the behaviour lives in Haute's commands, which read the workbench settings, so
the YAML stays thin and alike across providers, and a refresh command regenerates a
project's workflows without rewriting its pipeline or form, as `haute init --force` would.
The deploy triggers gain each release input as it arrives, where its specification puts it:
the app's entry point and the export templates; the store's tables follow the form, which
already starts the deploy. The app's delivery builds each release once. On every
pull request, Validate and the packaged app tested in the CI runner before any host sees it,
against SQLite and once against the server engine IT's instance runs (a service container),
each a throwaway database made from the SQL WB-44 writes for `main`'s form and then for the
branch's change, since SQLite proves neither that engine's types nor that the SQL runs there:
the test quotes of WB-12 re-priced one request each against their accepted outputs, a quote
saved, reopened and, once WB-11's formats exist, exported through the app, and the sheets
driven in a browser. On the
merge to `main` alone, never on a push of the save branch: the merge commit built once, the
packaged app's tests run again on that build, the image published to the registry and its
digest recorded; the staging app rolled to that digest, never to a tag, with no step changing
a database's tables, which the people who own them change in SQL (WB-44); a hosted check
that the expected release runs, that a request without sign-in is refused and that its quotes
are open, the run failing naming what its tables lack when they do not fit its form; impact
re-pricing a bounded sample of saved quotes from the store, through a read-only identity, on
the candidate and the
production app, comparing within each quote's response tables (a premium inside a table, a
schedule's rows matched by key) and naming a quote whose shape no longer compares; for a
first release, with no production app or no saved quotes, impact reports that there is no
baseline and the candidate answers for the committed test quotes alone, while a production
app that fails fails the check; approval; then production rolled to the same digest,
checked the same way, and the release that runs recorded. Rollouts take a maintenance
window: the old revision stops, the new revision starts, and no traffic is split; a new
revision that fails to start or fails its check is replaced by the previous release and the
run fails, except that one whose quotes are closed because its environment's tables do not
fit it yet stays, the run failing naming what they lack, and opens its quotes once they are
made to fit. The host step is small, and the project's IT team provides what it deploys
onto (the Container App, the registry, the identities and the database): it publishes, rolls the environment to
the release, waits until the release runs and reports it, and creates nothing. Azure Container
Apps first; other hosts follow with the same commands.

**Acceptance:** A project initialised by `haute init --workbench` for Azure Container Apps
and merged to `main` runs its generated workflow to a staging app and, after approval, to
production, with the digest CI tested deployed to both, a re-run of the workflow included, a
maintenance window around each rollout and the release that runs recorded; a change confined
to an export template starts the workflow; the packaged app passes the same tests against
SQLite and the server engine before any host sees it, each test database made from the SQL
WB-44 writes; a push of the save branch releases nothing; no step of the workflow changes a
hosted database's tables; a request to the hosted app without sign-in is refused; impact
reports a change to a premium inside a response table; the first release, with no
production app, reports that there is no baseline and still waits for approval; a release
rolled out before its tables fit stays with its quotes closed, the run failed naming what
they lack, and opens its quotes once the tables fit, with no new run; a candidate that fails
otherwise leaves the previous release running and the run failed.

**Dependencies:** WB-11 (the export the app's tests make, once its formats are
decided), WB-12 (the test quotes) and WB-44 (the app open whatever its tables hold, and the
SQL its test databases are made from). The Azure
resources and identities, provided by the project's IT team. The generated deploy jobs
already log in to the registry before a push; impact's input is the `[safety]` portfolio
sample, or the test quotes without one.

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
for and the names of its credential variables in `haute.toml`; the values in the
environment, never in a file the ledger pushes. Whether a press in Build writes the
sample, which Save then writes to disk, or actions run in Preview alone. Then the
slices: the sources table of `haute.toml`, which an Import's connection names; a route
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
connection and the tables each is written to; it is drawn disabled in Preview and the
app, since nothing runs it.

**Plan:** Decision first, recorded here before the first slice: which formats the first
release needs (the quote's tables as CSV or a spreadsheet, an import file for another
system, a document from a template), whether the Export on the sheets is how the app
exports or the quote's bar offers exports beside it, and whether a quote exported again
later reproduces the template it was first exported with or uses the current one. Then exports of a final
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

**Plan:** Decided with the owner before work starts: what a test quote is, where it is
kept, how it is run and reviewed, and how CI uses it. A pricing that answers the Workbench
Output's response whole, apart from the preview route's limits, keeps long quotes in full.

**Acceptance:** Set with the plan.

**Dependencies:** None.

**Evidence:** The commits above; `src/haute/deploy/_validators.py::score_test_quotes` (the
dry run's pricing of deploy's JSON test quotes, one request each).

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
canonical and should not be called "older". From the [25 October 2026 codebase
review](codebase-review-2026-10-25.md) (FWB-7).

**Plan:** Decide. Either `FormSpec` refuses `options` without `options_source` for input
columns, and `TYPED_IN`, its test and the problem text are removed; or both stay and the
specification and the field stop calling the shape older.

**Acceptance:** One of the two, with the workbench specification saying which.

**Dependencies:** None.

**Evidence:** `frontend/src/workbench/schema/AllowedField.tsx::TYPED_IN`,
`src/haute/_workbench_form.py::FormSpec`, `frontend/src/utils/workbenchForm.ts`.

### WB-44 — Quote store tables changed in SQL
**Why:** A hosted quote store's tables are Haute's to change
([workbench](../workbench/high-level.md)). `haute app migrate --environment` brings a
database to a release's form through the planner, comparing it by stable id with the
structure `haute_store` records: it adds and renames, keeps what the form removed and prints
its DROP for the DBA, refuses a type, rows or role change on a column or table that holds
values, and applies a step only a restore undoes only given `--restore-point`; `--plan` and
`--sql` print the plan or its SQL. The app opens the store as it is built and does not start
on a database whose tables, as `haute_store` records them and as its catalogue holds them,
are not exactly its release's form's, nor on one it cannot reach or whose sign-in lacks a
privilege: a container on such a database exits, its host restarts it, and underwriters have
no app until the tables are put right. The owner's ruling (31 October 2026) is that, once the
app is live, the people who own a project's databases change these tables in SQL, as they
change every other table they own, and that nothing in CI changes them. Against that: a
change made in SQL leaves `haute_store`'s record behind, so the app refuses a database the
change brought to the right shape; the planner converts no values, so a column holding
values cannot change type and keep them; a Haute release that changes the store's own
columns is refused by a hosted migrate outright; `haute_store` records the environment, so a
copy of prod's tables is refused as test's; and the analysts, who work in the editor rather
than at the command line, would be left to write each form change's SQL themselves.

**Plan:** The people who own a hosted store's tables make every change to them in SQL;
neither Haute nor CI changes a hosted database's tables, only the rows the app saves.
`haute_store` goes from hosted databases, so Haute records nothing of a database's shape or
environment there, and `haute app migrate` goes with its `--plan`, `--sql`, `--base` and
`--restore-point`, the tables and columns it keeps and its hold on writers; each
environment's own sign-in is what keeps an app off another environment's database. The
form's tables, the store's own columns of `quote_details` and `haute_activity` stay as they
are, since they hold the quotes and their history. `haute app serve`'s SQLite file stays
Haute's, as nobody else changes it: the planner keeps building it from the release's form in
dev's mode as the app starts, its record kept in that file alone.

The app checks its release's form against the database's catalogue, as
`_catalogue_differences` does today without `haute_store`: each table and column the store
reads or writes there, of its kind, taking a null as the store's does, keyed and referring
to its quote as the store keys them, every other column of those tables taking a null, a
text the store compares exactly under its binary collation on SQL Server, and the privileges
the app needs. The app starts whatever the database holds, reachable or not. Until the check
finds nothing, the quotes are closed: every quote route and the pricing route refuse (503,
`quote_store_mismatch`, or `quote_store_unreachable` for a database it cannot reach), naming
each difference or missing privilege; `GET /api/app/release` says the quotes are closed and
why; and the page opens no quote, new or saved, saying the quotes are unavailable, so nothing
is keyed or priced that could not be saved. A quote open as they close stops pricing and
saving, its values kept in the browser as unsaved values are now and offered back once they
open. An app started with no environment named keeps nothing and prices its one quote as it
does now. A refused request checks again at most once every 10 seconds, the requests in
between answered with the last check's refusal, and the page asks again on the same bound,
so tables put right in SQL open the quotes with no restart, no new image and no reload. A
statement the database refuses because a table or column is not as the check found it sends
the running app back to checking: the request is refused as above when the check finds a
difference, and fails as it does now when it finds none.
`/health` answers throughout.

Build mode's schema pane offers the SQL that brings an environment's tables from the form as
`main` holds it, or from none for a database that holds no store yet, to the form being
built, written for each declared environment's engine: a table the form gains made with the
store's own columns, its keys and its reference to its quote; a column it gains added taking
a null; a table or column it renamed, known by stable id, renamed through the engine's own
rename; a type change as the engine's ALTER, with a note that values which do not convert
stop it; a change of a table's rows or role as a note of what the table needs; and a removal
as a DROP left commented out, since the check passes a column that takes a null. It is one
transaction, so a statement that fails leaves the tables as they were. It is the planner's
`offline_plan` and `render_sql` without `haute_store`'s row, their refusals turned into
those notes. Nothing reads the SQL back or records that it ran; the check is the only judge
of the tables. A Haute release that changes the store's own columns gives their SQL in its
upgrade notes, and the check names each column until it is there.

**Acceptance:** On a hosted database whose tables a person made from the SQL Build mode
writes, on SQL Server and on PostgreSQL, the app opens and saves quotes. A column added,
renamed or retyped in SQL to fit a new release's form opens that release's quotes within the
recheck bound, with no restart. The app started on a database missing a table or a column,
holding a column of another kind, unreachable, or whose sign-in lacks a privilege serves the
page with no quote open, new or saved, and refuses every quote route and the pricing route
naming each difference; a quote open as its tables stop fitting stops pricing and saving, its
unsaved values offered back once they fit again. A copy of prod's
tables serves test with nothing changed in it. No command, route or workflow of Haute's
changes a hosted database's tables. The SQL for a rename renames rather than dropping and
adding. `haute app serve` builds its own file as now.

**Dependencies:** None.

**Evidence:** `src/haute/_workbench_app.py::create_app` (the store opened as the app is
built, so a mismatch stops the start); `src/haute/_quote_store/_store.py::open_engine` (the
check), `::_recorded`, `::_refuse_other` and `::_differences` (`haute_store`'s record, the
environment in it, and the form compared with it) and `::_catalogue_differences` (the
catalogue compared with the tables); `src/haute/_quote_store/_providers.py::missing_privileges`
(the privileges); `src/haute/_workbench_app_quotes.py::_call` (`quote_store_unreachable`);
`src/haute/_quote_store/_migrate.py::plan_store`, `::prepare_store`, `::offline_plan` and
`::render_sql` (the planner, its two modes and its SQL);
`src/haute/cli/_app.py::handle_app_migrate` (the hosted migrate) and `::_local_store`
(`haute app serve`'s own file); `src/haute/_quote_store/_schema.py::structure` (the record,
with the store's own columns).
