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
exports from the app; test quotes analysts keep and price again; and the actions
that fill the sheets from databases and APIs.

`WB-25` takes on what is left of the [review of 15 October
2026](workbench-review-2026-10-15.md) of what pull requests #303 to #309 built: the app's
sign-in, which waits on the Azure work. It names the review's findings it closes
(`FB-n`), and the review holds their evidence.

`WB-28` and `WB-29` give the Show when of a sheet, a section, a component and a field
more to test: other fields, text within text, dates apart and the price. A Table's column
and a Collection's field have a Show when of their own ([workbench](../workbench/high-level.md)),
the whole column hiding; a Table's cell does not hide row by row. The packages keep the
rule that decides what shows now:
everything starts shown and rounds only hide, so a condition fails once what it tests
hides, and there is no "none of", "at most", "exactly" or "not" over a group, any of which
could come to hold as hiding went on. Decisions taken 2026-10-20: the rounds stay, reading
a hidden value as empty in dependency order, which would allow those four, not planned;
and rules named once and used by many sheets were offered and not taken. Fields and
columns with conditions of their own, not taken that day, were asked for on 2026-10-22;
cells shown row by row through "this row" tests were offered again and not taken.

`WB-33` to `WB-38` come from the [25 October 2026 codebase
review](codebase-review-2026-10-25.md): the server's pricing executor and first saves, and
what the review found in the Workbench view and the underwriter app in the browser.

`WB-40` and `WB-41` change how the form is kept. Two designs made independently on
28 October 2026 (a Claude and a Codex model, the first tried on commercial-pricing's real
form) agreed on the folder's shape; the owner chose that day that the form lives in the
pipeline folder, that its tables' folder is `schema/`, that tables and columns keep fixed
ids beside their names, that only what is set is written, that a missing folder is
recovered from version history rather than replaced by a blank form, and that the
Workbench nodes stop keeping copies of the tables in a pull request after the folder's.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| WB-07 | Planned | P3 | The underwriter app runs on a Databricks App, on AWS or on an internal platform as it runs on Azure Container Apps, each signing underwriters in its own way. |
| WB-08 | Planned | P2 | A merge to `main` takes a workbench project's form, pipeline and app, tested together in CI, to staging and, after approval, to production on the project's host, Azure Container Apps first. |
| WB-09 | Decision | P2 | An Import on a sheet, or a lookup beside a key cell, runs against a file, a database or an API and fills input cells with what comes back, through the server with no credential in the browser, while pricing stays as pure as it is. |
| WB-10 | Planned | P2 | The quote store runs on the SQL instance the project's IT team provides, migrated by the deploy under an identity of its own, with the release before a candidate proved to work on the schema the candidate leaves. |
| WB-11 | Decision | P2 | An underwriter exports a saved quote from the app in the formats the project needs, naming the release that priced it. |
| WB-12 | Decision | P3 | Analysts keep test quotes and price them again as the pipeline changes, built again after the first version was taken out. |
| WB-13 | Planned | P3 | An underwriter compares a quote's revisions, records how a final quote ended, and archives or reassigns many quotes at once. |
| WB-25 | Decision | P3 | The underwriter app refuses to trust sign-in headers it was not put behind. |
| WB-28 | Planned | P3 | A condition tests a field against another field, text within text, and a date against another date give or take a number of days. |
| WB-29 | Planned | P3 | A component that takes nothing typed, or a section or a sheet holding only such components, shows by conditions on the price, applied after it so the price never decides what is priced. |
| WB-33 | Planned | P2 | A pricing that outruns its timeout cannot hold the app's executor: pricings run in a bounded pool and the app answers 503 when it is full. |
| WB-35 | Planned | P2 | Every quote action in the app says what it did: a quote made while the page stays is announced, a storeless page offers Reload when the release changed, "Open the newest" opens once, and the Quotes page's keys leave a focused select alone. Late archive/restore completion keeps the current query. |
| WB-36 | Planned | P2 | A number list in a Show when condition never splits a thousands separator into two values, and the app reads quote_details ids from one typed map. |
| WB-37 | Planned | P3 | The column key, the refusal predicate, the 409 detail reader, the empty visibility constants, the half-window width, the collapsible group and the pricing basis each exist once in the workbench and the app. |
| WB-38 | Decision | P3 | Allowed values typed into an older form are either refused by the form schema or canonical; the Allowed field stops calling a shape the server accepts "older". |
| WB-39 | Planned | P1 | The locally served underwriter app rejects foreign Host and forwarded requests before assigning the analyst's identity. |
| WB-40 | Planned | P2 | The form is a folder in the pipeline folder: an index, a file per table and per sheet, each sheet's layout in a file of its own and the sample by table, columns named by name and only what is set written, so a pull request shows what changed and where. |
| WB-41 | Planned | P2 | The Workbench Input and Workbench Output read their tables from the form folder instead of keeping copies, so a schema change shows once in a pull request and no copy can fall out of step. |

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
the migrations of WB-10 keep it able to read and write, and the run fails; after a migration
that names itself the oldest schema the previous release refuses that schema, so the run
fails with the app stopped, naming the copy of the database WB-10 keeps to restore. The
host step is small, and the project's IT team provides what it deploys onto (the Container
App, the registry, the identities and the database): it publishes, rolls the environment to
the release, waits until the release runs and reports it, and creates nothing. Azure Container
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
and the run failed, or, after a migration naming itself the oldest schema, the app stopped,
the run failed and the copy to restore named.

