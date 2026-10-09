# Extensions — High-Level Specification

## Purpose

An extension is a separately installed Python package that adds a view to `haute serve`
beside the pipeline editor. The first is Obverse, the underwriting front end: a pricing
team builds the forms underwriters fill in, in the same browser tab as the pipeline those
forms price against, under one toolbar, switching between the two from the bottom of the
left palette.

Haute names no extension. It finds whatever is installed through a Python entry-point
group, so a project that installs Obverse gets the second view and a project that does not
sees no change at all.

An extension can also supply the quote's tables. Obverse does: the quotes it prices are
keyed in, so its Workbench schema, which defines them, is where the pipeline's tables come
from. The pipeline reads them through a Workbench Input, a node type of its own beside the
Quote Input, and keeps its own copy of them. The extension may also supply a sample quote,
which the pipeline's previews run on: Obverse's is the values typed into its tables while
building.

## Scope

In scope:

- Discovering installed extensions through the `haute.extensions` entry-point group when
  the server starts, validating what each declares, and mounting its API router and its
  browser assets.
- `GET /api/extensions`, the list the editor reads.
- The view switcher, the host that loads an extension's browser module into the page, and
  how the pipeline editor steps aside while an extension's view shows.
- The browser contract an extension's module implements: a `mount` function, and
  optionally a `save` function.
- An extension supplying the quote's tables and a sample quote: the optional `quote_tables`
  and `quote_sample` functions, `GET /api/quote-tables`, and the Workbench Input, the node
  type that holds a copy of both, which the editor keeps current and shows, and previews on.

Out of scope:

- What an extension's view does. Obverse's form builder, where it stores a form and how it
  prices a quote belong to Obverse.
- The toolbar's pipeline controls ([frontend-shared](../frontend-shared/high-level.md)) and
  the canvas, palette and keyboard shortcuts
  ([frontend-graph-canvas](../frontend-graph-canvas/high-level.md)). This component states
  only how they behave while an extension's view shows.
- The Host, Origin and session-cookie checks
  ([sandbox-security](../sandbox-security/high-level.md)), which apply to extension routes
  exactly as to any other.
- Building an extension's front end. The extension ships its own browser module.
- How a deployed pipeline reads a request into its tables ([deploy](../deploy/high-level.md)).

## Behaviour

- **Discovery.** When `haute.server` is imported, every entry point in the
  `haute.extensions` group is loaded, in name order. The entry point's name is the
  extension's name: lower-case letters, digits and hyphens, starting with a letter, and
  never `pipeline`, the editor's own view. The object it loads declares a `label`, an
  `assets_dir`, an `entry` file name inside it, and `create_router(project_dir)`, which
  Haute calls once with the project root (the working directory).
- **Mounting.** The router is mounted at `/api/extensions/<name>`, so its requests pass the
  same Host, Origin and session-cookie checks as every other `/api/` route. `assets_dir` is
  served at `/extensions/<name>/`, outside the session gate like Haute's own built assets.
  Both are registered before Haute's `/api` 404 guard and the single-page-app catch-all, so
  an extension's `GET` routes are reached. In dev mode Vite proxies `/extensions` to the
  backend as it proxies `/api`, and the reloader also watches each extension's package.
- **Listing.** `GET /api/extensions` returns every installed extension with its name,
  label, API base, the URL of its browser module, and whether that module exists yet
  (`ready`), with a `detail` saying what to do when it does not. With nothing installed the
  list is empty and the editor looks exactly as it does without this feature.
