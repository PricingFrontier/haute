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

## Scope

In scope:

- Discovering installed extensions through the `haute.extensions` entry-point group when
  the server starts, validating what each declares, and mounting its API router and its
  browser assets.
- `GET /api/extensions`, the list the editor reads.
- The view switcher, the host that loads an extension's browser module into the page, and
  how the pipeline editor steps aside while an extension's view shows.
- The browser contract an extension's module implements: a `mount` function.

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
    the working branch, Save and Commit. Save and Commit act on the pipeline project exactly
    as they do in the pipeline editor; the extension's own files are not part of them.
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
  the same colours, and its palette from the kit's palette column, header and reveal strip.
  It installs the kit as a package and imports the kit's stylesheets into its own shadow
  roots.
- **Until the view mounts.** While the module loads, and when it cannot (front end not
  built, import failed, no `mount` function, `mount` threw), the view shows what is
  happening or what went wrong beside a palette-width column holding the switcher, so there
  is always a way back.

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