**Dependencies:** WB-10 (the store and its migrations), WB-11 (the export
the app's tests make, once its formats are decided) and WB-12 (the test quotes). The Azure
resources and identities, provided by the project's IT team. Shared ground with
[BUG-24](bugs.md#bug-24--container-deploys-log-in-to-their-registry) (the registry login
before a push); impact's input is the `[safety]` portfolio sample, or the test
quotes without one.

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
insert-if-absent. The current generic `_keep_form` check-then-insert is not a
supported server-engine implementation; SQLite already has its atomic insert. Use
the chosen engine's atomic form or a savepoint that rolls back a narrowly identified
duplicate-key conflict before continuing. Merely catching `IntegrityError` leaves a
PostgreSQL transaction aborted. A reference's canonical form is compared byte for byte
([workbench](../workbench/high-level.md)), so the quote's `reference_key` and its counter's
take that engine's binary collation (Azure SQL's default ignores case and accents), in the
metadata and a migration alike, and the unique index on a quote's identity keeps the quotes
saved alone, their pairs null, apart (Azure SQL does only when the index is filtered to the
pairs set); the store tests against that engine make `cafe` after `café` and two quotes
saved alone. The deploy's migrate step runs `haute app migrate` under an identity that
owns the schema, before the release rolls out, while the app's own identity reads and writes
data only. A release's migrations keep the previous release's reads and writes working (a
column or table added, nullable or with a default, no constraint the previous release's
writes would break, nothing dropped), and each records the oldest Haute that can still use
the database; that is proved by running the previous release's store tests against the
schema the migrations leave, so the previous release can be started again after a failed
rollout; removing a column is a step of its own after the release that stops using it. A
migration after which the previous release cannot read what the next saves, as the third
and the fourth, names itself the oldest schema instead, so the previous release refuses the
database naming the upgrade; the deploy keeps a copy of the database from before such a
migration, and rolling back past it is restoring that copy.

**Acceptance:** The same store tests pass against SQLite and the server engine. Two
concurrent first saves of a new form revision both commit their quotes and keep one
form copy on the chosen engine's service container; a non-duplicate constraint error
still fails the transaction. Forcing the generic branch on SQLite is not evidence of
server transaction semantics. A deploy
migrates IT's instance under the schema's identity before the release starts, and the app's
identity cannot change the schema; the previous release's store tests pass against the
schema a candidate's migrations leave, and the previous release starts on it, save after a
migration naming itself the oldest schema, when it refuses the schema naming the upgrade
and restoring the copy taken before the migration starts it again; no connection
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
later reproduces the template it was first exported with or uses the current one. Then exports of a saved quote's
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
life: two revisions cannot be compared side by side; nothing records whether a final quote was
bound, declined or not taken up, so the lists cannot tell open work from closed; and
archiving or reassigning many quotes is done one at a time.

