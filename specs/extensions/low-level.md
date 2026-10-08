# Extensions — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `src/haute/_extensions.py` | Server side of extensions: `EXTENSIONS_GROUP`, `RESERVED_NAMES`, the frozen `Extension` record (with `api_base`, `assets_url` and `entry_url`), `ExtensionError`, `discover_extensions` (loads and validates every entry point in name order), `mount_extensions` (mounts each router and asset directory and registers `GET /api/extensions`), and `extension_package_dirs` (the package directories dev mode's reloader watches, found without importing the extensions). |
| `frontend/src/api/extensions.ts` | `fetchExtensions`: `GET /api/extensions` through the shared request machinery, validated by the generated `extensions` contract, whose validators load with the first response. |
| `frontend/src/stores/useExtensionsStore.ts` | `useExtensionsStore` and `PIPELINE_VIEW`: the installed extensions, the active view (`"pipeline"` or an extension name), and the toolbar element the active extension renders its controls into. `load` fetches the list, leaves the store untouched when none is installed (so the editor never re-renders for it) and reports a failure as one error toast; `showView` rejects a view that is not installed. |
| `frontend/src/extensions/ViewSwitcher.tsx` | The view switcher: "Haute" and one button per extension, the active one `aria-pressed`, on the shared `.toolbar-btn` surface. Full width as a row of labelled buttons, or `compact` as a column of icon buttons for the collapsed palette strip. Renders nothing until the list holds an extension. |
| `frontend/src/extensions/ExtensionView.tsx` | Hosts one extension's view over the area below the toolbar: loads its module, calls `mount` with the view element, the toolbar slot, its API base and `SWITCHER_SLOT`, fills that slot with the switcher once mounted, unmounts on leave, and shows loading or the failure beside a fallback switcher column until the view mounts. |
| `frontend/src/extensions/loadExtensionModule.ts` | The browser contract: `SWITCHER_SLOT`, `ExtensionMountOptions`, `ExtensionHandle`, `ExtensionModule`, `loadExtensionModule` (dynamic import from the listed URL, kept out of Vite's graph, checked for a `mount` function) and `mountExtension` (calls `mount` and checks it returned a handle with `unmount`). |

`src/haute/schemas.py` defines `ExtensionInfo` and `ExtensionsResponse`;
`scripts/generate_api_contracts.py` lists `ExtensionsResponse` as the `extensions` response
group; `frontend/src/api/types.ts` re-exports the generated types.

## Key types and data structures

- **Entry point object.** Whatever `EntryPoint.load()` returns (Obverse uses a module) must
  have `label: str` (non-empty), `assets_dir: pathlib.Path`, `entry: str` (a plain file name:
  no `/`, `\`, or `..`) and a callable `create_router(project_dir: Path) -> fastapi.APIRouter`.
  Haute checks these with `getattr`, so the extension needs no import from Haute.
- **`Extension`** (`src/haute/_extensions.py`): `name`, `label`, `router`, `assets_dir`,
  `entry`. `api_base` is `/api/extensions/<name>`, `assets_url` is `/extensions/<name>` and
  `entry_url` is `/extensions/<name>/<entry>`.
- **`ExtensionInfo`** (`src/haute/schemas.py`): `name`, `label`, `api_base`, `entry_url`,
  `ready` (the entry file exists now) and `detail` (`null` when ready, otherwise what to do).
  **`ExtensionsResponse`**: `extensions: list[ExtensionInfo]`, in name order.
- **Store state** (`frontend/src/stores/useExtensionsStore.ts`): `extensions` (empty until
  a non-empty list loads), `activeView` (`PIPELINE_VIEW` initially), `toolbarSlot`
  (`HTMLElement | null`).
- **Browser contract** (`frontend/src/extensions/loadExtensionModule.ts`): the module exports
  `mount(options: ExtensionMountOptions): ExtensionHandle`, where the options are `main` (the
  element filling the area below the toolbar), `toolbar` (the toolbar's slot element),
  `apiBase` and `switcherSlot` (`SWITCHER_SLOT`, `"haute-view-switcher"`), and the handle has
  `unmount()`. The extension may attach a shadow root to `main` and `toolbar`; the light-DOM
  child Haute places in `main` carries `slot="haute-view-switcher"`.

## Control flow

1. **Import.** After `haute.server` includes its feature routers it calls
   `mount_extensions(app, discover_extensions(Path.cwd()))`, then registers its `/api` and
   `/ws` 404 guards and, when a build is present, the SPA catch-all.
2. **Discovery.** `discover_extensions` reads `importlib.metadata.entry_points(group=...)`,
   rejects duplicate names, then for each entry point in name order validates the name,
   loads the object, checks its attributes, and calls `create_router(project_dir)` once.
3. **Mounting.** For each extension, `mount_extensions` includes the router with prefix
   `api_base` and a `GET <assets_url>/{path}` route that serves a file inside `assets_dir`
   with `Cache-Control: no-cache` (so a rebuilt front end shows on the next page load) and
   answers 404 for anything else. It is a route rather than a `StaticFiles` mount, which
   answers 500 until its directory exists. Then it includes the `GET /api/extensions` route,
   which builds an `ExtensionInfo` per extension, checking the entry file on every request.
4. **Dev serving.** `haute.cli._serve._run_dev_mode` passes the Haute package directory and
   `extension_package_dirs()` as uvicorn's `reload_dirs`. `extension_package_dirs` resolves
   each entry point's top-level module with `importlib.util.find_spec`, so it imports
   nothing; a top-level module that is not a package adds no directory. Vite's dev server
   proxies `/extensions` to the backend.
5. **Editor start.** The editor shell calls `useExtensionsStore.load()` once. A non-empty
   list is stored; an empty one changes nothing; a failure shows an error toast naming the
   cause and the list stays empty.
6. **Switching to an extension.** A switcher button calls `showView(name)`. The shell then
   hides the pipeline region (`invisible` plus `inert`), passes `enabled: false` to
   `useKeyboardShortcuts`, skips its Ctrl/Cmd+Enter handler, gives React Flow `null`
   delete and pan-activation keys, and renders `ExtensionView` (lazy) over that region. A
   subscription to the store's view changes closes the context and connection-drop menus
   when the pipeline view is left. The toolbar renders its brand and a
   slot element it registers with `setToolbarSlot`.
7. **Mounting the view.** Once the toolbar slot exists, `ExtensionView` checks `ready`
   (not ready: show `detail`), imports the module through `loadExtensionModule(entry_url)`,
   and calls `mountExtension` with its own host element. Then it renders
   `<div slot="haute-view-switcher">` holding a `ViewSwitcher` inside the host.
8. **Switching back.** `showView(PIPELINE_VIEW)` unmounts `ExtensionView`, whose effect
   cleanup calls the handle's `unmount()`; the toolbar slot unregisters, and the pipeline
   region becomes visible and interactive with its shortcuts on.

## Edge cases and invariants

- Extension routes are registered before the 404 guards and the SPA catch-all, so `GET`
  routes under `/api/extensions/<name>` and files under `/extensions/<name>/` are reached.
- Extension APIs are under `/api/`, so the session-cookie check applies; assets are not.
- The pipeline editor is never unmounted by a view switch; only its visibility,
  interactivity and keyboard handling change.
- React never reuses an element an extension may have given a shadow root (the toolbar
  slot, the view host) for other content: the toolbar's two forms are keyed by view and
  `ExtensionView` by extension name. A reused element would keep the shadow root and hide
  whatever React rendered into it.
- While an extension's view shows, no Haute window-level key handler acts: the canvas
  shortcuts, Ctrl/Cmd+Enter and React Flow's delete and pan keys are all off. The editor's
  own `isTyping` checks cannot see into an extension's shadow root, which is why the
  handlers are switched off rather than trusted to ignore extension keys.
- The switcher is rendered outside the read-only `inert` node palette, so it stays usable
  while the pipeline is read-only.
- `ExtensionView` calls `mount` at most once per mounted host, always calls `unmount` on
  leave, and never calls `mount` once it has left, even when the module arrives later.
- An extension's module is imported from its listed URL with `/* @vite-ignore */`, so it is
  never part of Haute's bundle or its size budget.
- With no extensions installed nothing renders: no switcher, no toolbar slot, no proxy
  traffic beyond the one listing request.

## Error handling

- `ExtensionError` (a `RuntimeError`) carries the entry point's name and value and the
  reason; an import or `create_router` failure is chained with `from`. It propagates out of
  `import haute.server`.
- The listing route raises nothing for a missing build: `ready` is false and `detail` names
  the missing file and says to build the extension's front end.
- `fetchExtensions` throws `ApiError` or a contract error like any typed request; the store
  turns it into one error toast.
- `showView` throws an `Error` for a view that is not installed: only the switcher calls
  it, with names from the list.
- `loadExtensionModule` throws when the module has no `mount` function; `mountExtension`
  throws when `mount` throws or returns no `unmount`. `ExtensionView` shows either message.

## Testing

- `tests/test_extensions.py` covers discovery from real `EntryPoint` objects pointing at
  test packages (name order, `create_router` called with the project directory), mounting
  (an extension's `GET` route and asset reached in an app registered in `haute.server`'s
  order, a missing file and a path escaping the asset directory answered 404), the listing
  (`ready` and `detail` before and after the entry file exists, an empty list without
  extensions), every `ExtensionError` case, `extension_package_dirs` finding a package
  without importing it, and the real `haute.server` app answering `GET /api/extensions`
  ahead of its 404 guard and only with the session cookie.
- `tests/test_cli_serve.py` checks that dev mode passes extension package directories to
  uvicorn's `reload_dirs`.
- `frontend/src/stores/__tests__/useExtensionsStore.test.ts` covers loading through the
  generated contract, the untouched store for an empty list, the failure toast (also for a
  contract violation), `showView` and the toolbar slot.
- `frontend/src/extensions/__tests__/ViewSwitcher.test.tsx` covers the empty case, the
  buttons and their pressed state, switching, and the compact form.
- `frontend/src/extensions/__tests__/ExtensionView.test.tsx` covers mounting with the
  documented options, the slotted switcher, unmounting on leave, never mounting after a
  late load, waiting for the toolbar slot, an unbuilt extension, and load and mount
  failures keeping the switcher.
- `frontend/src/extensions/__tests__/loadExtensionModule.test.ts` covers importing a module
  by URL and the module and handle checks.
- `frontend/src/hooks/__tests__/useKeyboardShortcuts.test.ts` checks that shortcuts do
  nothing while disabled and act again once enabled, and
  `frontend/src/components/__tests__/Toolbar.test.tsx` checks the toolbar in an
  extension's view and that switching back never reuses the shadow-rooted slot.
- `frontend/src/__tests__/App.integration.test.tsx` and
  `frontend/src/__tests__/App.backgroundJobsIsolation.test.tsx` stub the listing with no
  extensions.
