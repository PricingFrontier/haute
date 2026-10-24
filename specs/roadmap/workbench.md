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

`WB-32` lets the underwriter key a new quote's reference and details in a window as the
quote is made, in place of the reference Haute numbers.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| WB-07 | Planned | P3 | The underwriter app runs on a Databricks App, on AWS or on an internal platform as it runs on Azure Container Apps, each signing underwriters in its own way. |
| WB-08 | Planned | P2 | A merge to `main` takes a workbench project's form, pipeline and app, tested together in CI, to staging and, after approval, to production on the project's host, Azure Container Apps first. |
| WB-09 | Decision | P2 | A Button on a sheet, or a lookup beside a key cell, runs an action the project declares against a database or an API and fills input cells with what comes back, through the server with no credential in the browser, while pricing stays as pure as it is. |
| WB-10 | Planned | P2 | The quote store runs on the SQL instance the project's IT team provides, migrated by the deploy under an identity of its own, with the release before a candidate proved to work on the schema the candidate leaves. |
| WB-11 | Decision | P2 | An underwriter exports a saved quote from the app in the formats the project needs, naming the release that priced it. |
| WB-12 | Decision | P3 | Analysts keep test quotes and price them again as the pipeline changes, built again after the first version was taken out. |
| WB-13 | Planned | P3 | An underwriter compares a quote's revisions, records how a final quote ended, and archives or reassigns many quotes at once. |
| WB-25 | Decision | P3 | The underwriter app refuses to trust sign-in headers it was not put behind. |
| WB-28 | Planned | P3 | A condition tests a field against another field, text within text, and a date against another date give or take a number of days. |
| WB-29 | Planned | P3 | A component that takes nothing typed, or a section or a sheet holding only such components, shows by conditions on the price, applied after it so the price never decides what is priced. |
| WB-32 | Planned | P2 | New quote, Duplicate and Renew open a window where the underwriter keys the quote's reference and details before it is made; a reference already in use is refused. |

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
rollout; removing a column is a step of its own after the release that stops using it. A
migration after which the previous release cannot read what the next saves, as the third
and the fourth, names itself the oldest schema instead, so the previous release refuses the
database naming the upgrade; the deploy keeps a copy of the database from before such a
migration, and rolling back past it is restoring that copy.

**Acceptance:** The same store tests pass against SQLite and the server engine; a deploy
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

### WB-32 — A new quote keyed in a window
**Why:** A quote's reference comes from Haute's own counter: New quote makes the quote at
once, as `/quotes/new` opens, under the next `Q-000123`, holding its columns' defaults. A
quote usually already has a reference the business uses, such as a broker's submission
number or the policy system's quote number, and the underwriter keys the quote's details on
the sheets only after it is made, so a quote can be made, and listed, with nothing yet to say
what it is.

Decisions taken 2026-10-24:

- The underwriter keys the reference, and Haute numbers no more quotes.
- A reference already in use is refused.
- The window asks for every column of `quote_details` the underwriter types.
- Duplicate and Renew open the same window, filled from their source, its reference blank.
- Create alternative is unchanged: the next option under the source's reference, at once.

**Plan:**

- **The window.** It holds the reference and the other columns of `quote_details` the
  underwriter types (`quote_name`, `insured`, `broker`, `inception_date`, `expiry_date`
  and `is_renewal`) in the table's order. Each is drawn as a Collection draws its column:
  its label, help, placeholder, allowed values and format, and the control its type calls
  for. For New quote they start as a new quote's do, with their columns' defaults
  (`newQuoteValues`). Every one is shown and checked whatever the sheets show or hide, and
  whether or not anything else is typed. The reference is always required; a column the analyst made
  required is marked so and must be filled, text holding only spaces counting as empty. A
  tick box is never empty, unticked or ticked, so `is_renewal` made required asks nothing
  more, as on the sheets. **Create** stays off until the reference fits and every value fits
  its column (`cellProblem`). **Cancel** and `Escape` close a window that has sent nothing.
- **Where it opens.** The window opens over the page it is asked for from, and leaves that
  page as it was: New quote (its button and `N`) over the Quotes page, its list as it is;
  Duplicate and Renew over the quote, or the Quotes page whose row they are on; and Save as
  new over the quote whose save was refused, its unsaved values kept on its sheets.
  Cancel leaves that page untouched, unsaved values and all. The window reads what it is
  filled from without installing anything on the sheets. `/quotes/new` is the Quotes page,
  its default list, with the window open; `/quotes/new?from=…&revision=…&as=duplicate` or
  `as=renewal` the Quotes page with the window filled from that revision; Cancel there
  replaces the address with `/`. `as=alternative` makes the alternative at once, as today,
  its operation key held as WB-30 holds it. Back and Forward close a window that has sent
  nothing, and reloading one loses what was typed into it unless a creation was sent.
