# Workbench roadmap

## Scope

The workbench inside Haute is specified in
[the workbench specification](../workbench/high-level.md): the form file and its tables,
the Workbench Input and Workbench Output, the Workbench view with its schema editor, its
sheets, the sample priced live and Preview, and the form on the save ledger. How a deployed
pipeline reads a quote in the workbench's shape is specified in
[the deploy specification](../deploy/high-level.md). The packages below are what neither
covers: the sheets in front of an underwriter, outside Haute, and the delivery of a
workbench project, its form, its pipeline and that app, through CI.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| WB-07 | Decision | P2 | An underwriter keys a quote into the deployed form's sheets, with no Haute and nothing of the builder in sight, and is given the price the pipeline gives. |
| WB-08 | Planned | P2 | A workbench project's generated CI checks the form against the pipeline, deploys the app with the pipeline's version, smokes one quote per request, and runs no step a one-quote pipeline cannot. |

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
