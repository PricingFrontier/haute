# Workbench roadmap

## Scope

The workbench inside Haute is specified in
[the workbench specification](../workbench/high-level.md): the form file and its tables,
the Workbench Input and Workbench Output, the Workbench view with its schema editor, its
sheets, the sample priced live and Preview, and the form on the save ledger. How a deployed
pipeline reads a quote in the workbench's shape is specified in
[the deploy specification](../deploy/high-level.md). The package below is what neither
covers: the sheets in front of an underwriter, outside Haute.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| WB-07 | Decision | P2 | An underwriter keys a quote into the deployed form's sheets, with no Haute and nothing of the builder in sight, and is given the price the pipeline gives. |

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