- **A reference.** The reference is trimmed and kept as keyed. It is 1 to 40 characters,
  holding no `/`, which the app puts before the option (`ABC-1/2`), and no control
  character. Two references are the same when their canonical forms are: the reference
  normalized to Unicode NFC and then case-folded (`str.casefold`), computed in Python by one
  function, so it is the same on every engine and for every letter: `ABC-1` and `abc-1`,
  `STRASSE` and `straße` are one reference, `cafe` and `café` two. The database compares
  canonical forms exactly, byte for byte, never through an engine's case- or
  accent-insensitive collation: SQLite's default, and a binary collation on an engine that
  offers others. Case folding can lengthen a letter to as many as three, so a canonical form
  is held in a column of its own bound, 160, while the keyed reference keeps 40. Each app
  quote holds its reference's canonical form (`haute_quotes.reference_key`), unique with the
  option in place of the index on the keyed reference and option, which is dropped. The
  options counter (`haute_quote_options`) is kept by canonical form. A creation other than an
  alternative inserts its reference's counter row in its own transaction, so two creations
  of one reference in any case, at once, on any engine, make one quote: the second fails
  the primary key and is refused (409, `reference_taken`), unless it is the first's own key
  repeated, which `_or_replayed` answers with the quote that key made. An alternative finds
  its counter by its source's canonical form and takes the next option, as today. The
  fourth migration, not released, gives each app quote and each counter row the canonical
  form of the reference it holds. `_next_reference` and the quotes' counter retire, the
  counter's table left unwritten.
- **Making it.** The creation names the reference (`reference`), which every creation but
  an alternative's must give and an alternative's must not, and carries the window's
  values as the new quote's `quote_details` row. The server refuses (422) a reference
  missing or breaking the rules above, and, for a creation the window makes, a required
  column of `quote_details` left empty, naming it. It refuses (409, `reference_taken`) a
  reference another quote holds, naming that quote's label and id: the reference's option
  1, else its lowest. Required columns are an entry requirement of the window alone: an
  alternative is made at once whatever its source holds, and a save, as now, keeps a quote
  its rules mark, as rules mark cells and never stop a pricing or a save.
