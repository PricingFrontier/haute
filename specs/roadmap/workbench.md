# Workbench roadmap

## Scope

The workbench inside Haute is specified in
[the workbench specification](../workbench/high-level.md): the form file and its tables,
the Workbench Input and Workbench Output, the Workbench view with its schema editor, its
sheets, the sample priced live and Preview, and the form on the save ledger. How a deployed
pipeline reads a quote in the workbench's shape is specified in
[the deploy specification](../deploy/high-level.md). The packages below are what neither
covers: the sheets in front of an underwriter, outside Haute, the delivery of a
workbench project, its form, its pipeline and that app, through CI, and the actions that
fill the sheets from databases and APIs.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| WB-07 | Decision | P2 | An underwriter keys a quote into the deployed form's sheets, with no Haute and nothing of the builder in sight, and is given the price the pipeline gives. |
| WB-08 | Planned | P2 | A workbench project's generated CI checks the form against the pipeline, deploys the app with the pipeline's version, smokes one quote per request, and runs no step a one-quote pipeline cannot. |
| WB-09 | Decision | P2 | A Button on a sheet, or a lookup beside a key cell, runs an action the project declares against a database or an API and fills input cells with what comes back, through the server with no credential in the browser, while pricing stays as pure as it is. |

## Planned improvements

### WB-07 — The underwriter app
**Why:** The sheets reach an underwriter only inside Haute's own server. Preview prices a
quote by patching the pipeline open in the editor with the form's tables and the typed
quote and previewing each Workbench Output port through the dev server's worker pool, so it
needs `haute serve`, the pipeline's source and the editor's graph, and it sits inside the
builder's chrome. Underwriters have no Haute, and the builder (the nodes, the schema editor,
the sample) is not theirs to see. The pricing contract exists: a deployed pipeline answers
the container's `/quote`, or Model Serving's `predict` on Databricks, with a quote in the
workbench's shape, one quote per request, and the response as its tables. Nothing shows the
sheets in front of it, and a deploy carries the Workbench Input's tables, not the form's
pages, so the sheets an underwriter would see are not versioned with the pipeline they
price on.

**Plan:** Decision first. Where the app is hosted varies by project and is not one answer:
beside Model Serving as a Databricks App, where the workspace proxy signs underwriters in
and a service principal calls the endpoint so no token reaches the browser
([hosted-databricks-app](../hosted-databricks-app/high-level.md) holds that trust
boundary); or beside the container image on Azure or AWS, where the app brings its own
sign-in. And what the first increment holds: the sheets and pricing alone, or with the
quote store, roles and referrals an underwriting workflow needs, which Haute's stateless
API cannot hold. Then a deployable of its own, not a view of the editor: the sheet renderer
(the canvas, the Table and the Collection, the page tabs, the value rules and the priced
rows) taking a form, the typed values and a pricer, with no builder chrome; the form's
schema and pages shipped with each deploy, so form and pipeline version together and the
sample stays in the builder; pricing through the deployed pipeline's `/quote`, or a
server-side call to Model Serving, one quote per request.

**Acceptance:** An underwriter with no Haute installed opens the app, sees the deployed
form's sheets and nothing of the builder, keys a quote in and is given the tables the
builder's Preview gives for the same quote on the same pipeline version; a value that
breaks its column's rule is marked before pricing, as Preview marks it; no workspace token
or deploy credential reaches the browser.

**Dependencies:** The hosting and scope decision, recorded here before the first slice. On
Databricks, [hosted-databricks-app](../hosted-databricks-app/high-level.md); elsewhere,
the container target ([deploy](../deploy/high-level.md)).

**Evidence:** `frontend/src/workbench/priceSample.ts::priceSample` (Preview's pricing
through the preview route); `frontend/src/workbench/SheetCanvas.tsx`,
`frontend/src/workbench/WidgetBody.tsx`, `frontend/src/workbench/PageTabs.tsx` (the
renderer, wired to the view's stores); `frontend/src/utils/sheetValues.ts::quoteFilled` and
`::pricedRows`; `src/haute/deploy/_container.py` (`/quote`);
`src/haute/deploy/_model_code.py::_request`; `src/haute/deploy/_utils.py::build_manifest`
(what a deploy carries).

### WB-08 — The workbench project's CI/CD
**Why:** `haute init --workbench` writes the `[workbench]` table and a blank form and
generates the workflows every project gets: Validate (a dry-run deploy scoring the test
quotes), Deploy → Staging, Smoke Test Staging, Impact Analysis, approval, Deploy →
Production. For a workbench project the dry run holds (each test quote is scored as a
request of its own, and the quote with nothing filled in runs through the pipeline), and
the pipeline's deploy holds. The rest does not fit. Nothing checks the form against the
pipeline before a deploy: the pipeline reads the Workbench Input's copy of the tables,
which the editor keeps current, so a form committed with a column the copy does not hold,
or a copy behind the form, deploys without a word. The smoke sends a test-quote file as one
request, which a pipeline reading one quote per request refuses when the file holds more
than one. Impact sends batches of a flat portfolio sample, which such a pipeline refuses
for the batch and could not read for the shape. And the app of WB-07, once it exists, has
no build, no deploy and no check of its own, where form and pipeline are meant to deploy
together so that form v12 pairs with pipeline v12.

**Plan:** With WB-07's host decided, a workbench project's generated workflows gain the
form's check and the app's delivery, and lose what a one-quote pipeline cannot run.
Validate: the form read and checked against the pipeline's copies of the tables, so a form
and pipeline that disagree fail the run before a deploy, naming the column. Deploy: the
pipeline as now, then the app built with the form's schema and pages at that version and
deployed to the host beside the pipeline's endpoint, staging with staging and production
with production (a Databricks App deploy, or an image pushed where the container's is).
Smoke: one request per quote for a pipeline that reads one, through the pipeline's
endpoint and, once the app exists, through it. Impact: not a flat portfolio for a
workbench pipeline but a re-rating of saved quotes, which waits for the quote store; until
then the generated workflow leaves the step out for a workbench project rather than
failing it.

**Acceptance:** A project initialised by `haute init --workbench` runs its generated
workflow to a staging app and endpoint and, after approval, to production, with the form
checked against the pipeline before the deploy, the smoke pricing each test quote as its
own request, and no step a one-quote pipeline cannot run; a form that names a column the
pipeline's copy does not hold fails the Validate job naming the column.

**Dependencies:** WB-07 (the host and the app). The impact step's input is shared ground
with
[BUG-23](bugs.md#bug-23--the-impact-steps-portfolio-sample-is-in-the-repository), and
the starter test quote with
[BUG-20](bugs.md#bug-20--the-starter-test-quote-passes-the-deploy-check).

**Evidence:** `src/haute/_scaffold.py` (the generated workflows);
`src/haute/cli/_init_cmd.py` (`--workbench` writes the table and the blank form and
changes nothing in CI); `src/haute/cli/_smoke.py::_smoke_databricks` and `::_smoke_http`
(a file's quotes as one request); `src/haute/deploy/_impact.py::score_endpoint_batched`;
`src/haute/_workbench_input.py::workbench_request_frames` (one quote per request);
`src/haute/deploy/_validators.py::score_test_quotes` (each case a request of its own).

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