**Plan:** Decisions first, recorded here before each slice: which outcomes a quote can close
with, and whether a project names its own. Then, each usable on `haute app serve`:
a comparison of two revisions, values and prices side by side, the rows of a many-row table
matched by their keys rather than their places;
outcomes, recorded on a final quote with who and when and listed apart from the open quotes;
and archiving and reassigning a selection of quotes at once, each checked against its
version, a quote moved on since named without undoing the rest. Exports stay WB-11's.
A quote's validity left with `quote_details.valid_until`, taken out on 2026-10-24 as no
project used it; a project that needs it keeps a date of its own.

**Acceptance:** Two revisions of a quote are compared with each changed value and price
named, the rows of a many-row table matched by their keys; an outcome
recorded on a final quote is listed with who recorded it and when, and the quote leaves the
open lists; a selection of quotes is archived or reassigned at once, a quote moved on since
refused by name and the rest changed.

**Dependencies:** The decisions above.

**Evidence:** `src/haute/_quote_store/_book.py::QuoteBook` (the quotes, their changes and
lists, without outcomes); `src/haute/deploy/_validators.py::response_changes` (a
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

### WB-28 — Other values to test against
**Why:** A condition tests a column against values typed into the condition. It cannot
test one field against another (in the commercial-pricing project, `policy_limit` above
`mga_limit`), text within text (an insured whose name contains "Holdings"), or one date
against another (a project completing more than 365 days after it starts,
`project_completion_date` against `project_start_date`).

**Plan:** A condition's value may be another field of the same type instead of values
typed: a one-row table's column, or, in a test of a list's rows, a column of the same
row. A date compared with another date may add or take away a whole number of days.
Text gains contains, does not contain, starts with and ends with, compared as equals
compares text. A test against another field holds only while both are fields that show,
so hiding still only hides more, and reads the other field's value as
it reads its own. A date against today is left out: a saved quote would then show other
sheets, and so be priced otherwise, on another day; a date field on the form stands in for
it.

**Acceptance:** A section shown while `policy_limit` is above `mga_limit`, one while the
insured contains "Holdings", and one while the completion date is more than 365 days after
the start date, each failing while either field hides; the server and the view are held to
new cases in the shared visibility fixture.

**Dependencies:** None.

**Evidence:** `src/haute/_workbench_visibility.py::_passes` (each test against the
condition's own values); `src/haute/_workbench_form.py::Condition` (`values`, typed text
alone).

### WB-29 — Conditions on the price
**Why:** Conditions test input tables alone; one naming an output column is a problem
("which is in an output table"). In the commercial-pricing project the referral reasons
(`product_premium.referral_reasons`) matter only while `premium.referral_count` is above 0,
and a warning on rate adequacy only while `rate_adequacy` is below 1.

**Plan:** A condition may test an output column, but only in the Show when of what takes
nothing typed: a Heading, a Chart, a Collection or a Table of output columns alone, a
field of an output column, or a section or a sheet holding only those. Hiding it never changes the quote, so the price never
decides what is priced: the rounds decide what shows from the inputs, these conditions
holding, and these conditions are then applied to the price the sheets show, the last one
while a new one is on its way, and before the first every output read as empty. The
server's `visibility` leaves them out, as they never change `hidden`. An input component
placed later in a section with such a condition, or such a condition on what takes
values, is named as a problem, not refused. An output table with many rows is
tested as a list is.

**Acceptance:** A Table of referral reasons, and a section holding it, each shown while
`referral_count` is above 0, hidden before the first price and after a price with none, in
the sheets, Preview and the app; an output condition on a section holding an input
Collection, or on an input Collection, named as a problem; the quote the same whatever the output conditions say, held by cases on both
sides.

**Dependencies:** None.

**Evidence:** `frontend/src/utils/sheetVisibility.ts::conditionFault` (a column of an output
table named as a fault); `src/haute/_workbench_visibility.py::_tested_column` (input tables
alone); `src/haute/_workbench_tables.py::sample_quote` (the hidden columns the quote leaves
out).

### WB-33 — Pricings that outrun the timeout
**Why:** The underwriter app prices a quote in the default executor under
`asyncio.wait_for(..., PRICE_TIMEOUT_SECONDS)`; a pricing that outruns the 60 s is left
running with no cancellation, so a pipeline slow on some quotes accumulates threads, and
once the executor's workers (`min(32, cpus + 4)`) are all held every later pricing queues
behind them and times out too, with no recovery until the stuck ones finish. This
package comes from the [25 October 2026 codebase review](codebase-review-2026-10-25.md).

**Plan:** Price in a bounded executor sized for the app (or in the batch worker with a
cancellable timeout), count the pricings in flight and answer 503 with a retry hint when
the bound is reached.

**Acceptance:** With the bound held by sleeping pricings, a further price request is
answered 503 at once rather than timing out; when one finishes, the next is served.

**Dependencies:** None.

**Evidence:** `src/haute/_workbench_app.py::PRICE_TIMEOUT_SECONDS`.

### WB-35 — Every quote action says what it did
**Why:** Duplicate and Renew from a quote with unsaved values create the new quote on the
server, close the window and only then ask the page's leave guard; `navigate` is called
without `force` and its answer is dropped, so on Cancel the quote exists and nothing on
the page says so (only the Save-as-new branch toasts). The storeless app (`OneQuote`)
reads the preview store's error alone, so a release change arrives as "Pricing failed:
This page was drawn for another release ... reload" with no Reload control; the Reload
notice is rendered only by the store-backed page. "Open the newest" calls `openQuote`
and then `navigate`, whose route effect calls `openQuote` again. The Quotes page gates
`/` and `N` with `isTypingTarget`, so with the Order or Assigned-to select focused `N`
opens the New quote window instead of the select's type-ahead; the pipeline editor and
the Workbench view use `isFormControl` for such keys. From the [25 October 2026 codebase
review](codebase-review-2026-10-25.md) (FWB-1, FWB-6, FWB-8, FWB-9).

**Plan:** Ask the leave guard before Create for Duplicate and Renew, or toast "Made
<label>" whenever the page does not move to the quote; state the guard case in the
workbench specification's `send` description. Render the same Reload notice in
`OneQuote` when `releaseChanged`. Make `navigate` report whether the path changed and
call `openQuote` only when it did not. Guard the Quotes page's keys with
`isFormControl`.

**Acceptance:** Tests: Duplicate from a dirty quote followed by Cancel at the guard
shows the toast naming the quote made (or the guard runs before creation and no quote is
made); the storeless page shows the Reload notice on a 409 release mismatch; "Open the
newest" reads the quote once; `N` with a select focused opens no window.

**Dependencies:** None.

**Evidence:** `frontend/src/app/useNewQuoteStore.ts::send`, `frontend/src/app/route.ts::navigate`,
`frontend/src/app/UnderwriterApp.tsx::OneQuote`, `frontend/src/app/QuoteView.tsx`,
`frontend/src/app/QuotesPage.tsx`, `frontend/src/hooks/keyboardTargets.ts::isFormControl`.

**28 October full-pass addition:** Archive/restore completion calls `load(query)` using the
query captured before its await. A real QuotesPage archive witness switches from Team to Mine
while the request waits; completion starts a newer load for Team and replaces Mine.
**Plan/acceptance:** reconcile the mutation against the current list generation/query, or
reload the current query. A delayed archive or restore must not replace newer
search/filter/sort/navigation state; test with the actual page/store and deferred API response.
The archive case was executed; restore shares the same source pattern at `frontend/src/app/QuotesPage.tsx::QuotesPage.restore`.

### WB-36 — Number lists in Show when, and the quote_details ids from one map
**Why:** "is one of" and "is not one of" on a number column without allowed values split
the typed list on every comma, so a thousands-separated "1,000" becomes the values "1"
and "000"; `parseTypedNumber` strips separators, so both pass `valueFits`, no fault
shows, and the condition silently tests 1 and 0. The single-value field of the same
column reads "1,000" correctly, and the specification shares the gap ("values typed
comma-separated"). Separately, the app repeats Haute's quote_details column ids and
names as string literals (`qd_is_renewal`, `qd_quote_name`, `qd_reference`, the listed
columns) instead of reading `QUOTE_DETAILS_COLUMNS`, so a rename on the server passes
`tsc` and the parity tests while the renewal tick, the name cell, the reference and the
listed columns silently miss. From the [25 October 2026 codebase
review](codebase-review-2026-10-25.md) (FWB-2, FWB-3).

**Plan:** For Integer and Decimal columns take one value per field, or refuse a comma
inside a number with a fault; keep the comma split for text; update both specification
sentences. Export a typed id map from `workbenchForm.ts` (`QUOTE_DETAILS.is_renewal` and
the rest) and import it at the four sites, or extend the parity test to assert each
literal is a member of the table.

**Acceptance:** Tests: "1,000" typed into an is-one-of on an Integer column is the one
value 1000 or shows a fault, never two values; a renamed quote_details id fails `tsc` or
the parity test.

**Dependencies:** None.

**Evidence:** `frontend/src/workbench/ShowWhenEditor.tsx`,
`frontend/src/utils/sheetValues.ts::parseTypedNumber`,
`frontend/src/utils/sheetVisibility.ts::valueFits`,
`frontend/src/utils/workbenchForm.ts::QUOTE_DETAILS_COLUMNS`, `frontend/src/app/quoteValues.ts`,
`frontend/src/app/QuoteView.tsx`, `frontend/src/app/useNewQuoteStore.ts`,
`frontend/src/utils/quoteIdentity.ts`; `specs/workbench/high-level.md` and
`specs/workbench/low-level.md` (the comma-separated sentences).

### WB-37 — One copy of each workbench helper
**Why:** The `tableId:columnId` key that looks, Order rows, the Columns list, the
condition select, the draft's places and the window's fields are looked up by is
hand-spelled in six places while `columnKey` is exported and already imported by two
others; the format is load-bearing, so a change to it breaks lookups silently. The
`refused` predicate (status under 500) is defined twice verbatim; the 409 detail is
unwrapped by hand in two stores while `api/errors.ts` has an unexported reader; the
empty visibility and locked constants, the half-window panel width, a collapsible titled
group and the pricing basis expression (`basisOf` exists but two stores inline it) are
each written two or three times; `adopted` is a module-level mutable in a component
file. From the [25 October 2026 codebase review](codebase-review-2026-10-25.md) (FWB-4,
FWB-5, FAP-14).

**Plan:** Import `columnKey` everywhere and delete the private copies. Export
`apiErrorRefused` and `apiErrorRawDetail` from `api/errors.ts` and use them in both
stores. Export the empty constants from `useSheetValues.ts`. One `halfWindowWidth`
helper, one `Collapsible` in `app/ui.tsx`, `basisOf` called from `useQuoteValues` and the
preview store, `adopted` moved into the open-quote store.

**Acceptance:** One definition of each by grep; the workbench and app tests pass
unchanged.

**Dependencies:** None.

**Evidence:** `frontend/src/utils/sheetVisibility.ts::columnKey`,
`frontend/src/workbench/ShowWhenEditor.tsx`, `frontend/src/workbench/panel/FieldOrder.tsx`,
`frontend/src/workbench/panel/lookParts.tsx`, `frontend/src/workbench/sheet/fieldCells.ts`,
`frontend/src/app/quoteValues.ts::basisOf`, `frontend/src/app/useNewQuoteStore.ts`,
`frontend/src/app/useOpenQuoteStore.ts`, `frontend/src/api/errors.ts::rawDetailRecord`,
`frontend/src/workbench/useSheetValues.ts`, `frontend/src/stores/useWorkbenchPreviewStore.ts`,
`frontend/src/app/NewQuoteWindow.tsx`, `frontend/src/app/HistoryPanel.tsx`,
`frontend/src/app/QuotesPage.tsx`, `frontend/src/workbench/PropertiesPanel.tsx`,
`frontend/src/workbench/SchemaPane.tsx`.

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

### WB-39 — Enforce the local app's request boundary
**Why:** The [28 October review](codebase-review-2026-10-28.md) measured a
local-principal app accepting `Host: attacker.example:8001` for both a release
read and a pricing POST with matching Origin and `Sec-Fetch-Site: same-origin`.
Both returned 200 under the analyst's identity. `haute app serve` binds loopback,
but `create_app` grants its local principal to every request and installs no
trusted-host guard. `_cross_site` exempts reads and accepts a same-origin write.
A hostile hostname resolving to loopback can therefore meet these checks;
DNS-rebinding exposure is inferred from the measured request acceptance, not
claimed as a browser exploit executed during the review. The quote routes use
the same identity middleware, so this boundary also protects saved quotes.

**Plan:** In local-principal mode, install the existing local trusted-host and
forwarded-metadata boundary before authentication. Confirm the app's Origin
and browser-request rules cover its local entry path without trusting arbitrary
hostnames. Keep hosted identity handling separately specified; `WB-25` owns
the Azure authentication-header policy. Document the local app threat boundary
in the workbench and sandbox-security specifications before implementation.

**Acceptance:** Local app requests with a foreign Host, with or without
same-origin fetch metadata, are refused before release/form/quote reads or
pricing and quote mutations. Forwarded metadata is refused in local mode.
Normal supported loopback hosts still load, price and save quotes; read-only
mode remains read-only. Include quote-store mutation assertions against a real
temporary store. Hosted proxy operation remains covered by its existing tests.

**Dependencies:** None; `WB-25` is a separate hosted trust decision.

**Evidence:** `src/haute/_workbench_app.py::create_app`;
`src/haute/_workbench_app.py::_cross_site`;
`src/haute/cli/_app.py::handle_app_serve`;
`src/haute/_local_security.py::LocalTrustedHostMiddleware`;
`tests/test_workbench_app.py`.

### WB-40 — The form as a folder of named files
**Why:** The form is one JSON document at the project root. In commercial-pricing it is
230,549 bytes and 7,893 lines holding 14 tables, 298 columns, five sheets and the sample.
Every field is written, defaults included, so a column takes 21 lines, and the sheets name
columns by random ids (`{"table": "t_517c29a8", "column": "c_fb083d46"}`). A pull request
therefore shows hunks thousands of lines apart that say neither which table nor which sheet
they change, and every save commits as "Updated form.json". Kept outside the pipeline
folder, the form is a special case wherever that folder is the rule: the seed pathspecs,
the release's inputs and the CI triggers each name `forms/`, and commercial-pricing's
deploy workflow, generated before that trigger existed, misses it.

**Plan:** The workbench specification's form folder is the contract. The decisions behind
it are in Scope above.
- The form is the pipeline folder's `workbench/`: `form.json` (`version`, `name`, the
  colours set in `theme`, and the order of `schema` and `sheets`), `schema/<table>.json`
  per table, `sheets/<sheet>.json` and `sheets/<sheet>.layout.json` per sheet (the second
  holding geometry alone), and `sample/<table>.json` per input table with rows.
  `[workbench].form` goes.
- Files are named by the table's name and by a name made from the sheet's title. Inside
  them a column is `table.column`, or its bare name where its table is said. Tables and
  columns keep a fixed id written beside their name, so a rename keeps saved quotes'
  values and a column made again under an old name does not take them. Sheets, sections
  and components take ids that say their kind (`collection_k3f9`).
- Only what is set is written, besides the keys that say what a thing is. Haute's own
  parts (`quote_details`' structure, the identity columns) are never written.
- One new storage module turns the folder into the `FormSpec` the editor, the routes, the
  release and the quote store already hold, and back. Nothing else changes its model.
  Save writes the files that changed and deletes the form's files it no longer has, all
  staged and rolled back together under the pipeline save's lock with the pipeline save's
  staged writes and deletes, and captures every path on the ledger. The folder's revision
  hashes every file's path and bytes.
- A missing folder is refused, pointing to version history. `haute init --workbench` is
  the only thing that makes the folder, and it can add one to a project that already has
  a `haute.toml`.
- Save refuses names the folder cannot hold. `haute app build` and deploy refuse
  references to columns the schema does not hold; the editor opens such a form and shows
  them.
- The scaffold, the seed pathspecs, the CI triggers, the commit messages and the watcher
  follow the folder, and the documentation moves with it. No reader of `forms/form.json`
  remains.
- commercial-pricing's form is converted once, with the owner's agreement, by a script
  that is not shipped. Its tables' and columns' ids are kept, its sheets', sections' and
  components' ids are made anew, and the compiled form is compared before and after.

**Acceptance:**
- commercial-pricing's form, converted, reads back to the same compiled form apart from
  the sheets', sections' and components' ids, and prices its sample as before.
- Changing a column's label writes one file; moving a component writes one layout file;
  renaming a column changes its name, every reference to it and its sample's key, and
  keeps its id; renaming a table moves its schema file and its sample file.
- A read refuses, naming the file and the place: a missing folder, a missing listed file,
  an unlisted file, a file spelled in another case than its listing, a sheet without its
  layout file or the reverse, an identity column written, a rule on an output column, an
  unknown key and an unknown version.
- Save refuses, writing nothing: two tables of one name whatever their case, two columns
  of one name in a table, a name that is not an identifier, a Windows-reserved table or
  sheet name, a sheet title that gives no name or another sheet's, and a path over 240
  characters.
- A write that fails leaves every file as it was; a stale base revision writes nothing.
- `haute app build` and `haute deploy` refuse a form that names a column the schema does
  not hold, naming each place; the editor opens it and shows each one as a problem.
- `haute init --workbench` writes the blank folder in a new project and adds it to an
  existing one, refusing when the table or the folder is already there.
- A save's commit message names the tables and sheets it changed.
- For the same form, the release's `form.json` and `form_revision`, and the quote store's
  forms, are unchanged.

**Dependencies:** None. `WB-41` follows it.

**Evidence:** `src/haute/_workbench_form.py::read_form_document`,
`src/haute/_workbench_form.py::write_form`, `src/haute/_workbench_form.py::render_form`;
`src/haute/_workbench_config.py::DEFAULT_FORM_PATH`;
`src/haute/routes/workbench.py::_save_form`;
`src/haute/routes/_save_pipeline.py::_stage_artifact_write_bytes`,
`src/haute/routes/_save_pipeline.py::capture_save_in_ledger`;
`src/haute/_workbench_app.py::build_release`;
`src/haute/deploy/_validators.py::_workbench_form_errors`;
`src/haute/_git_setup.py::_SEED_PATHSPECS`;
`src/haute/_git_core.py::_generate_commit_message`;
`src/haute/_scaffold.py::_release_input_dirs`;
`src/haute/cli/_init_cmd.py::handle_init`;
`src/haute/server.py::_workbench_form_file`;
`frontend/src/stores/useWorkbenchFormStore.ts`; `frontend/src/utils/workbenchForm.ts`.

### WB-41 — The Workbench nodes read the form folder
**Why:** A Workbench Input and a Workbench Output keep copies of the tables, and the Input
keeps a copy of the sample, in their config files. The editor keeps the copies current and
deploy holds them to the form, so every schema change shows twice in a pull request
(commercial-pricing's copies are 21,167 and 7,959 bytes), and a copy out of step is a
failure that exists only because there is a copy. The copy's rationale, that runs, tests,
generated code and deploy never read the form, stops holding once the form is in the
pipeline folder (`WB-40`): deploy already reads the form to check the copies.

**Plan:** Rewrite "A copy, not a reference" in the workbench specification first. The
nodes then take their tables from the form folder through `WB-40`'s reader: the schema
gives the ports and the request's shape, and the sample is the one `sample_quote` makes,
with its visibility and preset rows, never the typed rows read raw. The node configs hold
no tables, the editor's synchronisation of the copies goes, and deploy's comparison
becomes a check that the form reads and fits the pipeline. The folder's files join the
nodes' cache keys and the pipeline's revision, and generated code and the deploy bundle
take the tables from the folder.

**Acceptance:** A schema change shows in the form folder alone. Previews, runs, `haute
deploy` and the release price commercial-pricing's requests as before, hidden fields
still left out of the sample. No copy can fall out of step, and an undo never brings one
back.

**Dependencies:** `WB-40`.

**Evidence:** `src/haute/_workbench_input.py::workbench_input_tables`;
`src/haute/_workbench_tables.py::sample_quote`;
`src/haute/deploy/_validators.py::_workbench_form_errors`;
`frontend/src/hooks/useWorkbenchTables.ts`.