- **Switching views.** When the list is not empty, the bottom of the left palette shows the
  view switcher: "Pricing" for the pipeline editor, then one button per extension label, one
  per row at the palette's full width so a long label fits, the current view pressed.
  When the node palette is collapsed the switcher stays, as icon buttons under the reveal
  strip. An extension's palette collapses with it: collapsing either collapses both, so the
  switcher stays put when you switch. It stays usable while the pipeline is read-only:
  switching views changes nothing in the pipeline. Choosing an extension:
  - hides the pipeline editor without unmounting it. It keeps its graph, undo history,
    selection, viewport and live sync, but is invisible and inert, and its keyboard
    shortcuts (React Flow's delete and pan keys, Ctrl/Cmd+Enter and every canvas shortcut)
    are off, so no key pressed in the extension edits the hidden pipeline;
  - closes the canvas context menu and the connection-drop menu;
  - shows the extension's view over the area below the toolbar, beside the Git or Assistant
    panel while one is open;
  - replaces the toolbar's pipeline controls with a space the extension fills with its own
    controls. The brand column stays, and so do the project's controls: Assistant, Help,
    the working branch, Save and Commit. When the extension's module exports `save`, Save
    saves the view's own work to the project through it, with no Git step, and a toast
    says how it went; otherwise Save saves the pipeline. Commit acts on the pipeline
    project exactly as it does in the pipeline editor; the extension's own files are not
    committed (planned as `EXT-01` in the [extensions roadmap](../roadmap/extensions.md)).
- **The extension's view.** Haute imports the extension's module from its URL and calls
  `mount` with the element to render the view in, the space in the toolbar, its API base,
  the name of the slot for the switcher and the palette's state. The extension puts that
  slot at the bottom of its own palette and Haute fills it with the same switcher, compact
  while the palette is collapsed. Where the extension shows no
  palette, as in Obverse's Preview (what underwriters see), there is no switcher: it is for
  the people building the pipeline and the form. Switching back to Haute unmounts the
  extension's view; the extension keeps its own state between mounts.
- **Looking like Haute.** An extension builds its toolbar controls from `haute-ui`, the
  kit Haute's own toolbar is built from ([frontend-shared](../frontend-shared/high-level.md)):
  the same two-row columns, labelled buttons, Undo/Redo, Zoom In/Zoom Out and Save, in
  the same colours, its palette from the kit's palette column, header, items and reveal strip,
  and a panel on its right, such as Obverse's properties panel, from the kit's side panel.
  It installs the kit as a package and imports the kit's stylesheets into its own shadow
  roots.
- **Until the view mounts.** While the module loads, and when it cannot (front end not
  built, import failed, no `mount` function, `mount` threw), the view shows what is
  happening or what went wrong beside a palette-width column holding the switcher, so there
  is always a way back.
- **The quote's tables and the Workbench Input.** The entry-point object may also define
  `quote_tables(project_dir)`, returning tables in the Quote Input's v2 shape
  ([json-shredding](../json-shredding/high-level.md)) as the extension's files on disk define
  them. At most one installed extension may. The listing says which (`quote_tables: true`),
  and `GET /api/quote-tables` returns its tables as they are now, checked against the Quote
  Input's schema rules. Beside it, the object may define `quote_sample(project_dir)`,
  returning one quote as a request holds it (each one-row table an object under its name,
  each many-row table a list of objects under its name) as the extension's files define it
  now; `GET /api/quote-tables` returns it, unchecked, as `sample` beside the tables, an
  empty object when there is none. A pipeline takes them through the Workbench Input, a node type of its
  own: `workbenchInput` in the pipeline document, declared in the pipeline file with
  `@pipeline.workbench_input(config=...)` (a submodel's file uses `@submodel.workbench_input`)
  and configured in `config/workbench_input/<function>.json`. In the editor it is "Workbench
  Input", badged "WORKBENCH IN", in the entry group with the Quote Input's colour and shape
  and an icon of its own. Its config has `tables` and `sample`, copies of the extension's
  tables and sample quote; the type says where they come from, so it names no extension. A workbench is whichever installed extension supplies the quote's tables,
  Obverse being one: the node's name is Haute's, and the extension's own label appears only in
  its panel.
  - **Read as a Quote Input.** A Workbench Input has one port per emitting table, named by the
    table's label, as a Quote Input with the same `tables` has, and a request is read through
    them exactly as through a Quote Input's: by `Pipeline.score`, a deployed pipeline's
    `/quote` and deploy's test quotes, none of which reads the sample. Deploy shares the Quote Input's defect
    [BUG-31](../roadmap/bugs.md#bug-31--a-deployed-quote-input-splits-a-request-into-its-tables):
    a deployed pipeline hands every port the whole request, so a Workbench Input's tables
    reach their ports in deploy only where a Quote Input's would. Haute calls the two the
    request inputs and decides this once, from one set holding both types; tests fail when a
    check in the code names the Quote Input alone where it means a request input. The
    pipeline never needs the extension: the copies are an ordinary `tables` list and
    `sample` object.
  - **No file; the sample.** A Workbench Input reads no file. Without a request, in editor
    runs and previews and in generated code's `run()`, it reads its sample as one request is
    read through its tables: each emitting table is the rows the sample gives it, typed as
    declared, and a table the sample gives no rows, or every table when there is no sample,
    is one row of nulls, for a many-row table too. The sample is read whole, whatever a
    preview asks of it: every emitting table and every selected column, then cut to what is
    asked. What the tables do not read (fields under no table, unselected columns, tables
    that do not emit) is ignored, as a request's is, and a missing or `null` part reads as a
    request's does: as a many-row table's list it gives no rows, and as an object it leaves
    the columns under it null, keeping the row and its other values. Downstream nodes run on
    those rows, so a calculation previews on the sample's values, and a node that cannot take
    a null, such as a rating lookup on a null key, reports it in its preview as it would for
    any null input. The editor builds nothing for a Workbench Input: its tables have no input
    snapshots, a node that reads one of them as its data, such as Explore, reads its rows
    with nothing to cache, build or clear, and the cache report shows its tables together as
    current and read directly. Where deploy reads a Quote Input's sample file, to learn the
    request's schema and to dry-run the pipeline on one row, it derives both from a Workbench
    Input's tables, never its sample: the schema places each selected column where its path
    names, under an object for a one-row table and a list of objects for a many-row one, with
    its declared type, and the dry run reads one request record of nulls in that schema. A
    node that cannot take a null fails that dry run with its own error.
  - **One request input per pipeline.** A pipeline holds at most one Quote Input or Workbench
    Input, counting those inside its submodels. Saving or deploying a pipeline with two is
    refused with "Only one Quote Input or Workbench Input node is allowed per pipeline (found
    2).", deploy refusing before it prunes or builds anything, and `Pipeline.score` refuses to
    score one. While the pipeline has either, the palette greys out the request input it
    offers, titled "Only one Quote Input or Workbench Input allowed per pipeline", and dropping
    or pasting another is refused.
  - **The palette.** While an installed extension supplies tables, the palette shows the
    Workbench Input where the Quote Input was, and otherwise the Quote Input; never both. A
    Workbench Input dragged from it starts with the newest tables and sample fetched, or none
    while the first fetch is running, and gets them when it finishes. Installing or removing the
    extension changes only what the palette offers: a pipeline keeps the request input it
    has, and a Quote Input becomes a Workbench Input, or back, only by deleting it and dragging
    in the other.
  - **Kept current.** The editor fetches the tables, with the sample, when it finds an
    extension that supplies them, each time it adopts a pipeline document (opening one, or reloading it after a branch
    switch or a change on disk), and each time the pipeline view shows after the extension's
    view. Only the newest fetch counts. A fetch brings the Workbench Input of the document it
    was made for up to date, once, through the same update an edit in its panel makes, as one
    undoable step saved with the pipeline. A copy whose tables and sample both match is left
    alone, a missing sample matching an empty one, a read-only document is never changed, and while a submodel is open the update waits until
    the top level shows. Undo and redo restore earlier copies like any other edit, and the
    next fetch brings the copy up to date again. An update that does not commit, as when a
    submodel is opened while it is under way, is tried again when the top level shows. Edits
    the extension has not saved are not part of the tables or the sample. A fetch never changes a Quote
    Input, and a Workbench Input inside a submodel keeps the copy it has.
  - **Connections follow table names.** A connection to a table whose name the new tables
    still have stays, however the tables were reordered or replaced; one to a table whose name
    has gone is removed and reported, as removals are for any request input edit. A renamed
    table is a new port. A Quote Input's connections still follow a renamed table by position.
  - **Its panel** shows the tables read-only, with whatever the editor finds wrong with a
    table's name as a port (not an identifier, a reserved word, a repeat), an "Edit in
    <label>" button that opens the extension's view to edit them, and a note on what previews
    run on: "Previews run on the workbench's sample values; a table with none is one row of
    nulls." while the copy has a sample, and "Previews run on one row of nulls per table until
    the workbench supplies values." while it has none. With no installed
    extension supplying tables it says "No installed extension supplies these tables, so they
    are the last copy and nothing updates them.", and while a submodel is open "The editor
    updates these tables only at the pipeline's top level." A name another node already has is
    refused when the pipeline is saved, as it is for any request input. The Quote Input's panel
    is its table editor, whatever is installed.
  - **The assistant** reads a Workbench Input and connects nodes to its tables, or removes
    such connections, as it does a Quote Input's, but cannot add, change, rename or delete one,
    nor edit its steps: its node card says the tables are the installed workbench's and the
    analyst adds the node from the palette.

  Obverse supplies the input tables of its Workbench schema, in schema order. In each quote a
  one-row table is an object under its own name (table path `$[:]`, columns
  `$[:].<table>.<column>`) and a many-row table is an array of objects under its name (path
  `$[:].<table>[:]`, columns `$[:].<table>[:].<column>`); a request is one quote or a list of
  them. Column types carry over, a column's allowed values become its `levels`, every column
  is selected, every table emits, and a many-row table with exactly one key column uses it as
  its `row_id_column`. Rules such as required and ranges stay in the Workbench. Output tables
  have no part in this, nor does a quote's identity on its many-row tables for requests that
  hold several quotes. Its sample is the values typed into its Tables and Collections while
  building, saved with the form: each input table with values under its name, each value as
  its column's type holds it (a number typed with a currency sign or separators is a number,
  and an unticked box in a filled row is false), rows with nothing typed left out. What an
  underwriter types in Obverse's Preview is not part of it.

## Design rationale

- **Entry points, not imports.** Haute never imports an extension by name. The extension
  depends on Haute's contract rather than Haute on the extension, and installing or
  removing the package is the whole switch. Entry points are Python's standard way of
  saying "if it is installed".
- **Registered at import, before the catch-alls.** `haute serve` runs the module-level
  `haute.server:app` by import string, and Starlette tries routes in order. A router
  included after import would have every `GET` answered by the `/api` 404 guard or by the
  single-page app.
- **A module that mounts into Haute's page, not an iframe.** The toolbar is shared, so the
  extension's controls render inside Haute's toolbar. An iframe cannot do that without
  Haute re-implementing them; a module given elements to render into can. Isolation is the
  extension's job: Obverse renders into shadow roots because two Tailwind stylesheets on
  one page re-order each other's utilities. The switcher reaches the extension's palette
  through a named `<slot>`, so it stays Haute's own component in Haute's styles.
- **The toolbar kit is shared at build time.** The extension renders its controls with its
  own React, in its own shadow root, so Haute cannot lend it components at run time. It
  builds against `haute-ui` instead, the same source Haute's toolbar renders, and runs the
  same way under `haute serve` and on its own.
- **The pipeline editor stays mounted.** It owns live sync, the editor document and undo
  history; unmounting it would reconnect and reload on every switch. `visibility: hidden`
  keeps React Flow's measured size, and `inert` keeps focus and assistive technology out.
  Its window-level shortcuts would still fire, so they are switched off explicitly rather
  than relying on focus.
- **The switcher sits in the palette column, not the toolbar.** The palette is the
  builder's workspace and underwriters never see it, so the switcher never reaches them.
  The toolbar was rejected because Obverse's underwriter view keeps it.
- **Loud failure.** An extension that cannot load stops `haute serve` at startup, naming
  the entry point. One whose front end is not built is listed with `ready: false` and its
  view says how to fix it.
- **A type of its own.** A Quote Input whose tables an extension supplied did a different
  job from one whose tables are built in its panel, yet both read the same in the pipeline
  file and the editor branched on the difference everywhere. The Workbench Input says in the
  pipeline file where its tables come from, and the Quote Input keeps one job.
- **One way of reading a request.** The two types read a request identically, so their
  behaviour is decided from one set rather than copied: a change to how a request is read,
  such as the fix for BUG-31, covers both, and a test reports a check that names the Quote
  Input alone.
- **A copy, not a reference.** The pipeline file is canonical and runs without the GUI, so a
  Workbench Input holds the tables it runs from instead of pointing at the extension's files:
  runs, tests, generated code and deploy never need the extension, and the editor, which has
  both, keeps the copy current.
- **The workbench's sample, not a file.** Quotes keyed in through a workbench leave no sample
  file, and the workbench is where the quote's tables are laid out, so it is where a pricing
  analyst types the quote previews run on. Copied into the config like the tables, it lets
  the pipeline file run and preview without the extension; read as a request is, it gives
  the rows a request would; read whole, every reader finds the same misfit, and resolving
  points, which reads only the tables, never does. A table it gives no rows is one row of
  nulls rather than none, so every node's expressions still run on a row and its preview
  shows one. Deploy takes the request's shape from the tables, as a deployed pipeline never
  sees the sample.
- **Connections follow names.** An extension may reorder or replace its tables wholesale, so
  a table's position says nothing about which table it is, and matching by position would
  quietly reconnect a downstream input to another table. A table's name is also its key in
  the request.
- **Fetched for a document.** A fetch belongs to the document it was made for, and fetches can
  settle out of order, so only the newest publishes and none applies to a document adopted
  after it started, including through an update still waiting on identity resolution. Graph
  edits, undo and redo never start one, so the refresh never fights the user's history.

## Interactions

- [server-api](../server-api/high-level.md): `haute.server` mounts extensions between its
  feature routers and its 404 guards, and owns the response models.
- [cli](../cli/high-level.md): dev mode's reloader watches each extension's package too.
- [sandbox-security](../sandbox-security/high-level.md): the local Host, Origin and session
  middleware gate extension API routes.
- [frontend-shared](../frontend-shared/high-level.md): the toolbar gives its pipeline
  controls' space to the extension, the extension builds its controls from `haute-ui`,
  and the API client validates `GET /api/extensions` with the generated contract.
- [frontend-graph-canvas](../frontend-graph-canvas/high-level.md): the editor shell renders
  the switcher under the palette, hides the pipeline editor and turns its shortcuts off.
- [build-and-distribution](../build-and-distribution/high-level.md): the Vite dev server
  proxies `/extensions`.
- [engineering-quality](../engineering-quality/high-level.md): the generated contract
  bundle carries the `extensions` response group.
- [json-shredding](../json-shredding/high-level.md): the v2 tables an extension supplies,
  checked by the Quote Input's schema rules and read as a Quote Input's are, and its sample
  read through them in memory.
- [frontend-node-editors](../frontend-node-editors/high-level.md): the palette's Workbench
  Input and its panel.
- [frontend-graph-canvas](../frontend-graph-canvas/high-level.md): a palette drop reports the
  node it creates, the commit controller checks an update's `isCurrent`, and the request
  inputs share one singleton slot.
- [caching](../caching/high-level.md): a Workbench Input's `tables` and `sample` are
  classified as node config, and its tables have no input snapshots.
- [pipeline-config](../pipeline-config/high-level.md): the `workbench_input` decorator and its
  config folder.
- [server-api](../server-api/high-level.md) and [deploy](../deploy/high-level.md): save and
  deploy refuse a second request input, and deploy takes a Workbench Input's request schema
  and sample record from its tables.
- [assistant](../assistant/high-level.md): the Workbench Input's node card and the operations
  that refuse to author one.

## Failure model

- An entry point with an invalid or reserved name, two installed distributions declaring
  the same name, an import error, a missing or mistyped attribute, an `entry` that is not a
  plain file name, and a `create_router` that raises or returns something other than an
  `APIRouter` each raise `ExtensionError` naming the entry point while `haute.server` is
  imported. `haute serve` does not start; in dev mode the reloader waits for a fix.
- A missing `assets_dir` or entry file is not a startup error: the listing reports
  `ready: false` with a `detail`, `/extensions/<name>/...` answers 404, and the view shows
  the detail.
- A failed listing request shows an error toast and no switcher; the pipeline editor is
  unaffected.
- A module without a `mount` function, or a `mount` that throws, shows the error in the
  view beside the switcher.
- Errors inside an extension's routes are the extension's. Haute's exception handlers and
  request-ID middleware treat them as they treat any route.
- Two installed extensions defining `quote_tables`, or one defining it as something other
  than a function, raise `ExtensionError` naming them while `haute.server` is imported.
- `GET /api/quote-tables` answers 404 when no extension supplies tables, 500 naming the
  extension when its `quote_tables` raises or returns something other than a list, and the
  structured 422 that Infer Tables answers when the tables break the Quote Input's schema
  rules. A failed fetch shows one error toast and changes no Workbench Input; a fetch made for an
  earlier document never changes the current one, and an update it started that is still
  waiting on identity resolution is dropped with the commit controller's usual message.
- A Workbench Input with no installed extension supplying tables keeps its copy and runs
  from it; its panel says so and nothing updates it.
- A Workbench Input with no tables has no ports. Previewing it fails with "This Workbench
  Input has no tables: add input tables to the workbench's schema and save it.", and
  deploying it fails because its request has no schema. Deploy also fails, naming the field,
  when two of its columns' paths disagree about a request field.
- A `quote_sample` that is not a function, or one on an extension without `quote_tables`,
  raises `ExtensionError` naming the entry point while `haute.server` is imported. One that
  raises, or returns something other than an object, makes `GET /api/quote-tables` answer
  500 naming the extension. The sample is not checked when served, so a bad one never stops
  the tables updating.
- A sample that does not fit its Workbench Input's tables fails whatever reads its rows
  (previews, `run()`, leasing a table point), and planning a preview that sizes the tables,
  with "The workbench's sample does not fit this Workbench Input's tables: <reason>. Correct
  it in the workbench and save it." It does not fit when a value is not of its column's
  declared type, when something other than a list stands where a many-row table's rows
  belong, when something other than an object stands where a one-row table's object, or any
  object a column's path passes through, belongs, or when a many-row table's list holds
  anything but rows (a value, `null` or a list). Resolving a table point and the cache report
  read the tables alone, so a misfit sample never stops them.
- Two request inputs of either type, at the top level or in a submodel, fail save and deploy
  with "Only one Quote Input or Workbench Input node is allowed per pipeline (found 2)." and
  `Pipeline.score` as two Quote Inputs do. A config key a Workbench Input does not declare,
  `path` among them, is refused as it is for any node type.