- **A creation sent.** As Create is pressed, the whole creation is made once and kept in
  the tab before it is sent: its operation key, form revision, release, reference, values
  (the window's details over a new quote's defaults for New quote, over the carried values
  for Duplicate and Renew, or over the sheets' values for Save as new) and source. While it is unanswered the window takes no typing and does not close.
  Answered, the quote opens at its own address as today, an unpriced draft holding what the
  creation held, and the kept creation is let go. Refused (a status below 500) it is let go
  and the window opens again for typing, saying why: a reference in use with **Open it**, a
  release moved on with a reload. Unanswered, or answered 500 or more, the window says the
  quote may have been made and offers **Try again**, which sends the kept creation exactly
  as it was, and **Close**, which keeps it. A kept creation is offered back the next time
  the app opens in the tab, a reload included, with Try again and **Discard**, before
  anything else is made; Discard lets it go, the quote it may have made then found in the
  lists. Where the tab keeps nothing, the creation is sent all the same, and a reload before
  its answer asks afresh.
- **Duplicate, Renew and Save as new.** Duplicate and Renew fill the window from the source
  revision carried onto the current sheets (`carry`, which leaves out the details the app
  set), its reference blank, a renewal's `is_renewal` ticked. Create makes the quote from
  that revision, as today: its other values carried, any that no longer fit named, the
  window's details over the carried ones. Save as new fills it from the values on the
  sheets, unsaved ones included, and makes the quote from them, not from a new carry.
- **The reference afterwards.** The reference stays a column the app sets: keyed in the
  window once, read only on the sheets afterwards, never changed by a save, a change or a
  page. Quotes made before keep their `Q-000123` references.
- **Search and order.** A search word is matched against references through their
  canonical form: the word made canonical by the same function, the reference part of
  `ABC-1/2` included, against `reference_key`, so it finds a reference however its letters
  are cased or composed; `ABC-1/2` finds that option alone, as today.
  Names, insured and broker are searched as now. The order the Quotes page calls Newest
  reference becomes **Reference**, A to Z by canonical form, then option, then id, its
  cursor comparing the same, since a keyed reference says nothing of when it was made.
  `Q-000123` references sort among the others as text.
- **Unchanged.** An app without a quote store offers no New quote, as today. Deploy and the
  pricing API are untouched: a request has always named its quote.
- **The specifications.** `specs/workbench/high-level.md`: the creation, the references
  and their comparison, the window and its pages, the order and search, `quote_details`'
  reference keyed once, and the rules rationale (an entry requirement in the window alone).
  `specs/workbench/low-level.md`: the module map; the create request's `reference` and the
  `reference_taken` refusal with the quote holding it; error handling for the new 409 and
  the creation's 422s; the migration; and Testing scenarios. The docs: the Quotes page and
  quote sections describe the window, the order and the search, and Quote details says the
  reference is keyed in the window, its cells read only afterwards.

**Acceptance:**

- **New quote.** New quote opens the window over the list as it was, its Create off until
  the reference fits and each required column is filled: text of spaces alone counts as
  empty, a required tick box asks nothing, and a required column on no sheet is asked for
  all the same. A `quote_details` column with a default starts the window holding it.
  Keying `ABC-1` with an insured and an inception date makes `ABC-1/1`, which opens with
  those details on its sheets, a draft, the default of a column in another one-row input
  table held as a new quote holds it. Cancel makes nothing.
- **A reference in use.** Keying `abc-1` again is refused naming `ABC-1/1`, with Open it,
  as `straße` is once `STRASSE` is made, while `café` and `cafe` are both made. A reference
  of 39 letters and a `ß`, 40 characters, is made though its canonical form is 41.
  Create alternative on `ABC-1/1` makes `ABC-1/2` at once, with no window, even while a
  required column of its source is empty.
- **A reference that breaks the rules.** A reference blank, holding `/` or 41 characters
  long is refused in the window, and refused 422 by the server when sent anyway, as is a
  creation from the window leaving a required column empty.
- **Duplicate, Renew and Save as new.** Duplicate on `ABC-1/1` opens the window over the
  quote, filled from its details, its reference blank, and Cancel leaves the quote and its
  unsaved values as they were. Keying `DEF-2` makes `DEF-2/1` holding the source's values,
  the window's details over them. Renew does the same with `is_renewal` ticked. Save as new
  after a conflict opens the window filled from the sheets, and its quote holds their
  unsaved values. `/quotes/new?from=…&as=duplicate`, reloaded, opens the window filled from
  that revision.
- **At once and after a lost answer.** Two creations of `GHI-3` and `ghi-3` at once, under
  different keys, make one quote and refuse the other 409. One creation sent twice under its
  key makes one quote and answers both with it. A creation whose answer is lost, tried again
  after a reload, sends what it first sent, the window's later typing and the sheets'
  unsaved values as they were then, and makes one quote.
- **Search and order.** A store holding `Q-000001/1`, `Q-000001/2`, `ABC-1/1` and `abc-2/1`
  lists them by Reference across pages, options in order, without repeating or missing
  one, and a search for `ABC` finds both keyed references; `café` keyed composed is found
  by a search for it decomposed.
- **The migration.** A database at the third schema migrates with each app quote's and
  each counter row's canonical reference, and an alternative of `Q-000001` then takes
  option 2.

**Dependencies:** None. It builds on WB-30 and WB-31.

**Evidence:**

- `src/haute/_quote_store/_book.py::QuoteBook._create` and `::QuoteBook._next_reference`
  (each quote but an alternative a reference from Haute's counter), `::QuoteBook._or_replayed`
  (an `IntegrityError` not its own key's repeated) and `::_search_filter`, `::_order`
  (references searched through the database's `lower()`, ordered newest first as text).
- `src/haute/_quote_store/_tables.py::QUOTES` and `::QUOTE_OPTIONS` (the reference compared
  as stored, its case included).
- `frontend/src/app/useOpenQuoteStore.ts::openNew` (the quote made as `/quotes/new` opens,
  the sheets installed first) and `frontend/src/app/quoteValues.ts::heldOperation` (a key
  kept, its creation built again on each attempt).
- `frontend/src/app/route.ts::routeOf` (`/quotes/new`, its source in the query) and
  `frontend/src/app/UnderwriterApp.tsx` (the list or a quote, as the route says).
- `frontend/src/app/QuoteView.tsx` (Duplicate, Renew and Create alternative go to
  `/quotes/new`) and `frontend/src/app/QuotesPage.tsx` (New quote, a row's Duplicate, and
  Newest reference).
- `frontend/src/utils/sheetValues.ts::cellProblem` (a tick box never empty).
- `src/haute/schemas.py::WorkbenchAppQuoteCreateRequest` (no reference).
- `src/haute/_workbench_form.py::QUOTE_DETAILS_MANAGED` (the reference the app's).
- `specs/workbench/high-level.md` (a creation, references from the counter, and rules that
  never stop a pricing).
